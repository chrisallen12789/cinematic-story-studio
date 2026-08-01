import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import {
  lstat,
  mkdir,
  readFile,
  realpath,
  writeFile,
} from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { repositoryRoot as defaultRepositoryRoot } from "../lib/paths.mjs";

export const BUILD_EVIDENCE_ENVIRONMENT = Object.freeze({
  workflowHeadSha: "CSS_BUILD_WORKFLOW_HEAD_SHA",
  testedCheckoutSha: "CSS_BUILD_TESTED_CHECKOUT_SHA",
  pullRequestHeadSha: "CSS_BUILD_PR_HEAD_SHA",
  timestamp: "CSS_BUILD_EVIDENCE_TIMESTAMP",
  executablePath: "CSS_PACKAGED_E2E_EXECUTABLE",
  screenshotPath: "CSS_PACKAGED_E2E_EVIDENCE_PATH",
  resultPath: "CSS_PACKAGED_E2E_RESULT_PATH",
  phase3ResultPath: "CSS_PHASE3_PACKAGED_E2E_RESULT_PATH",
  phase3VoiceCastingEvidencePath:
    "CSS_PHASE3_VOICE_CASTING_EVIDENCE_PATH",
  phase3bResultPath: "CSS_PHASE3B_PACKAGED_E2E_RESULT_PATH",
  stepOutcome: "CSS_PACKAGED_E2E_STEP_OUTCOME",
  runnerName: "CSS_BUILD_EVIDENCE_RUNNER_NAME",
  runnerOs: "CSS_BUILD_EVIDENCE_RUNNER_OS",
  runnerArchitecture: "CSS_BUILD_EVIDENCE_RUNNER_ARCH",
  runnerEnvironment: "CSS_BUILD_EVIDENCE_RUNNER_ENVIRONMENT",
  runId: "CSS_BUILD_EVIDENCE_RUN_ID",
  runAttempt: "CSS_BUILD_EVIDENCE_RUN_ATTEMPT",
  workflow: "CSS_BUILD_EVIDENCE_WORKFLOW",
  job: "CSS_BUILD_EVIDENCE_JOB",
});

const APP_EXECUTABLE_NAME = "Cinematic Story Studio.exe";
const SERVICE_EXECUTABLE_NAME = "cinematic-story-service.exe";
const MAX_HARNESS_RESULT_BYTES = 1024 * 1024;
const MAX_SECURITY_INPUT_BYTES = 5 * 1024 * 1024;
const BUILD_EVIDENCE_SCHEMA_VERSION = "6.0.0";
const PACKAGED_E2E_RESULT_SCHEMA_VERSION = "4.0.0";
const PHASE_3_PACKAGED_E2E_RESULT_SCHEMA_VERSION = "5.0.0";
const PHASE_3B_PACKAGED_E2E_RESULT_SCHEMA_VERSION = "7.0.0";
const PHASE_3B_EVIDENCE_CLASSIFICATION =
  "deterministic_fixture_lifecycle_only";
const PHASE_3B_ASSERTION_KEYS = Object.freeze([
  "phase0ThroughPhase3aPrerequisitesCurrent",
  "fixtureProviderClearlyClassified",
  "modelVerifiedAndActivated",
  "pronunciationDecisionApproved",
  "narratorAndTwoCharacterAuditionsGenerated",
  "authenticatedWavLoadsPassed",
  "verifiedCacheHitProven",
  "targetedInvalidationProven",
  "fiveGateTypesApproved",
  "restartPersistenceProven",
  "runtimeNetworkPolicyAndOwnedPidEndpointObservationProven",
  "electronServiceAndProviderWorkerOwnershipProven",
  "allExactOwnedProcessesExited",
  "noUnrelatedProcessInspectedOrTerminated",
]);
const PHASE_3B_GATE_IDS = Object.freeze([
  "per_role_audition_review",
  "narrator_audition_review",
  "character_audition_review",
  "pronunciation_review",
  "voice_readiness_review",
]);
const VOICE_CASTING_CONTRACT_VERSION = "3.0.0";
const GOVERNED_VOICE_CASTING_PROFILE_ID =
  "governed-voice-casting-v1@1.0.0";
const GOVERNED_VOICE_CASTING_PROFILE_FINGERPRINT =
  "3eaa6b4d1333b49e55707b1e9aa20606f262e1315a043bff2912a0fe77f97fa6";
const VOICE_CASTING_PRODUCER_ID =
  "voice-casting-orchestrator@1.0.0";
const VOICE_RIGHTS_POLICY_ID = "voice-rights-policy-v1";
const MAX_CASTING_CORRECTIONS_PER_RUN = 200;
const SYNTHETIC_VOICE_CATALOG_REVISION_ID =
  "synthetic-voice-catalog-v1@1.0.0";
const SYNTHETIC_VOICE_CATALOG_FINGERPRINT =
  "68d116d1f66e4ea4bcceabfd0520fd889cf9da3074ee1b9186c43c285575c25f";
const ANALYSIS_CONTRACT_VERSION = "2.0.0";
const WHOLE_BOOK_ANALYSIS_PROFILE_ID = "whole-book-intelligence-v1";
const WHOLE_BOOK_ANALYSIS_PROFILE_VERSION = "1.0.0";
const WHOLE_BOOK_ANALYSIS_PRODUCER_ID =
  "whole-book-analysis-orchestrator";
const WHOLE_BOOK_ANALYSIS_PRODUCER_VERSION = "1.0.0";
const WHOLE_BOOK_ANALYSIS_PROFILE_CANONICAL_JSON =
  '{"agentVersions":[{"agentId":"story-structure","version":"1.0.0"},{"agentId":"story-beats","version":"1.0.0"},{"agentId":"character-identity","version":"1.0.0"},{"agentId":"dialogue-attribution","version":"1.0.0"},{"agentId":"point-of-view","version":"1.0.0"},{"agentId":"story-setting","version":"1.0.0"},{"agentId":"story-timeline","version":"1.0.0"},{"agentId":"character-relationships","version":"1.0.0"},{"agentId":"emotion-dramatic-intent","version":"1.0.0"},{"agentId":"story-continuity","version":"1.0.0"},{"agentId":"analysis-synthesis","version":"1.0.0"}],"analysisContractVersion":"2.0.0","confidenceClassification":{"high":{"maximumInclusive":1,"minimumInclusive":0.85},"low":{"maximumExclusive":0.75,"minimumExclusive":0},"medium":{"maximumExclusive":0.85,"minimumInclusive":0.75},"unknown":{"score":0}},"deterministic":true,"limits":{"defaultPageSize":50,"maximumAgentEnvelopeBytes":32768,"maximumAnalysisEntities":250000,"maximumAnalysisWords":150000,"maximumAttributionCandidatesPerLine":8,"maximumCheckpointBytes":67108864,"maximumEvidenceExcerptCodePoints":512,"maximumEvidenceSpansPerClaim":16,"maximumExactTextCodePoints":16384,"maximumPageSize":200,"maximumSnapshotStages":5,"maximumWarningsPerEntity":32},"offsetUnit":"unicode-code-point","producer":{"producerId":"whole-book-analysis-orchestrator","producerVersion":"1.0.0"},"profileId":"whole-book-intelligence-v1","semanticVersion":"1.0.0"}';
const WHOLE_BOOK_ANALYSIS_PROFILE_FINGERPRINT =
  "6ae73e83e89fbcfc0261ff339950407913cd990093fa13cdcc83ce3b1da810ec";
const PHASE_2_CORRECTION_REASON_FINGERPRINTS = Object.freeze({
  character_identity:
    "b42b6091d0bde37b4dd15f99a321e86dd965f2272a4ca68da6703b6f5ba2f0da",
  dialogue_speaker:
    "6db94e679f663d3dfbed36c173eb8445d6a364beb114c7f8c070e30983247300",
  continuity_disposition:
    "d7497ae908f34096c6bbbfdcbf7ea8287967882880cd229af078eee2383c2554",
});
const PHASE_2_RUNTIME_AGENTS = Object.freeze([
  Object.freeze({ agentId: "story-structure", agentVersion: "1.0.0" }),
  Object.freeze({ agentId: "story-beats", agentVersion: "1.0.0" }),
  Object.freeze({ agentId: "character-identity", agentVersion: "1.0.0" }),
  Object.freeze({ agentId: "dialogue-attribution", agentVersion: "1.0.0" }),
  Object.freeze({ agentId: "point-of-view", agentVersion: "1.0.0" }),
  Object.freeze({ agentId: "story-setting", agentVersion: "1.0.0" }),
  Object.freeze({ agentId: "story-timeline", agentVersion: "1.0.0" }),
  Object.freeze({ agentId: "character-relationships", agentVersion: "1.0.0" }),
  Object.freeze({ agentId: "emotion-dramatic-intent", agentVersion: "1.0.0" }),
  Object.freeze({ agentId: "story-continuity", agentVersion: "1.0.0" }),
  Object.freeze({ agentId: "analysis-synthesis", agentVersion: "1.0.0" }),
]);
const PHASE_2_GATE_IDS = Object.freeze([
  "story_structure_review",
  "character_registry_review",
  "dialogue_attribution_review",
  "whole_book_analysis_review",
]);
const PHASE_2_JOB_STAGES = Object.freeze([
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
  "publish_analysis",
]);
const PHASE_2_PACKAGED_FLOW = Object.freeze([
  "create",
  "import_synthetic_docx",
  "wait_for_extraction",
  "review_import",
  "approve_import",
  "analyze",
  "correct_speaker",
  "start_whole_book_analysis",
  "observe_analysis_stages",
  "inspect_structure",
  "inspect_character_registry",
  "correct_character_identity",
  "inspect_dialogue_and_narration",
  "correct_dialogue_speaker",
  "inspect_whole_book_intelligence",
  "disposition_continuity",
  "approve_story_structure_review",
  "approve_character_registry_review",
  "approve_dialogue_attribution_review",
  "approve_whole_book_analysis_review",
  "close",
  "restart",
  "restore",
  "verify_import_review_persistence",
  "verify_story_analysis_persistence",
  "close",
]);
const PHASE_3_PACKAGED_FLOW = Object.freeze([
  "create_project",
  "import_synthetic_docx",
  "wait_for_extraction",
  "approve_import_review",
  "complete_phase_2_analysis",
  "verify_four_phase_2_approvals",
  "open_casting_workspace",
  "load_synthetic_voice_catalog",
  "create_production_roles",
  "run_casting_analysis",
  "inspect_narrator_candidates",
  "select_narrator_voice",
  "lock_narrator_assignment",
  "inspect_character_candidates",
  "select_first_character_voice",
  "lock_first_character_assignment",
  "select_second_character_voice",
  "lock_second_character_assignment",
  "surface_metadata_differentiation_conflict",
  "disposition_casting_conflict",
  "surface_restricted_or_ineligible_rights",
  "reject_ineligible_final_approval",
  "approve_narrator_casting_review",
  "approve_character_casting_review",
  "approve_complete_cast_review",
  "close_application",
  "verify_first_shutdown_owned_process_exit",
  "restart_same_application",
  "restore_phase_0_through_phase_2_evidence",
  "restore_phase_3a_casting_evidence",
  "close_restarted_application",
  "verify_final_owned_process_exit",
]);
const PHASE_3_ASSERTION_KEYS = Object.freeze([
  "phase2PrerequisitesCurrent",
  "castingProfilePinned",
  "catalogFingerprintVerified",
  "rolesCreated",
  "boundedCandidatesCreated",
  "metadataConflictProven",
  "rightsGovernanceProven",
  "humanAssignmentsLocked",
  "threeGateDecisionsPersisted",
  "restartPersistenceProven",
  "processOwnershipExitProven",
]);
const PHASE_3_NARRATOR_ROLE_TYPES = Object.freeze([
  "primary_narrator",
  "secondary_narrator",
]);
const PHASE_3_CHARACTER_ROLE_TYPES = Object.freeze([
  "named_character",
  "unresolved_speaker",
  "group_or_crowd",
  "quoted_document_or_announcement",
  "internal_thought",
  "custom",
]);
const PHASE_3_APPROVED_ASSIGNMENT_RIGHTS_STATES = Object.freeze([
  "verified",
  "restricted",
]);
const SECURE_INGEST_DEPENDENCIES = Object.freeze([
  Object.freeze({
    name: "lxml",
    version: "6.1.1",
    license: "BSD-3-Clause",
    purpose: "Strict XML parsing for validated DOCX and EPUB package parts.",
  }),
  Object.freeze({
    name: "pypdf",
    version: "6.14.2",
    license: "BSD-3-Clause",
    purpose: "Bounded page-aware text extraction from text-based PDF files.",
  }),
]);
const SECURE_INGEST_BOUNDARY_LIMITS = Object.freeze({
  sourceBytes: 100 * 1024 * 1024,
  previewCodePoints: 8_000,
});
const PARSER_LIMITS_PROFILE_CANONICAL_JSON =
  '{"archiveExpandedBytes":209715200,"archiveMemberBytes":33554432,"archiveMemberNameCodePoints":512,"archiveMembers":2048,"archivePathDepth":20,"canonicalTextCodePoints":10000000,"extractedSections":10000,"ingestContractVersion":"1.0.0","maximumCompressionRatio":100.0,"parserDeadlineMs":30000,"parserProcessMemoryBytes":805306368,"pdfPages":2000,"profileId":"secure-ingest-v1"}';
const PARSER_LIMITS_PROFILE = Object.freeze(
  JSON.parse(PARSER_LIMITS_PROFILE_CANONICAL_JSON),
);
const PARSER_LIMITS_FINGERPRINT = sha256Bytes(
  Buffer.from(PARSER_LIMITS_PROFILE_CANONICAL_JSON, "utf8"),
);
const PACKAGED_FAILURE_STAGES = new Set([
  "prelaunch_inventory_1",
  "isolation_setup",
  "launch_1",
  "root_ownership_1",
  "readiness_1",
  "service_ownership_1",
  "workflow_1",
  "shutdown_1",
  "prelaunch_inventory_2",
  "launch_2",
  "root_ownership_2",
  "readiness_2",
  "service_ownership_2",
  "restore_2",
  "screenshot",
  "shutdown_2",
  "evidence_generation",
  "cleanup",
]);
const PACKAGED_FAILURE_CODES = new Set([
  "PROCESS_INVENTORY_TIMEOUT",
  "PROCESS_INVENTORY_DEADLINE_EXCEEDED",
  "PROCESS_INVENTORY_COMMAND_FAILED",
  "PROCESS_INVENTORY_COMMAND_START_FAILED",
  "PROCESS_INVENTORY_HELPER_EXIT_UNCONFIRMED",
  "PROCESS_INVENTORY_OUTPUT_LIMIT",
  "PROCESS_INVENTORY_MALFORMED_OUTPUT",
  "PROCESS_INVENTORY_INVALID_IDENTITY",
  "PROCESS_INVENTORY_AMBIGUOUS_IDENTITY",
  "PROCESS_INVENTORY_UNSUPPORTED_PLATFORM",
  "ISOLATION_SETUP_FAILED",
  "APPLICATION_LAUNCH_FAILED",
  "ROOT_OWNERSHIP_NOT_ESTABLISHED",
  "APPLICATION_READINESS_FAILED",
  "SERVICE_OWNERSHIP_NOT_ESTABLISHED",
  "APPLICATION_WORKFLOW_FAILED",
  "SHUTDOWN_VERIFICATION_FAILED",
  "SCREENSHOT_CAPTURE_FAILED",
  "EVIDENCE_GENERATION_FAILED",
  "CLEANUP_FAILED",
]);
const PACKAGED_INVENTORY_FAILURE_CODES = new Set([
  "PROCESS_INVENTORY_TIMEOUT",
  "PROCESS_INVENTORY_DEADLINE_EXCEEDED",
  "PROCESS_INVENTORY_COMMAND_FAILED",
  "PROCESS_INVENTORY_COMMAND_START_FAILED",
  "PROCESS_INVENTORY_HELPER_EXIT_UNCONFIRMED",
  "PROCESS_INVENTORY_OUTPUT_LIMIT",
  "PROCESS_INVENTORY_MALFORMED_OUTPUT",
  "PROCESS_INVENTORY_INVALID_IDENTITY",
  "PROCESS_INVENTORY_AMBIGUOUS_IDENTITY",
  "PROCESS_INVENTORY_UNSUPPORTED_PLATFORM",
]);

export async function generateBuildEvidence({
  repositoryRoot = defaultRepositoryRoot,
  workflowHeadSha,
  testedCheckoutSha,
  pullRequestHeadSha,
  timestamp,
  runner,
  packagedE2e,
  manifestPath,
}) {
  const root = path.resolve(repositoryRoot);
  const canonicalRoot = await realpath(root);
  const appVersion = await readAppVersion(root);
  const releaseRoot = path.join(
    root,
    "apps",
    "desktop",
    "release",
    appVersion,
  );
  const expectedExecutable = path.join(
    releaseRoot,
    "win-unpacked",
    APP_EXECUTABLE_NAME,
  );
  const stagedService = path.join(
    root,
    "apps",
    "desktop",
    "build-resources",
    "service",
    SERVICE_EXECUTABLE_NAME,
  );
  const embeddedService = path.join(
    releaseRoot,
    "win-unpacked",
    "resources",
    "service",
    SERVICE_EXECUTABLE_NAME,
  );
  const expectedScreenshot = path.join(releaseRoot, "packaged-e2e.png");
  const expectedResult = path.join(releaseRoot, "packaged-e2e-result.json");
  const expectedPhase3Result = path.join(
    releaseRoot,
    "phase-3-packaged-e2e-result.json",
  );
  const expectedPhase3VoiceCastingEvidence = path.join(
    releaseRoot,
    "phase-3-voice-casting-evidence.json",
  );
  const expectedPhase3bResult = path.join(
    releaseRoot,
    "phase-3b-packaged-e2e-result.json",
  );
  const outputPath =
    manifestPath ?? path.join(releaseRoot, "build-evidence.json");

  assertExactPath(
    packagedE2e.executablePath,
    expectedExecutable,
    "packaged E2E executable",
  );
  assertExactPath(
    packagedE2e.screenshotPath,
    expectedScreenshot,
    "packaged E2E screenshot",
  );
  assertExactPath(
    packagedE2e.resultPath,
    expectedResult,
    "packaged E2E result",
  );
  assertExactPath(
    packagedE2e.phase3ResultPath,
    expectedPhase3Result,
    "Phase 3A packaged E2E result",
  );
  assertExactPath(
    packagedE2e.phase3VoiceCastingEvidencePath,
    expectedPhase3VoiceCastingEvidence,
    "Phase 3A voice-casting evidence",
  );
  assertExactPath(
    packagedE2e.phase3bResultPath,
    expectedPhase3bResult,
    "Phase 3B packaged E2E result",
  );
  assertRepositoryChild(root, outputPath, "build-evidence manifest");

  const [
    desktopApplicationEvidence,
    stagedServiceEvidence,
    embeddedServiceEvidence,
    screenshotEvidence,
    resultEvidence,
    phase3ResultEvidence,
    phase3VoiceCastingEvidenceFile,
    phase3bResultEvidence,
    secureIngestEvidence,
    storyAnalysisContractEvidence,
    voiceCatalogEvidence,
  ] = await Promise.all([
    requiredFileEvidence(canonicalRoot, root, expectedExecutable, "desktop application"),
    requiredFileEvidence(canonicalRoot, root, stagedService, "staged service"),
    requiredFileEvidence(canonicalRoot, root, embeddedService, "embedded service"),
    optionalFileEvidence(canonicalRoot, root, expectedScreenshot, "packaged E2E screenshot"),
    optionalFileEvidence(canonicalRoot, root, expectedResult, "packaged E2E result"),
    optionalFileEvidence(
      canonicalRoot,
      root,
      expectedPhase3Result,
      "Phase 3A packaged E2E result",
    ),
    optionalFileEvidence(
      canonicalRoot,
      root,
      expectedPhase3VoiceCastingEvidence,
      "Phase 3A voice-casting evidence",
    ),
    optionalFileEvidence(
      canonicalRoot,
      root,
      expectedPhase3bResult,
      "Phase 3B packaged E2E result",
    ),
    collectSecureIngestEvidence(canonicalRoot, root),
    collectStoryAnalysisContractEvidence(canonicalRoot, root),
    collectSyntheticVoiceCatalogEvidence(canonicalRoot, root),
  ]);

  const stagedServiceMatchesEmbeddedService =
    stagedServiceEvidence.sizeBytes === embeddedServiceEvidence.sizeBytes &&
    stagedServiceEvidence.sha256 === embeddedServiceEvidence.sha256;
  const normalizedStepOutcome = normalizeStepOutcome(packagedE2e.stepOutcome);
  const expectedHarnessStatus =
    normalizedStepOutcome === "success" ? "passed" : "failed";
  const harnessResult = await inspectHarnessResult(
    expectedResult,
    resultEvidence,
    appVersion,
  );
  const phase3HarnessResult = await inspectPhase3HarnessResult(
    expectedPhase3Result,
    phase3ResultEvidence,
    screenshotEvidence,
    harnessResult,
  );
  const voiceCastingContract = await inspectPhase3VoiceCastingEvidence(
    expectedPhase3VoiceCastingEvidence,
    phase3VoiceCastingEvidenceFile,
    phase3HarnessResult,
    harnessResult,
    voiceCatalogEvidence,
  );
  const localSpeechAuditionsContract = await inspectPhase3bEvidence(
    expectedPhase3bResult,
    phase3bResultEvidence,
    screenshotEvidence,
    harnessResult,
    phase3HarnessResult,
  );
  const harnessResultMatchesStepOutcome =
    harnessResult.contractValid &&
    harnessResult.reportedStatus === expectedHarnessStatus;
  const packagedE2eOwnershipExitProven =
    harnessResult.ownershipExitProven;
  const phase1DocxImportReviewProven =
    harnessResult.importReview !== null &&
    harnessResult.importReview.sourceSha256 ===
      secureIngestEvidence.syntheticDocx.decodedSha256 &&
    harnessResult.importReview.approvalDecision === "approved" &&
    harnessResult.importReview.approvalPersistedAfterRestart &&
    harnessResult.importReview.extractionPersistedAfterRestart &&
    harnessResult.importReview.analysisPersistedAfterRestart;
  const phase2ProfileAndAgentsProven =
    harnessResult.storyAnalysis !== null &&
    harnessResult.storyAnalysis.profile.profileFingerprint ===
      storyAnalysisContractEvidence.profile.fingerprint &&
    harnessResult.storyAnalysis.profile.profileId ===
      storyAnalysisContractEvidence.profile.values.profileId &&
    harnessResult.storyAnalysis.profile.semanticVersion ===
      storyAnalysisContractEvidence.profile.values.semanticVersion &&
    harnessResult.storyAnalysis.profile.producerId ===
      storyAnalysisContractEvidence.profile.values.producer.producerId &&
    harnessResult.storyAnalysis.profile.producerVersion ===
      storyAnalysisContractEvidence.profile.values.producer
        .producerVersion &&
    equalAgentVersionEvidence(
      harnessResult.storyAnalysis.agents,
      PHASE_2_RUNTIME_AGENTS,
    );
  const phase2ApprovedInputProven =
    harnessResult.storyAnalysis !== null &&
    harnessResult.importReview !== null &&
    harnessResult.storyAnalysis.approvedInput.sourceSha256 ===
      secureIngestEvidence.syntheticDocx.decodedSha256 &&
    harnessResult.storyAnalysis.approvedInput.sourceSha256 ===
      storyAnalysisContractEvidence.fixture.syntheticDocx
        .decodedSha256 &&
    harnessResult.storyAnalysis.approvedInput.extractedTextSha256 ===
      harnessResult.importReview.extractedTextSha256 &&
    harnessResult.storyAnalysis.approvedInput.extractionRevision ===
      harnessResult.importReview.extractionRevision;
  const phase2RunSnapshotAndStagesProven =
    harnessResult.storyAnalysis !== null &&
    harnessResult.storyAnalysis.run.status === "succeeded" &&
    equalStringArrays(
      harnessResult.storyAnalysis.observedStages,
      PHASE_2_JOB_STAGES,
    ) &&
    harnessResult.storyAnalysis.counts.agentExecutions ===
      PHASE_2_RUNTIME_AGENTS.length;
  const phase2StoryAssertionsProven =
    harnessResult.storyAnalysis !== null &&
    Object.values(harnessResult.storyAnalysis.assertions).every(
      (value) => value === true,
    ) &&
    storyAnalysisCountsMeetFixture(
      harnessResult.storyAnalysis.counts,
      storyAnalysisContractEvidence.fixture.expectedCounts,
    );
  const phase2CorrectionsProven =
    harnessResult.storyAnalysis !== null &&
    harnessResult.storyAnalysis.counts.corrections >= 3 &&
    Object.values(harnessResult.storyAnalysis.corrections).every(
      (correction) =>
        correction.immutable &&
        correction.lockedAgainstAutomation &&
        correction.persistedAfterRestart &&
        correction.effectiveAuthorityAfterRestart === "human",
    );
  const phase2FourGateDecisionsProven =
    harnessResult.storyAnalysis !== null &&
    harnessResult.storyAnalysis.gates.length ===
      PHASE_2_GATE_IDS.length &&
    harnessResult.storyAnalysis.gates.every(
      (gate, index) =>
        gate.gateId === PHASE_2_GATE_IDS[index] &&
        gate.immutable &&
        gate.beforeRestart.state === "approved" &&
        gate.afterRestart.state === "approved",
    );
  const phase2DecisionRecordsPersisted =
    phase2FourGateDecisionsProven &&
    harnessResult.storyAnalysis !== null &&
    harnessResult.storyAnalysis.restart.gateDecisionsPersisted === true &&
    harnessResult.storyAnalysis.gates.every(
      (gate) =>
        isSha256(gate.beforeRestart.decisionRecordFingerprint) &&
        gate.beforeRestart.decisionRecordFingerprint ===
          gate.afterRestart.decisionRecordFingerprint,
    );
  const phase2RestartDurabilityProven =
    harnessResult.storyAnalysis !== null &&
    Object.values(harnessResult.storyAnalysis.restart).every(
      (value) => value === true,
    );
  const phase2WholeBookAnalysisProven =
    phase2ProfileAndAgentsProven &&
    phase2ApprovedInputProven &&
    phase2RunSnapshotAndStagesProven &&
    phase2StoryAssertionsProven &&
    phase2CorrectionsProven &&
    phase2FourGateDecisionsProven &&
    phase2DecisionRecordsPersisted &&
    phase2RestartDurabilityProven;
  const phase3VoiceCastingProven =
    phase3HarnessResult.contractValid &&
    voiceCastingContract !== null;
  const phase3bLocalSpeechAuditionsProven =
    localSpeechAuditionsContract !== null;
  const packagedE2eEvidenceComplete =
    normalizedStepOutcome === "success" &&
    harnessResultMatchesStepOutcome &&
    screenshotEvidence.exists &&
    screenshotEvidence.sizeBytes > 0 &&
    harnessResult.screenshotCaptured === true &&
    packagedE2eOwnershipExitProven &&
    phase1DocxImportReviewProven &&
    phase2WholeBookAnalysisProven &&
    phase3VoiceCastingProven &&
    phase3bLocalSpeechAuditionsProven;
  const normalizedWorkflowHeadSha = normalizeHeadSha(workflowHeadSha);
  const normalizedTestedCheckoutSha = normalizeHeadSha(
    testedCheckoutSha,
  );
  if (normalizedWorkflowHeadSha !== normalizedTestedCheckoutSha) {
    throw new Error(
      "The tested checkout SHA does not match the workflow head SHA.",
    );
  }

  const manifest = {
    schemaVersion: BUILD_EVIDENCE_SCHEMA_VERSION,
    artifactPathScope: "repository-root",
    workflowHeadSha: normalizedWorkflowHeadSha,
    testedCheckoutSha: normalizedTestedCheckoutSha,
    pullRequestHeadSha: normalizeOptionalHeadSha(pullRequestHeadSha),
    appVersion,
    artifacts: {
      desktopApplication: desktopApplicationEvidence,
      stagedService: stagedServiceEvidence,
      embeddedService: embeddedServiceEvidence,
    },
    assertions: {
      stagedServiceMatchesEmbeddedService,
      packagedE2eHarnessResultMatchesStepOutcome:
        harnessResultMatchesStepOutcome,
      packagedE2eOwnershipExitProven,
      phase1DocxImportReviewProven,
      phase2ProfileAndAgentsProven,
      phase2ApprovedInputProven,
      phase2RunSnapshotAndStagesProven,
      phase2StoryAssertionsProven,
      phase2CorrectionsProven,
      phase2FourGateDecisionsProven,
      phase2DecisionRecordsPersisted,
      phase2RestartDurabilityProven,
      phase2WholeBookAnalysisProven,
      phase3bLocalSpeechAuditionsProven,
      packagedE2eEvidenceComplete,
    },
    secureIngest: secureIngestEvidence,
    storyAnalysisContract: storyAnalysisContractEvidence,
    packagedE2e: {
      result: harnessResult.reportedStatus ?? expectedHarnessStatus,
      stepOutcome: normalizedStepOutcome,
      screenshot: screenshotEvidence,
      machineResult: {
        ...resultEvidence,
        contractValid: harnessResult.contractValid,
        reportedStatus: harnessResult.reportedStatus,
        failureStage: harnessResult.failureStage,
        failureCode: harnessResult.failureCode,
        applicationLaunchBegan:
          harnessResult.applicationLaunchBegan,
        ownershipEstablished: harnessResult.ownershipEstablished,
        cleanupCompleted: harnessResult.cleanupCompleted,
        completedLaunches: harnessResult.completedLaunches,
      },
      importReview: harnessResult.importReview,
      storyAnalysis: harnessResult.storyAnalysis,
      flow: harnessResult.flow,
      launches: harnessResult.launches,
    },
    voiceCastingContract,
    localSpeechAuditionsContract,
    testTimestamp:
      phase3HarnessResult.completedAt ??
      harnessResult.completedAt ??
      normalizeTimestamp(timestamp),
    runner: normalizeRunner(runner),
  };

  await assertNoSyntheticStoryTextLeak(root, manifest);
  assertSafeCacheKeyEvidence(manifest);
  const serializedManifest = `${JSON.stringify(manifest, null, 2)}\n`;
  await mkdir(path.dirname(outputPath), { recursive: true });
  await writeFile(outputPath, serializedManifest, "utf8");

  if (!stagedServiceMatchesEmbeddedService) {
    throw new Error(
      "The staged service does not match the service embedded in the desktop artifact.",
    );
  }
  if (
    normalizedStepOutcome === "success" &&
    (!harnessResultMatchesStepOutcome ||
      !packagedE2eEvidenceComplete ||
      voiceCastingContract === null ||
      localSpeechAuditionsContract === null)
  ) {
    throw new Error(
      "The packaged E2E step reported success without complete, valid machine evidence.",
    );
  }

  return {
    manifest,
    manifestPath: outputPath,
  };
}

export async function validateBuildEvidenceManifest({
  repositoryRoot = defaultRepositoryRoot,
  manifestPath,
}) {
  const root = path.resolve(repositoryRoot);
  const canonicalRoot = await realpath(root);
  const target =
    manifestPath === undefined
      ? path.join(
          root,
          "apps",
          "desktop",
          "release",
          await readAppVersion(root),
          "build-evidence.json",
        )
      : path.resolve(root, manifestPath);
  assertRepositoryChild(root, target, "build-evidence manifest");
  const manifestFile = await requiredFileEvidence(
    canonicalRoot,
    root,
    target,
    "build-evidence manifest",
  );
  if (
    manifestFile.sizeBytes === 0 ||
    manifestFile.sizeBytes > MAX_SECURITY_INPUT_BYTES
  ) {
    throw new Error("The build-evidence manifest size is invalid.");
  }

  let manifest;
  try {
    manifest = JSON.parse(await readFile(target, "utf8"));
  } catch {
    throw new Error("The build-evidence manifest is invalid JSON.");
  }
  const topLevelKeys = [
    "schemaVersion",
    "artifactPathScope",
    "workflowHeadSha",
    "testedCheckoutSha",
    "pullRequestHeadSha",
    "appVersion",
    "artifacts",
    "assertions",
    "secureIngest",
    "storyAnalysisContract",
    "packagedE2e",
    "voiceCastingContract",
    "localSpeechAuditionsContract",
    "testTimestamp",
    "runner",
  ];
  if (
    !isPlainObject(manifest) ||
    !hasExactKeys(manifest, topLevelKeys) ||
    manifest.schemaVersion !== BUILD_EVIDENCE_SCHEMA_VERSION ||
    manifest.artifactPathScope !== "repository-root" ||
    normalizeHeadSha(manifest.workflowHeadSha) !==
      manifest.workflowHeadSha ||
    normalizeHeadSha(manifest.testedCheckoutSha) !==
      manifest.testedCheckoutSha ||
    manifest.workflowHeadSha !== manifest.testedCheckoutSha ||
    (manifest.pullRequestHeadSha !== null &&
      normalizeHeadSha(manifest.pullRequestHeadSha) !==
        manifest.pullRequestHeadSha) ||
    normalizeTimestamp(manifest.testTimestamp) !==
      manifest.testTimestamp
  ) {
    throw new Error(
      "The build-evidence manifest envelope is invalid.",
    );
  }
  await assertNoSyntheticStoryTextLeak(root, manifest);
  assertSafeCacheKeyEvidence(manifest);
  const appVersion = await readAppVersion(root);
  if (manifest.appVersion !== appVersion) {
    throw new Error(
      "The build-evidence manifest app version is invalid.",
    );
  }
  const releaseRoot = path.join(
    root,
    "apps",
    "desktop",
    "release",
    appVersion,
  );
  const expectedPaths = {
    desktopApplication: path.join(
      releaseRoot,
      "win-unpacked",
      APP_EXECUTABLE_NAME,
    ),
    stagedService: path.join(
      root,
      "apps",
      "desktop",
      "build-resources",
      "service",
      SERVICE_EXECUTABLE_NAME,
    ),
    embeddedService: path.join(
      releaseRoot,
      "win-unpacked",
      "resources",
      "service",
      SERVICE_EXECUTABLE_NAME,
    ),
    screenshot: path.join(releaseRoot, "packaged-e2e.png"),
    result: path.join(releaseRoot, "packaged-e2e-result.json"),
    phase3Result: path.join(
      releaseRoot,
      "phase-3-packaged-e2e-result.json",
    ),
    phase3VoiceCastingEvidence: path.join(
      releaseRoot,
      "phase-3-voice-casting-evidence.json",
    ),
    phase3bResult: path.join(
      releaseRoot,
      "phase-3b-packaged-e2e-result.json",
    ),
  };
  if (
    !isPlainObject(manifest.artifacts) ||
    !hasExactKeys(manifest.artifacts, [
      "desktopApplication",
      "stagedService",
      "embeddedService",
    ])
  ) {
    throw new Error(
      "The build-evidence artifact inventory is invalid.",
    );
  }
  const [
    desktopApplication,
    stagedService,
    embeddedService,
    screenshot,
    resultEvidence,
    phase3ResultEvidence,
    phase3VoiceCastingEvidenceFile,
    phase3bResultEvidence,
    secureIngest,
    storyAnalysisContract,
    voiceCatalogEvidence,
  ] = await Promise.all([
    requiredFileEvidence(
      canonicalRoot,
      root,
      expectedPaths.desktopApplication,
      "desktop application",
    ),
    requiredFileEvidence(
      canonicalRoot,
      root,
      expectedPaths.stagedService,
      "staged service",
    ),
    requiredFileEvidence(
      canonicalRoot,
      root,
      expectedPaths.embeddedService,
      "embedded service",
    ),
    optionalFileEvidence(
      canonicalRoot,
      root,
      expectedPaths.screenshot,
      "packaged E2E screenshot",
    ),
    optionalFileEvidence(
      canonicalRoot,
      root,
      expectedPaths.result,
      "packaged E2E result",
    ),
    optionalFileEvidence(
      canonicalRoot,
      root,
      expectedPaths.phase3Result,
      "Phase 3A packaged E2E result",
    ),
    optionalFileEvidence(
      canonicalRoot,
      root,
      expectedPaths.phase3VoiceCastingEvidence,
      "Phase 3A voice-casting evidence",
    ),
    optionalFileEvidence(
      canonicalRoot,
      root,
      expectedPaths.phase3bResult,
      "Phase 3B packaged E2E result",
    ),
    collectSecureIngestEvidence(canonicalRoot, root),
    collectStoryAnalysisContractEvidence(canonicalRoot, root),
    collectSyntheticVoiceCatalogEvidence(canonicalRoot, root),
  ]);
  const expectedArtifacts = {
    desktopApplication,
    stagedService,
    embeddedService,
  };
  if (
    !jsonValuesEqual(manifest.artifacts, expectedArtifacts) ||
    !jsonValuesEqual(manifest.secureIngest, secureIngest) ||
    !jsonValuesEqual(
      manifest.storyAnalysisContract,
      storyAnalysisContract,
    )
  ) {
    throw new Error(
      "The build-evidence inputs or artifact hashes are stale.",
    );
  }

  const assertionKeys = [
    "stagedServiceMatchesEmbeddedService",
    "packagedE2eHarnessResultMatchesStepOutcome",
    "packagedE2eOwnershipExitProven",
    "phase1DocxImportReviewProven",
    "phase2ProfileAndAgentsProven",
    "phase2ApprovedInputProven",
    "phase2RunSnapshotAndStagesProven",
    "phase2StoryAssertionsProven",
    "phase2CorrectionsProven",
    "phase2FourGateDecisionsProven",
    "phase2DecisionRecordsPersisted",
    "phase2RestartDurabilityProven",
    "phase2WholeBookAnalysisProven",
    "phase3bLocalSpeechAuditionsProven",
    "packagedE2eEvidenceComplete",
  ];
  if (
    !isPlainObject(manifest.assertions) ||
    !hasExactKeys(manifest.assertions, assertionKeys) ||
    !assertionKeys.every(
      (key) => manifest.assertions[key] === true,
    )
  ) {
    throw new Error(
      "The build-evidence manifest contains an unproven assertion.",
    );
  }
  if (
    stagedService.sha256 !== embeddedService.sha256 ||
    stagedService.sizeBytes !== embeddedService.sizeBytes
  ) {
    throw new Error(
      "The build-evidence staged and embedded services differ.",
    );
  }

  const harnessResult = await inspectHarnessResult(
    expectedPaths.result,
    resultEvidence,
    appVersion,
  );
  const packaged = manifest.packagedE2e;
  if (
    !isPlainObject(packaged) ||
    !hasExactKeys(packaged, [
      "result",
      "stepOutcome",
      "screenshot",
      "machineResult",
      "importReview",
      "storyAnalysis",
      "flow",
      "launches",
    ]) ||
    packaged.result !== "passed" ||
    packaged.stepOutcome !== "success" ||
    !jsonValuesEqual(packaged.screenshot, screenshot) ||
    !isPlainObject(packaged.machineResult) ||
    !hasExactKeys(packaged.machineResult, [
      "path",
      "exists",
      "sizeBytes",
      "sha256",
      "contractValid",
      "reportedStatus",
      "failureStage",
      "failureCode",
      "applicationLaunchBegan",
      "ownershipEstablished",
      "cleanupCompleted",
      "completedLaunches",
    ]) ||
    !jsonValuesEqual(
      {
        path: packaged.machineResult.path,
        exists: packaged.machineResult.exists,
        sizeBytes: packaged.machineResult.sizeBytes,
        sha256: packaged.machineResult.sha256,
      },
      resultEvidence,
    ) ||
    packaged.machineResult.contractValid !== true ||
    packaged.machineResult.reportedStatus !== "passed" ||
    packaged.machineResult.failureStage !== null ||
    packaged.machineResult.failureCode !== null ||
    packaged.machineResult.applicationLaunchBegan !== true ||
    packaged.machineResult.ownershipEstablished !== true ||
    packaged.machineResult.cleanupCompleted !== true ||
    !equalNumberArrays(
      packaged.machineResult.completedLaunches,
      [1, 2],
    ) ||
    harnessResult.contractValid !== true ||
    harnessResult.reportedStatus !== "passed" ||
    harnessResult.ownershipExitProven !== true ||
    harnessResult.completedAt !== manifest.testTimestamp ||
    harnessResult.screenshotCaptured !== true ||
    screenshot.exists !== true ||
    screenshot.sizeBytes === null ||
    screenshot.sizeBytes <= 0 ||
    harnessResult.importReview === null ||
    harnessResult.storyAnalysis === null ||
    harnessResult.importReview.sourceSha256 !==
      secureIngest.syntheticDocx.decodedSha256 ||
    harnessResult.storyAnalysis.approvedInput.sourceSha256 !==
      secureIngest.syntheticDocx.decodedSha256 ||
    harnessResult.storyAnalysis.approvedInput.extractedTextSha256 !==
      harnessResult.importReview.extractedTextSha256 ||
    harnessResult.storyAnalysis.approvedInput.extractionRevision !==
      harnessResult.importReview.extractionRevision ||
    !storyAnalysisCountsMeetFixture(
      harnessResult.storyAnalysis.counts,
      storyAnalysisContract.fixture.expectedCounts,
    ) ||
    harnessResult.storyAnalysis.counts.corrections < 3 ||
    !Object.values(harnessResult.storyAnalysis.assertions).every(
      (value) => value === true,
    ) ||
    !Object.values(harnessResult.storyAnalysis.corrections).every(
      (correction) =>
        correction.immutable &&
        correction.lockedAgainstAutomation &&
        correction.persistedAfterRestart &&
        correction.effectiveAuthorityBeforeRestart === "human" &&
        correction.effectiveAuthorityAfterRestart === "human",
    ) ||
    harnessResult.storyAnalysis.corrections.characterIdentity
      .reasonFingerprint !==
      PHASE_2_CORRECTION_REASON_FINGERPRINTS.character_identity ||
    harnessResult.storyAnalysis.corrections.dialogueSpeaker
      .reasonFingerprint !==
      PHASE_2_CORRECTION_REASON_FINGERPRINTS.dialogue_speaker ||
    harnessResult.storyAnalysis.corrections.continuityDisposition
      .reasonFingerprint !==
      PHASE_2_CORRECTION_REASON_FINGERPRINTS.continuity_disposition ||
    !harnessResult.storyAnalysis.gates.every(
      (gate, index) =>
        gate.gateId === PHASE_2_GATE_IDS[index] &&
        gate.immutable &&
        gate.beforeRestart.state === "approved" &&
        gate.afterRestart.state === "approved" &&
        gate.beforeRestart.profileFingerprint ===
          harnessResult.storyAnalysis.profile.profileFingerprint &&
        gate.beforeRestart.runFingerprint ===
          harnessResult.storyAnalysis.run.runFingerprint &&
        gate.beforeRestart.snapshotId ===
          harnessResult.storyAnalysis.run.snapshotId &&
        gate.beforeRestart.snapshotRevision ===
          harnessResult.storyAnalysis.run.snapshotRevision &&
        gate.beforeRestart.snapshotFingerprint ===
          harnessResult.storyAnalysis.run.snapshotFingerprint &&
        jsonValuesEqual(gate.beforeRestart, gate.afterRestart),
    ) ||
    !Object.values(harnessResult.storyAnalysis.restart).every(
      (value) => value === true,
    ) ||
    !jsonValuesEqual(
      packaged.importReview,
      harnessResult.importReview,
    ) ||
    !jsonValuesEqual(
      packaged.storyAnalysis,
      harnessResult.storyAnalysis,
    ) ||
    !jsonValuesEqual(packaged.flow, harnessResult.flow) ||
    !jsonValuesEqual(packaged.launches, harnessResult.launches)
  ) {
    throw new Error(
      "The build-evidence packaged E2E proof is invalid or stale.",
    );
  }
  const phase3HarnessResult = await inspectPhase3HarnessResult(
    expectedPaths.phase3Result,
    phase3ResultEvidence,
    screenshot,
    harnessResult,
  );
  const voiceCastingContract = await inspectPhase3VoiceCastingEvidence(
    expectedPaths.phase3VoiceCastingEvidence,
    phase3VoiceCastingEvidenceFile,
    phase3HarnessResult,
    harnessResult,
    voiceCatalogEvidence,
  );
  const localSpeechAuditionsContract = await inspectPhase3bEvidence(
    expectedPaths.phase3bResult,
    phase3bResultEvidence,
    screenshot,
    harnessResult,
    phase3HarnessResult,
  );
  if (
    !phase3HarnessResult.contractValid ||
    voiceCastingContract === null ||
    localSpeechAuditionsContract === null ||
    !jsonValuesEqual(
      manifest.voiceCastingContract,
      voiceCastingContract,
    ) ||
    !jsonValuesEqual(
      manifest.localSpeechAuditionsContract,
      localSpeechAuditionsContract,
    ) ||
    localSpeechAuditionsContract.completedAt !== manifest.testTimestamp ||
    phase3HarnessResult.completedAt !== manifest.testTimestamp
  ) {
    throw new Error(
      "The Phase 3A voice-casting or Phase 3B local-speech evidence is invalid or stale.",
    );
  }
  if (
    !hasExactKeys(manifest.runner, [
      "name",
      "os",
      "architecture",
      "environment",
      "runId",
      "runAttempt",
      "workflow",
      "job",
    ]) ||
    !jsonValuesEqual(manifest.runner, normalizeRunner(manifest.runner))
  ) {
    throw new Error(
      "The build-evidence runner identity is invalid.",
    );
  }
  return {
    manifest,
    manifestPath: target,
    manifestSha256: manifestFile.sha256,
  };
}

async function readAppVersion(repositoryRoot) {
  const packagePath = path.join(
    repositoryRoot,
    "apps",
    "desktop",
    "package.json",
  );
  const value = JSON.parse(await readFile(packagePath, "utf8"));
  const version = value?.version;
  if (
    typeof version !== "string" ||
    !/^[0-9A-Za-z][0-9A-Za-z.+-]{0,79}$/u.test(version)
  ) {
    throw new Error("The desktop package version is invalid.");
  }
  return version;
}

async function collectSecureIngestEvidence(
  canonicalRoot,
  repositoryRoot,
) {
  const requirementsInputPath = path.join(
    repositoryRoot,
    "apps",
    "local-service",
    "requirements.in",
  );
  const requirementsLockPath = path.join(
    repositoryRoot,
    "apps",
    "local-service",
    "requirements.lock",
  );
  const generatorPath = path.join(
    repositoryRoot,
    "fixtures",
    "synthetic-story",
    "generate-fixtures.mjs",
  );
  const encodedDocxPath = path.join(
    repositoryRoot,
    "fixtures",
    "synthetic-story",
    "sample-story.docx.base64",
  );
  const [
    requirementsInput,
    requirementsLock,
    fixtureGenerator,
    encodedDocx,
  ] = await Promise.all([
    requiredFileEvidence(
      canonicalRoot,
      repositoryRoot,
      requirementsInputPath,
      "Python requirements input",
    ),
    requiredFileEvidence(
      canonicalRoot,
      repositoryRoot,
      requirementsLockPath,
      "Python requirements lock",
    ),
    requiredFileEvidence(
      canonicalRoot,
      repositoryRoot,
      generatorPath,
      "synthetic fixture generator",
    ),
    requiredFileEvidence(
      canonicalRoot,
      repositoryRoot,
      encodedDocxPath,
      "synthetic DOCX base64 fixture",
    ),
  ]);
  const [requirementsInputText, requirementsLockText, encodedDocxText] =
    await Promise.all([
      readBoundedUtf8(
        requirementsInputPath,
        "Python requirements input",
      ),
      readBoundedUtf8(
        requirementsLockPath,
        "Python requirements lock",
      ),
      readBoundedAscii(
        encodedDocxPath,
        "synthetic DOCX base64 fixture",
      ),
    ]);
  assertSecureIngestDependencyPins(
    requirementsInputText,
    requirementsLockText,
  );
  const decodedDocx = decodeStrictBase64(encodedDocxText);
  if (
    decodedDocx.length === 0 ||
    decodedDocx.length > 1024 * 1024 ||
    decodedDocx.subarray(0, 4).compare(
      Buffer.from("PK\u0003\u0004", "binary"),
    ) !== 0
  ) {
    throw new Error("The synthetic DOCX fixture bytes are invalid.");
  }
  return {
    supportedFormats: ["txt", "markdown", "docx", "epub", "pdf"],
    boundaryLimits: {
      ...SECURE_INGEST_BOUNDARY_LIMITS,
    },
    parserProfile: {
      values: {
        ...PARSER_LIMITS_PROFILE,
      },
      canonicalJson: PARSER_LIMITS_PROFILE_CANONICAL_JSON,
      fingerprint: PARSER_LIMITS_FINGERPRINT,
    },
    parserDependencies: SECURE_INGEST_DEPENDENCIES,
    inputs: {
      requirementsInput,
      requirementsLock,
      fixtureGenerator,
      syntheticDocxEncoding: encodedDocx,
    },
    syntheticDocx: {
      encodedPath: encodedDocx.path,
      decodedName: "sample-story.docx",
      decodedSizeBytes: decodedDocx.length,
      decodedSha256: sha256Bytes(decodedDocx),
    },
  };
}

async function collectStoryAnalysisContractEvidence(
  canonicalRoot,
  repositoryRoot,
) {
  const fixtureDirectory = path.join(
    repositoryRoot,
    "fixtures",
    "synthetic-story",
  );
  const paths = {
    generator: path.join(fixtureDirectory, "generate-fixtures.mjs"),
    canonicalText: path.join(fixtureDirectory, "sample-story.txt"),
    markdown: path.join(fixtureDirectory, "sample-story.md"),
    expectations: path.join(
      fixtureDirectory,
      "sample-story.expected.json",
    ),
    encodedDocx: path.join(
      fixtureDirectory,
      "sample-story.docx.base64",
    ),
  };
  const [
    generator,
    canonicalTextFile,
    markdownFile,
    expectationsFile,
    encodedDocxFile,
    canonicalText,
    markdownText,
    expectationsText,
    encodedDocxText,
  ] = await Promise.all([
    requiredFileEvidence(
      canonicalRoot,
      repositoryRoot,
      paths.generator,
      "Phase 2 fixture generator",
    ),
    requiredFileEvidence(
      canonicalRoot,
      repositoryRoot,
      paths.canonicalText,
      "Phase 2 canonical text fixture",
    ),
    requiredFileEvidence(
      canonicalRoot,
      repositoryRoot,
      paths.markdown,
      "Phase 2 Markdown fixture",
    ),
    requiredFileEvidence(
      canonicalRoot,
      repositoryRoot,
      paths.expectations,
      "Phase 2 fixture expectations",
    ),
    requiredFileEvidence(
      canonicalRoot,
      repositoryRoot,
      paths.encodedDocx,
      "Phase 2 DOCX base64 fixture",
    ),
    readBoundedUtf8(
      paths.canonicalText,
      "Phase 2 canonical text fixture",
    ),
    readBoundedUtf8(paths.markdown, "Phase 2 Markdown fixture"),
    readBoundedUtf8(
      paths.expectations,
      "Phase 2 fixture expectations",
    ),
    readBoundedAscii(
      paths.encodedDocx,
      "Phase 2 DOCX base64 fixture",
    ),
  ]);
  let expectations;
  try {
    expectations = JSON.parse(expectationsText);
  } catch {
    throw new Error("The Phase 2 fixture expectations are invalid JSON.");
  }
  const decodedDocx = decodeStrictBase64(encodedDocxText);
  const canonicalTextSha256 = sha256Bytes(
    Buffer.from(canonicalText, "utf8"),
  );
  const markdownSha256 = sha256Bytes(
    Buffer.from(markdownText, "utf8"),
  );
  const decodedDocxSha256 = sha256Bytes(decodedDocx);
  if (
    !isPlainObject(expectations) ||
    expectations.schemaVersion !== ANALYSIS_CONTRACT_VERSION ||
    expectations.fixtureId !== "phase-2-whole-book-intelligence" ||
    expectations.offsetUnit !== "unicode-code-point" ||
    !isPlainObject(expectations.canonicalText) ||
    expectations.canonicalText.fileName !== "sample-story.txt" ||
    expectations.canonicalText.byteLength !==
      Buffer.byteLength(canonicalText, "utf8") ||
    expectations.canonicalText.codePointLength !==
      [...canonicalText].length ||
    expectations.canonicalText.sha256 !== canonicalTextSha256 ||
    !isPlainObject(expectations.markdown) ||
    expectations.markdown.fileName !== "sample-story.md" ||
    expectations.markdown.byteLength !==
      Buffer.byteLength(markdownText, "utf8") ||
    expectations.markdown.codePointLength !==
      [...markdownText].length ||
    expectations.markdown.sha256 !== markdownSha256 ||
    !isPlainObject(expectations.binaryFixtures) ||
    !isPlainObject(expectations.binaryFixtures.docx) ||
    expectations.binaryFixtures.docx.encodedFileName !==
      "sample-story.docx.base64" ||
    expectations.binaryFixtures.docx.decodedFileName !==
      "sample-story.docx" ||
    expectations.binaryFixtures.docx.sizeBytes !== decodedDocx.length ||
    expectations.binaryFixtures.docx.sha256 !== decodedDocxSha256
  ) {
    throw new Error(
      "The Phase 2 fixture expectations do not match the committed inputs.",
    );
  }
  const counts = sanitizeSyntheticExpectedCounts(
    expectations.expectedCounts,
  );
  const expectedSpans = [];
  collectExpectedFixtureSpans(expectations, expectedSpans);
  if (expectedSpans.length < 40) {
    throw new Error(
      "The Phase 2 fixture does not contain enough exact expected spans.",
    );
  }
  const seenSpanIds = new Set();
  for (const span of expectedSpans) {
    if (
      seenSpanIds.has(span.spanId) ||
      !validExpectedFixtureSpan(canonicalText, span)
    ) {
      throw new Error(
        "The Phase 2 fixture expected offsets or hashes are invalid.",
      );
    }
    seenSpanIds.add(span.spanId);
  }
  if (
    !Array.isArray(expectations.chapters) ||
    expectations.chapters.length !== counts.chapters ||
    !Array.isArray(expectations.scenes) ||
    expectations.scenes.length !== counts.scenes ||
    !Array.isArray(expectations.characters) ||
    expectations.characters.length !== counts.namedCharacters ||
    !Array.isArray(expectations.locations) ||
    expectations.locations.length !== counts.locations ||
    !Array.isArray(expectations.dialogueLines) ||
    expectations.dialogueLines.length !== counts.dialogueLines ||
    !isPlainObject(expectations.narrationDistinction) ||
    !isPlainObject(expectations.pointOfView) ||
    !isPlainObject(expectations.timeline) ||
    !Array.isArray(expectations.relationshipChanges) ||
    expectations.relationshipChanges.length < 2 ||
    !isPlainObject(expectations.emotionalProgression) ||
    !isPlainObject(expectations.continuityAnomaly) ||
    expectations.continuityAnomaly.expectedCategory !==
      "unexplained_object_state_change"
  ) {
    throw new Error(
      "The Phase 2 fixture semantic expectations are incomplete.",
    );
  }
  const profileValues = JSON.parse(
    WHOLE_BOOK_ANALYSIS_PROFILE_CANONICAL_JSON,
  );
  if (
    sha256Bytes(
      Buffer.from(
        WHOLE_BOOK_ANALYSIS_PROFILE_CANONICAL_JSON,
        "utf8",
      ),
    ) !== WHOLE_BOOK_ANALYSIS_PROFILE_FINGERPRINT
  ) {
    throw new Error(
      "The Phase 2 analysis profile fingerprint is invalid.",
    );
  }
  return {
    profile: {
      values: profileValues,
      canonicalJson: WHOLE_BOOK_ANALYSIS_PROFILE_CANONICAL_JSON,
      fingerprint: WHOLE_BOOK_ANALYSIS_PROFILE_FINGERPRINT,
    },
    inputs: {
      generator,
      canonicalText: canonicalTextFile,
      markdown: markdownFile,
      expectations: expectationsFile,
      syntheticDocxEncoding: encodedDocxFile,
    },
    fixture: {
      fixtureId: expectations.fixtureId,
      canonicalText: {
        byteLength: Buffer.byteLength(canonicalText, "utf8"),
        codePointLength: [...canonicalText].length,
        sha256: canonicalTextSha256,
      },
      markdown: {
        byteLength: Buffer.byteLength(markdownText, "utf8"),
        codePointLength: [...markdownText].length,
        sha256: markdownSha256,
      },
      syntheticDocx: {
        decodedSizeBytes: decodedDocx.length,
        decodedSha256: decodedDocxSha256,
      },
      expectedCounts: counts,
      expectedSpanCount: expectedSpans.length,
      expectationsSha256: expectationsFile.sha256,
    },
  };
}

function sanitizeSyntheticExpectedCounts(value) {
  if (
    !isPlainObject(value) ||
    !hasExactKeys(value, [
      "chapters",
      "scenes",
      "namedCharacters",
      "locations",
      "dialogueLines",
      "ambiguousDialogueLines",
      "povShifts",
      "continuityAnomalies",
    ])
  ) {
    throw new Error("The Phase 2 fixture expected counts are invalid.");
  }
  const counts = {};
  for (const [key, count] of Object.entries(value)) {
    if (
      !Number.isSafeInteger(count) ||
      count < 0 ||
      count > 1_000_000
    ) {
      throw new Error("The Phase 2 fixture expected counts are invalid.");
    }
    counts[key] = count;
  }
  if (
    counts.chapters < 3 ||
    counts.scenes < 6 ||
    counts.namedCharacters < 6 ||
    counts.locations < 3 ||
    counts.dialogueLines < 4 ||
    counts.ambiguousDialogueLines < 2 ||
    counts.povShifts < 1 ||
    counts.continuityAnomalies < 1
  ) {
    throw new Error(
      "The Phase 2 fixture does not meet the semantic minimums.",
    );
  }
  return counts;
}

function collectExpectedFixtureSpans(value, spans) {
  if (Array.isArray(value)) {
    for (const item of value) {
      collectExpectedFixtureSpans(item, spans);
    }
    return;
  }
  if (!isPlainObject(value)) {
    return;
  }
  if (
    typeof value.spanId === "string" &&
    Object.hasOwn(value, "startOffset") &&
    Object.hasOwn(value, "endOffset") &&
    Object.hasOwn(value, "textSha256")
  ) {
    spans.push(value);
    return;
  }
  for (const item of Object.values(value)) {
    collectExpectedFixtureSpans(item, spans);
  }
}

function validExpectedFixtureSpan(text, span) {
  if (
    !/^[A-Za-z0-9][A-Za-z0-9._:-]*$/u.test(span.spanId) ||
    !Number.isSafeInteger(span.startOffset) ||
    !Number.isSafeInteger(span.endOffset) ||
    span.startOffset < 0 ||
    span.endOffset <= span.startOffset ||
    span.endOffset > [...text].length ||
    !isSha256(span.textSha256)
  ) {
    return false;
  }
  const exactText = [...text]
    .slice(span.startOffset, span.endOffset)
    .join("");
  return (
    (!Object.hasOwn(span, "exactText") ||
      span.exactText === exactText) &&
    sha256Bytes(Buffer.from(exactText, "utf8")) === span.textSha256
  );
}

async function assertNoSyntheticStoryTextLeak(root, manifest) {
  const expectationsPath = path.join(
    root,
    "fixtures",
    "synthetic-story",
    "sample-story.expected.json",
  );
  const expectations = JSON.parse(
    await readBoundedUtf8(
      expectationsPath,
      "Phase 2 fixture expectations",
    ),
  );
  const expectedSpans = [];
  collectExpectedFixtureSpans(expectations, expectedSpans);
  if (
    expectedSpans.some(
      (span) =>
        typeof span.exactText === "string" &&
        objectContainsString(manifest, span.exactText),
    )
  ) {
    throw new Error(
      "The build-evidence manifest contains private story text.",
    );
  }
}

const CACHE_KEY_HASH_FIELDS = new Set([
  "cacheKey",
  "originalCacheKey",
  "repeatedCacheKey",
  "priorCacheKey",
  "regeneratedCacheKey",
]);

function assertSafeCacheKeyEvidence(value) {
  if (Array.isArray(value)) {
    for (const item of value) assertSafeCacheKeyEvidence(item);
    return;
  }
  if (!isPlainObject(value)) return;
  for (const [key, item] of Object.entries(value)) {
    if (CACHE_KEY_HASH_FIELDS.has(key) && !isSha256(item)) {
      throw new Error(
        "The build-evidence manifest contains an unhashed cache-key value.",
      );
    }
    if (
      /cache[_-]?key/iu.test(key) &&
      !CACHE_KEY_HASH_FIELDS.has(key) &&
      key !== "identicalCacheKeyInputsProven"
    ) {
      throw new Error(
        "The build-evidence manifest contains an unknown cache-key field.",
      );
    }
    assertSafeCacheKeyEvidence(item);
  }
}

function objectContainsString(value, privateText) {
  if (typeof value === "string") {
    return value.includes(privateText);
  }
  if (Array.isArray(value)) {
    return value.some((item) => objectContainsString(item, privateText));
  }
  if (isPlainObject(value)) {
    return Object.values(value).some((item) =>
      objectContainsString(item, privateText),
    );
  }
  return false;
}

async function readBoundedUtf8(target, label) {
  const bytes = await readFile(target);
  if (
    bytes.length === 0 ||
    bytes.length > MAX_SECURITY_INPUT_BYTES ||
    bytes.includes(0)
  ) {
    throw new Error(`The ${label} content is invalid.`);
  }
  const text = bytes.toString("utf8");
  if (Buffer.from(text, "utf8").compare(bytes) !== 0) {
    throw new Error(`The ${label} must be valid UTF-8.`);
  }
  return text;
}

async function readBoundedAscii(target, label) {
  const text = await readBoundedUtf8(target, label);
  if ([...text].some((character) => character.codePointAt(0) > 0x7f)) {
    throw new Error(`The ${label} must contain ASCII only.`);
  }
  return text;
}

function assertSecureIngestDependencyPins(requirementsInput, requirementsLock) {
  const inputLines = requirementsInput.split(/\r?\n/gu);
  const lockLines = requirementsLock.split(/\r?\n/gu);
  for (const dependency of SECURE_INGEST_DEPENDENCIES) {
    const pin = `${dependency.name}==${dependency.version}`;
    if (inputLines.filter((line) => line === pin).length !== 1) {
      throw new Error(`The ${dependency.name} input pin is invalid.`);
    }
    const lockIndex = lockLines.indexOf(`${pin} \\`);
    const firstHash = lockLines[lockIndex + 1] ?? "";
    if (
      lockIndex < 0 ||
      !/^ {4}--hash=sha256:[a-f0-9]{64}(?: \\)?$/u.test(firstHash)
    ) {
      throw new Error(`The ${dependency.name} hash lock is invalid.`);
    }
  }
}

function decodeStrictBase64(text) {
  if (
    text.length === 0 ||
    /[^A-Za-z0-9+/=\r\n]/u.test(text)
  ) {
    throw new Error("The synthetic DOCX fixture is not strict base64.");
  }
  const compact = text.replace(/\r?\n/gu, "");
  if (
    compact.length === 0 ||
    compact.length % 4 !== 0 ||
    !/^[A-Za-z0-9+/]+={0,2}$/u.test(compact)
  ) {
    throw new Error("The synthetic DOCX fixture is not strict base64.");
  }
  const decoded = Buffer.from(compact, "base64");
  if (decoded.toString("base64") !== compact) {
    throw new Error("The synthetic DOCX fixture base64 is not canonical.");
  }
  return decoded;
}

async function requiredFileEvidence(
  canonicalRoot,
  repositoryRoot,
  target,
  label,
) {
  const evidence = await fileEvidence(
    canonicalRoot,
    repositoryRoot,
    target,
    label,
    true,
  );
  return {
    path: evidence.path,
    sizeBytes: evidence.sizeBytes,
    sha256: evidence.sha256,
  };
}

async function optionalFileEvidence(
  canonicalRoot,
  repositoryRoot,
  target,
  label,
) {
  return fileEvidence(
    canonicalRoot,
    repositoryRoot,
    target,
    label,
    false,
  );
}

async function fileEvidence(
  canonicalRoot,
  repositoryRoot,
  target,
  label,
  required,
) {
  const relativePath = relativeRepositoryPath(repositoryRoot, target, label);
  let metadata;
  try {
    metadata = await lstat(target);
  } catch (error) {
    if (!required && isMissingFileError(error)) {
      return {
        path: relativePath,
        exists: false,
        sizeBytes: null,
        sha256: null,
      };
    }
    throw new Error(`The ${label} is missing or unreadable.`, {
      cause: error,
    });
  }
  if (!metadata.isFile() || metadata.isSymbolicLink()) {
    throw new Error(`The ${label} must be a regular, non-symlink file.`);
  }

  const canonicalTarget = await realpath(target);
  assertRepositoryChild(canonicalRoot, canonicalTarget, label);
  return {
    path: relativePath,
    exists: true,
    sizeBytes: metadata.size,
    sha256: await sha256File(canonicalTarget),
  };
}

async function inspectHarnessResult(
  resultPath,
  resultEvidence,
  appVersion,
) {
  if (!resultEvidence.exists) {
    return invalidHarnessResult();
  }
  if (
    typeof resultEvidence.sizeBytes !== "number" ||
    resultEvidence.sizeBytes > MAX_HARNESS_RESULT_BYTES
  ) {
    return invalidHarnessResult();
  }

  try {
    const value = JSON.parse(await readFile(resultPath, "utf8"));
    if (
      !isPlainObject(value) ||
      !hasExactKeys(value, [
        "schemaVersion",
        "completedAt",
        "status",
        "failureStage",
        "failureCode",
        "packagedVersion",
        "executable",
        "fixture",
        "isolationEnvironment",
        "completedLaunches",
        "applicationLaunchBegan",
        "ownershipEstablished",
        "cleanupCompleted",
        "preexistingRelevantProcesses",
        "flow",
        "screenshot",
        "importReview",
        "storyAnalysis",
        "launches",
      ])
    ) {
      return invalidHarnessResult();
    }
    const expectedExecutable = `release/${appVersion}/win-unpacked/${APP_EXECUTABLE_NAME}`;
    const completedAt =
      typeof value.completedAt === "string"
        ? normalizeUtcTimestamp(value.completedAt)
        : null;
    const launches = sanitizeLaunchProof(value.launches);
    const completedLaunches = sanitizeCompletedLaunchNumbers(
      value.completedLaunches,
    );
    const importReview =
      value.importReview === null
        ? null
        : sanitizeImportReviewEvidence(value.importReview);
    const importReviewShapeValid =
      value.importReview === null || importReview !== null;
    const storyAnalysis =
      value.storyAnalysis === null
        ? null
        : sanitizeStoryAnalysisEvidence(value.storyAnalysis);
    const storyAnalysisShapeValid =
      value.storyAnalysis === null || storyAnalysis !== null;
    const flow = sanitizePackagedFlow(value.flow);
    const preexistingRelevantProcessesWereUnavailable =
      value.preexistingRelevantProcesses === null;
    const preexistingRelevantProcesses =
      preexistingRelevantProcessesWereUnavailable
        ? null
        : sanitizePreexistingProcesses(
            value.preexistingRelevantProcesses,
          );
    const actualOwnershipExitProof =
      Array.isArray(launches) &&
      launches.length === 2 &&
      launches.every(
        (launch, index) =>
          launch.launch === index + 1 &&
          launch.ownership.processes.some(
            (process) =>
              process.pid === launch.ownership.rootPid &&
              process.kind === "app",
          ) &&
          launch.ownership.processes.some(
            (process) => process.kind === "service",
          ) &&
          launch.exitProof.ownedPids.length > 0 &&
          launch.exitProof.ownedPids.includes(
            launch.ownership.rootPid,
          ) &&
          launch.exitProof.graceful &&
          launch.exitProof.forcedPids.length === 0 &&
          launch.exitProof.remainingPids.length === 0,
      );
    const commonContractValid =
      value.schemaVersion === PACKAGED_E2E_RESULT_SCHEMA_VERSION &&
      (value.status === "passed" || value.status === "failed") &&
      completedAt !== null &&
      value.packagedVersion === appVersion &&
      value.executable === expectedExecutable &&
      value.fixture ===
        "fixtures/synthetic-story/sample-story.docx.base64" &&
      equalStringArrays(value.isolationEnvironment, [
        "APPDATA",
        "LOCALAPPDATA",
        "TEMP",
        "TMP",
      ]) &&
      Array.isArray(flow) &&
      equalStringArrays(flow, PHASE_2_PACKAGED_FLOW) &&
      isPlainObject(value.screenshot) &&
      hasExactKeys(value.screenshot, ["artifactId", "captured"]) &&
      value.screenshot.artifactId === "packaged-ui-screenshot" &&
      typeof value.screenshot.captured === "boolean" &&
      importReviewShapeValid &&
      storyAnalysisShapeValid &&
      Array.isArray(launches) &&
      Array.isArray(completedLaunches) &&
      equalNumberArrays(
        completedLaunches,
        launches.map((launch) => launch.launch),
      ) &&
      (preexistingRelevantProcessesWereUnavailable ||
        Array.isArray(preexistingRelevantProcesses)) &&
      typeof value.applicationLaunchBegan === "boolean" &&
      typeof value.ownershipEstablished === "boolean" &&
      typeof value.cleanupCompleted === "boolean" &&
      (!value.ownershipEstablished ||
        value.applicationLaunchBegan) &&
      (launches.length === 0 ||
        (preexistingRelevantProcesses !== null &&
          equalPreexistingProcesses(
            preexistingRelevantProcesses,
            launches[0].preexistingRelevantProcesses,
          )));
    const passedContractValid =
      value.status === "passed" &&
      value.failureStage === null &&
      value.failureCode === null &&
      preexistingRelevantProcesses !== null &&
      value.applicationLaunchBegan === true &&
      value.ownershipEstablished === true &&
      value.cleanupCompleted === true &&
      value.screenshot.captured === true &&
      importReview !== null &&
      storyAnalysis !== null &&
      importReview.approvalPersistedAfterRestart &&
      importReview.extractionPersistedAfterRestart &&
      importReview.analysisPersistedAfterRestart &&
      equalNumberArrays(completedLaunches ?? [], [1, 2]) &&
      actualOwnershipExitProof;
    const failedContractValid =
      value.status === "failed" &&
      PACKAGED_FAILURE_STAGES.has(value.failureStage) &&
      PACKAGED_FAILURE_CODES.has(value.failureCode) &&
      validFailureCodeForStage(
        value.failureStage,
        value.failureCode,
      ) &&
      validFailedProgress({
        stage: value.failureStage,
        launches,
        preexistingRelevantProcesses,
        preexistingRelevantProcessesWereUnavailable,
        applicationLaunchBegan: value.applicationLaunchBegan,
        ownershipEstablished: value.ownershipEstablished,
        cleanupCompleted: value.cleanupCompleted,
        screenshotCaptured: value.screenshot.captured,
        actualOwnershipExitProof,
      });
    const contractValid =
      commonContractValid &&
      (passedContractValid || failedContractValid);
    if (!contractValid) {
      return invalidHarnessResult();
    }
    return {
      contractValid: true,
      reportedStatus: value.status,
      screenshotCaptured: value.screenshot.captured,
      completedAt,
      launches,
      ownershipExitProven: actualOwnershipExitProof,
      failureStage: value.failureStage,
      failureCode: value.failureCode,
      applicationLaunchBegan: value.applicationLaunchBegan,
      ownershipEstablished: value.ownershipEstablished,
      cleanupCompleted: value.cleanupCompleted,
      completedLaunches,
      importReview,
      storyAnalysis,
      flow,
    };
  } catch {
    return invalidHarnessResult();
  }
}

async function collectSyntheticVoiceCatalogEvidence(
  canonicalRoot,
  repositoryRoot,
) {
  const catalogPath = path.join(
    repositoryRoot,
    "apps",
    "local-service",
    "src",
    "cinematic_story_service",
    "catalogs",
    "synthetic_voice_catalog.v1.json",
  );
  await requiredFileEvidence(
    canonicalRoot,
    repositoryRoot,
    catalogPath,
    "synthetic voice catalog",
  );
  let catalog;
  try {
    catalog = JSON.parse(
      await readBoundedUtf8(catalogPath, "synthetic voice catalog"),
    );
  } catch {
    throw new Error("The synthetic voice catalog is invalid JSON.");
  }
  if (
    !isPlainObject(catalog) ||
    !hasExactKeys(catalog, [
      "contractVersion",
      "catalogRevision",
      "providers",
      "models",
      "voices",
      "rights",
      "fingerprint",
    ]) ||
    catalog.contractVersion !== VOICE_CASTING_CONTRACT_VERSION ||
    !isPlainObject(catalog.catalogRevision) ||
    catalog.catalogRevision.catalogRevisionId !==
      SYNTHETIC_VOICE_CATALOG_REVISION_ID ||
    catalog.catalogRevision.revision !== 1 ||
    catalog.catalogRevision.rightsPolicyId !==
      VOICE_RIGHTS_POLICY_ID ||
    catalog.catalogRevision.catalogFingerprint !==
      SYNTHETIC_VOICE_CATALOG_FINGERPRINT ||
    catalog.fingerprint !== SYNTHETIC_VOICE_CATALOG_FINGERPRINT ||
    !Array.isArray(catalog.providers) ||
    catalog.providers.length < 1 ||
    catalog.providers.length > 100 ||
    !Array.isArray(catalog.models) ||
    catalog.models.length < 1 ||
    catalog.models.length > 500 ||
    !Array.isArray(catalog.voices) ||
    catalog.voices.length < 14 ||
    catalog.voices.length > 5_000 ||
    !Array.isArray(catalog.rights) ||
    catalog.rights.length !== catalog.voices.length
  ) {
    throw new Error("The synthetic voice catalog envelope is invalid.");
  }

  const fingerprintInput = structuredClone(catalog);
  delete fingerprintInput.fingerprint;
  delete fingerprintInput.catalogRevision.catalogFingerprint;
  const computedFingerprint = sha256Bytes(
    Buffer.from(
      JSON.stringify(canonicalizeJsonValue(fingerprintInput)),
      "utf8",
    ),
  );
  if (computedFingerprint !== catalog.fingerprint) {
    throw new Error("The synthetic voice catalog fingerprint is invalid.");
  }

  const providers = catalog.providers.map((provider) => {
    if (
      !isPlainObject(provider) ||
      !isPhase3Id(provider.providerId) ||
      !isPhase3Id(provider.providerVersion)
    ) {
      throw new Error(
        "The synthetic voice provider descriptor evidence is invalid.",
      );
    }
    return {
      descriptorId: provider.providerId,
      version: provider.providerVersion,
    };
  });
  const models = catalog.models.map((model) => {
    if (
      !isPlainObject(model) ||
      !isPhase3Id(model.modelId) ||
      !isPhase3Id(model.modelVersion)
    ) {
      throw new Error(
        "The synthetic voice model descriptor evidence is invalid.",
      );
    }
    return {
      descriptorId: model.modelId,
      version: model.modelVersion,
    };
  });
  if (
    new Set(providers.map((item) => item.descriptorId)).size !==
      providers.length ||
    new Set(models.map((item) => item.descriptorId)).size !==
      models.length ||
    !equalStringArrays(
      catalog.catalogRevision.providerDescriptorIds,
      providers.map((item) => item.descriptorId),
    ) ||
    !equalStringArrays(
      catalog.catalogRevision.modelDescriptorIds,
      models.map((item) => item.descriptorId),
    )
  ) {
    throw new Error(
      "The synthetic voice catalog descriptor inventory is invalid.",
    );
  }
  const rightsByVoiceId = new Map();
  for (const rights of catalog.rights) {
    if (
      !isPlainObject(rights) ||
      !isPhase3Id(rights.voiceProfileId) ||
      !isPhase3Id(rights.rightsRecordId) ||
      !Number.isSafeInteger(rights.revision) ||
      rights.revision < 1 ||
      rightsByVoiceId.has(rights.voiceProfileId)
    ) {
      throw new Error(
        "The synthetic voice rights evidence is invalid.",
      );
    }
    rightsByVoiceId.set(rights.voiceProfileId, rights);
  }
  const assignmentEvidenceByVoiceId = new Map();
  for (const voice of catalog.voices) {
    const rights = rightsByVoiceId.get(voice?.voiceProfileId);
    if (
      !isPlainObject(voice) ||
      !isPhase3Id(voice.voiceProfileId) ||
      !isBoundedEvidenceText(voice.version, 80) ||
      !/^[0-9A-Za-z][0-9A-Za-z.+-]{0,79}$/u.test(voice.version) ||
      voice.catalogRevisionId !==
        catalog.catalogRevision.catalogRevisionId ||
      !isPlainObject(rights) ||
      rights.rightsRecordId !== voice.rightsRecordId ||
      rights.state !== voice.rightsState ||
      assignmentEvidenceByVoiceId.has(voice.voiceProfileId)
    ) {
      throw new Error(
        "The synthetic voice assignment evidence is invalid.",
      );
    }
    assignmentEvidenceByVoiceId.set(voice.voiceProfileId, {
      voiceProfileVersion: voice.version,
      voiceEvidenceFingerprint: sha256Bytes(
        Buffer.from(
          JSON.stringify(canonicalizeJsonValue(voice)),
          "utf8",
        ),
      ),
      rightsRecordId: rights.rightsRecordId,
      rightsRecordRevision: rights.revision,
      rightsEvidenceFingerprint: sha256Bytes(
        Buffer.from(
          JSON.stringify(canonicalizeJsonValue(rights)),
          "utf8",
        ),
      ),
      rightsState: rights.state,
    });
  }
  if (
    assignmentEvidenceByVoiceId.size !== catalog.voices.length ||
    rightsByVoiceId.size !== catalog.voices.length
  ) {
    throw new Error(
      "The synthetic voice assignment inventory is incomplete.",
    );
  }
  return {
    providers,
    models,
    catalogRevision: {
      catalogRevisionId: catalog.catalogRevision.catalogRevisionId,
      revision: catalog.catalogRevision.revision,
      fingerprint: catalog.fingerprint,
    },
    rightsPolicyId: catalog.catalogRevision.rightsPolicyId,
    assignmentEvidenceByVoiceId,
  };
}

async function inspectPhase3bEvidence(
  resultPath,
  resultEvidence,
  screenshotEvidence,
  harnessResult,
  phase3HarnessResult,
) {
  if (
    !resultEvidence.exists ||
    typeof resultEvidence.sizeBytes !== "number" ||
    resultEvidence.sizeBytes <= 0 ||
    resultEvidence.sizeBytes > MAX_HARNESS_RESULT_BYTES ||
    !screenshotEvidence.exists ||
    !harnessResult.contractValid ||
    harnessResult.reportedStatus !== "passed" ||
    !phase3HarnessResult.contractValid
  ) {
    return null;
  }
  try {
    const value = JSON.parse(await readFile(resultPath, "utf8"));
    const topKeys = [
      "schemaVersion",
      "completedAt",
      "status",
      "evidenceClassification",
      "fixtureClaims",
      "runtime",
      "fixtureProvider",
      "realProviderAdapter",
      "model",
      "pronunciation",
      "auditions",
      "cacheHit",
      "targetedInvalidation",
      "gateDecisions",
      "restart",
      "process",
      "screenshot",
      "assertions",
    ];
    const completedAt =
      typeof value?.completedAt === "string"
        ? normalizeUtcTimestamp(value.completedAt)
        : null;
    if (
      !isPlainObject(value) ||
      !hasExactKeys(value, topKeys) ||
      value.schemaVersion !==
        PHASE_3B_PACKAGED_E2E_RESULT_SCHEMA_VERSION ||
      value.status !== "passed" ||
      value.evidenceClassification !==
        PHASE_3B_EVIDENCE_CLASSIFICATION ||
      completedAt === null ||
      completedAt !== harnessResult.completedAt ||
      completedAt !== phase3HarnessResult.completedAt ||
      !validPhase3bFixtureClaims(value.fixtureClaims) ||
      !validPhase3bRuntime(value.runtime) ||
      !validPhase3bProvider(value.fixtureProvider) ||
      !validPhase3bProvider(value.realProviderAdapter) ||
      !validPhase3bModel(value.model) ||
      !validPhase3bPronunciation(value.pronunciation) ||
      !validPhase3bRestart(value.restart) ||
      !validPhase3bScreenshot(value.screenshot, screenshotEvidence) ||
      !isAllTrueRecord(value.assertions, PHASE_3B_ASSERTION_KEYS) ||
      containsPrivatePhase3bEvidence(value)
    ) {
      return null;
    }
    const auditions = sanitizePhase3bAuditions(value.auditions);
    if (
      auditions === null ||
      !validPhase3bCacheHit(value.cacheHit, auditions) ||
      !validPhase3bTargetedInvalidation(
        value.targetedInvalidation,
        auditions,
        value.pronunciation,
      ) ||
      !validPhase3bGateDecisions(value.gateDecisions) ||
      !validPhase3bProcessProof(
        value.process,
        harnessResult.launches,
        value.restart.priorLaunchRuntimeExit,
        value.runtime.runtimeInstanceIds,
      )
    ) {
      return null;
    }
    return {
      evidenceFile: resultEvidence,
      schemaVersion: value.schemaVersion,
      completedAt,
      evidenceClassification: value.evidenceClassification,
      fixtureClaims: structuredClone(value.fixtureClaims),
      runtime: structuredClone(value.runtime),
      fixtureProvider: structuredClone(value.fixtureProvider),
      realProviderAdapter: structuredClone(value.realProviderAdapter),
      model: structuredClone(value.model),
      pronunciation: structuredClone(value.pronunciation),
      auditions,
      cacheHit: structuredClone(value.cacheHit),
      targetedInvalidation: structuredClone(value.targetedInvalidation),
      gateDecisions: structuredClone(value.gateDecisions),
      restart: structuredClone(value.restart),
      process: structuredClone(value.process),
      screenshot: structuredClone(value.screenshot),
      assertions: structuredClone(value.assertions),
    };
  } catch {
    return null;
  }
}

function validPhase3bFixtureClaims(value) {
  return (
    isPlainObject(value) &&
    hasExactKeys(value, [
      "lifecycleEvidenceOnly",
      "naturalSpeechQualityProven",
      "productionExportEligible",
      "humanListeningClaimed",
    ]) &&
    value.lifecycleEvidenceOnly === true &&
    value.naturalSpeechQualityProven === false &&
    value.productionExportEligible === false &&
    value.humanListeningClaimed === false
  );
}

function validPhase3bRuntime(value) {
  return (
    isPlainObject(value) &&
    hasExactKeys(value, [
      "profileId",
      "profileFingerprint",
      "protocolVersion",
      "runtimeInstanceIds",
      "networkPolicy",
      "observedNetworkRequestCount",
      "externalNetworkObservation",
    ]) &&
    isPhase3Id(value.profileId) &&
    isSha256(value.profileFingerprint) &&
    value.protocolVersion === "1.0.0" &&
    isUniquePhase3IdArray(value.runtimeInstanceIds, 1, 10) &&
    value.networkPolicy === "python_socket_api_denied" &&
    value.observedNetworkRequestCount === null &&
    isPlainObject(value.externalNetworkObservation) &&
    hasExactKeys(value.externalNetworkObservation, [
      "method",
      "ownedPidsOnly",
      "observedNonLoopbackEndpointCount",
    ]) &&
    value.externalNetworkObservation.method ===
      "owned_pid_tcp_endpoint_inventory" &&
    value.externalNetworkObservation.ownedPidsOnly === true &&
    value.externalNetworkObservation.observedNonLoopbackEndpointCount === 0
  );
}

function validPhase3bProvider(value) {
  return (
    isPlainObject(value) &&
    hasExactKeys(value, ["providerId", "providerVersion"]) &&
    isPhase3Id(value.providerId) &&
    isPhase3Id(value.providerVersion)
  );
}

function validPhase3bModel(value) {
  return (
    isPlainObject(value) &&
    hasExactKeys(value, [
      "modelPackageId",
      "manifestVersion",
      "modelPackageFingerprint",
      "installationRevision",
      "verificationId",
      "verificationFingerprint",
      "verified",
      "active",
    ]) &&
    isPhase3Id(value.modelPackageId) &&
    isPhase3Id(value.manifestVersion) &&
    isSha256(value.modelPackageFingerprint) &&
    isBoundedPositiveInteger(value.installationRevision) &&
    isPhase3Id(value.verificationId) &&
    isSha256(value.verificationFingerprint) &&
    value.verified === true &&
    value.active === true
  );
}

function validPhase3bPronunciation(value) {
  return (
    isPlainObject(value) &&
    hasExactKeys(value, [
      "dictionaryId",
      "initialRevision",
      "initialFingerprint",
      "initialEntryId",
      "initialEntryFingerprint",
      "initialDecision",
      "supersedingEntryId",
      "supersedingEntryFingerprint",
      "supersedesEntryId",
      "supersedingDecision",
      "finalRevision",
      "finalFingerprint",
    ]) &&
    isPhase3Id(value.dictionaryId) &&
    isBoundedPositiveInteger(value.initialRevision) &&
    isSha256(value.initialFingerprint) &&
    isPhase3Id(value.initialEntryId) &&
    isSha256(value.initialEntryFingerprint) &&
    value.initialDecision === "approved" &&
    isPhase3Id(value.supersedingEntryId) &&
    value.supersedingEntryId !== value.initialEntryId &&
    isSha256(value.supersedingEntryFingerprint) &&
    value.supersedesEntryId === value.initialEntryId &&
    value.supersedingDecision === "approved" &&
    isBoundedPositiveInteger(value.finalRevision) &&
    value.finalRevision > value.initialRevision &&
    isSha256(value.finalFingerprint) &&
    value.finalFingerprint !== value.initialFingerprint
  );
}

function sanitizePhase3bAuditions(value) {
  if (!Array.isArray(value) || value.length < 3 || value.length > 20) {
    return null;
  }
  const auditions = [];
  const narratorRoles = new Set();
  const characterRoles = new Set();
  const seenClipIds = new Set();
  for (const item of value) {
    if (
      !isPlainObject(item) ||
      !hasExactKeys(item, [
        "roleId",
        "roleType",
        "assignmentId",
        "assignmentRevision",
        "voiceRuntimeBindingId",
        "voiceRuntimeBindingFingerprint",
        "providerVoiceId",
        "auditionSessionId",
        "providerRequestId",
        "requestFingerprint",
        "executionClassification",
        "providerDispatchCount",
        "sourceProviderRequestId",
        "runtimeInstanceId",
        "normalizedTextSha256",
        "pronunciationPlanFingerprint",
        "cacheKey",
        "cacheStatus",
        "auditionClipId",
        "clipFingerprint",
        "audioArtifactId",
        "audio",
        "authenticatedAudioLoaded",
        "fixtureEvidenceOnly",
      ]) ||
      !isPhase3Id(item.roleId) ||
      (item.roleType !== "narrator" && item.roleType !== "character") ||
      !isPhase3Id(item.assignmentId) ||
      !isBoundedPositiveInteger(item.assignmentRevision) ||
      !isPhase3Id(item.voiceRuntimeBindingId) ||
      !isSha256(item.voiceRuntimeBindingFingerprint) ||
      !isPhase3Id(item.providerVoiceId) ||
      !isPhase3Id(item.auditionSessionId) ||
      !isPhase3Id(item.providerRequestId) ||
      !isSha256(item.requestFingerprint) ||
      (item.cacheStatus === "miss" &&
        (item.executionClassification !== "provider_execution" ||
          item.providerDispatchCount !== 1 ||
          item.sourceProviderRequestId !== null ||
          !isPhase3Id(item.runtimeInstanceId))) ||
      (item.cacheStatus === "verified_hit" &&
        (item.executionClassification !== "verified_cache_lookup" ||
          item.providerDispatchCount !== 0 ||
          !isPhase3Id(item.sourceProviderRequestId) ||
          item.sourceProviderRequestId === item.providerRequestId ||
          item.runtimeInstanceId !== null)) ||
      !isSha256(item.normalizedTextSha256) ||
      !isSha256(item.pronunciationPlanFingerprint) ||
      !isSha256(item.cacheKey) ||
      (item.cacheStatus !== "miss" &&
        item.cacheStatus !== "verified_hit") ||
      !isPhase3Id(item.auditionClipId) ||
      seenClipIds.has(item.auditionClipId) ||
      !isSha256(item.clipFingerprint) ||
      !isPhase3Id(item.audioArtifactId) ||
      !validPhase3bAudio(item.audio) ||
      item.authenticatedAudioLoaded !== true ||
      item.fixtureEvidenceOnly !== true
    ) {
      return null;
    }
    seenClipIds.add(item.auditionClipId);
    (item.roleType === "narrator"
      ? narratorRoles
      : characterRoles
    ).add(item.roleId);
    auditions.push(structuredClone(item));
  }
  return narratorRoles.size >= 1 && characterRoles.size >= 2
    ? auditions
    : null;
}

function validPhase3bAudio(value) {
  return (
    isPlainObject(value) &&
    hasExactKeys(value, [
      "mediaType",
      "codec",
      "sampleRateHz",
      "channels",
      "sampleWidthBytes",
      "durationMilliseconds",
      "byteSize",
      "sha256",
      "nonSilencePassed",
      "clippingPassed",
      "blockingFindingCount",
    ]) &&
    value.mediaType === "audio/wav" &&
    value.codec === "pcm_s16le" &&
    value.sampleRateHz === 24_000 &&
    value.channels === 1 &&
    value.sampleWidthBytes === 2 &&
    isBoundedPositiveInteger(value.durationMilliseconds) &&
    value.durationMilliseconds <= 30_000 &&
    isBoundedPositiveInteger(value.byteSize) &&
    value.byteSize <= 24 * 1024 * 1024 &&
    isSha256(value.sha256) &&
    value.nonSilencePassed === true &&
    value.clippingPassed === true &&
    value.blockingFindingCount === 0
  );
}

function validPhase3bCacheHit(value, auditions) {
  if (
    !isPlainObject(value) ||
    !hasExactKeys(value, [
      "originalClipId",
      "repeatedClipId",
      "originalRequestFingerprint",
      "repeatedRequestFingerprint",
      "originalCacheKey",
      "repeatedCacheKey",
      "artifactSha256",
      "repeatedArtifactSha256",
      "repeatedCacheStatus",
      "identicalCacheKeyInputsProven",
      "lookupOnlyNoProviderExecutionProven",
    ]) ||
    !isPhase3Id(value.originalClipId) ||
    !isPhase3Id(value.repeatedClipId) ||
    value.originalClipId === value.repeatedClipId ||
    !isSha256(value.originalRequestFingerprint) ||
    !isSha256(value.repeatedRequestFingerprint) ||
    value.originalRequestFingerprint === value.repeatedRequestFingerprint ||
    !isSha256(value.originalCacheKey) ||
    value.repeatedCacheKey !== value.originalCacheKey ||
    !isSha256(value.artifactSha256) ||
    value.repeatedArtifactSha256 !== value.artifactSha256 ||
    value.repeatedCacheStatus !== "verified_hit" ||
    value.identicalCacheKeyInputsProven !== true ||
    value.lookupOnlyNoProviderExecutionProven !== true
  ) {
    return false;
  }
  const original = auditions.find(
    (audition) => audition.auditionClipId === value.originalClipId,
  );
  const repeated = auditions.find(
    (audition) => audition.auditionClipId === value.repeatedClipId,
  );
  return (
    original !== undefined &&
    repeated !== undefined &&
    original.roleId === repeated.roleId &&
    original.voiceRuntimeBindingId === repeated.voiceRuntimeBindingId &&
    original.voiceRuntimeBindingFingerprint ===
      repeated.voiceRuntimeBindingFingerprint &&
    original.providerVoiceId === repeated.providerVoiceId &&
    original.requestFingerprint === value.originalRequestFingerprint &&
    original.executionClassification === "provider_execution" &&
    original.providerDispatchCount === 1 &&
    original.sourceProviderRequestId === null &&
    isPhase3Id(original.runtimeInstanceId) &&
    original.cacheKey === value.originalCacheKey &&
    original.audio.sha256 === value.artifactSha256 &&
    repeated.requestFingerprint === value.repeatedRequestFingerprint &&
    repeated.executionClassification === "verified_cache_lookup" &&
    repeated.providerDispatchCount === 0 &&
    repeated.sourceProviderRequestId === original.providerRequestId &&
    repeated.runtimeInstanceId === null &&
    repeated.cacheKey === value.repeatedCacheKey &&
    repeated.cacheStatus === "verified_hit" &&
    repeated.audio.sha256 === value.repeatedArtifactSha256
  );
}

function validPhase3bRegeneratedBinding(value, auditions) {
  if (!Array.isArray(value.invalidatedClipIds)) return false;
  const prior = auditions.find(
    (audition) =>
      audition.roleId === value.impactedRoleId &&
      value.invalidatedClipIds.includes(audition.auditionClipId) &&
      audition.requestFingerprint === value.priorRequestFingerprint &&
      audition.cacheKey === value.priorCacheKey &&
      audition.audio.sha256 === value.priorArtifactSha256,
  );
  const regenerated = auditions.find(
    (audition) =>
      audition.roleId === value.impactedRoleId &&
      !value.invalidatedClipIds.includes(audition.auditionClipId) &&
      audition.cacheStatus === "miss" &&
      audition.requestFingerprint === value.regeneratedRequestFingerprint &&
      audition.cacheKey === value.regeneratedCacheKey &&
      audition.audio.sha256 === value.regeneratedArtifactSha256,
  );
  return (
    prior !== undefined &&
    regenerated !== undefined &&
    prior.voiceRuntimeBindingId === regenerated.voiceRuntimeBindingId &&
    prior.voiceRuntimeBindingFingerprint ===
      regenerated.voiceRuntimeBindingFingerprint &&
    prior.providerVoiceId === regenerated.providerVoiceId
  );
}

function validPhase3bTargetedInvalidation(
  value,
  auditions,
  pronunciation,
) {
  if (
    !isPlainObject(value) ||
    !hasExactKeys(value, [
      "supersededEntryId",
      "supersedingEntryId",
      "beforeDictionaryFingerprint",
      "afterDictionaryFingerprint",
      "impactedRoleId",
      "priorRequestFingerprint",
      "regeneratedRequestFingerprint",
      "priorCacheKey",
      "regeneratedCacheKey",
      "priorArtifactSha256",
      "regeneratedArtifactSha256",
      "invalidatedClipIds",
      "preservedClipIds",
      "persistedInvalidatedGateStates",
      "targetedOnly",
    ]) ||
    value.supersededEntryId !== pronunciation.initialEntryId ||
    value.supersedingEntryId !== pronunciation.supersedingEntryId ||
    value.beforeDictionaryFingerprint !==
      pronunciation.initialFingerprint ||
    value.afterDictionaryFingerprint !== pronunciation.finalFingerprint ||
    !validPhase3bRegeneratedBinding(value, auditions) ||
    !isSha256(value.priorRequestFingerprint) ||
    !isSha256(value.regeneratedRequestFingerprint) ||
    value.priorRequestFingerprint ===
      value.regeneratedRequestFingerprint ||
    !isSha256(value.priorCacheKey) ||
    !isSha256(value.regeneratedCacheKey) ||
    value.priorCacheKey === value.regeneratedCacheKey ||
    !isSha256(value.priorArtifactSha256) ||
    !isSha256(value.regeneratedArtifactSha256) ||
    value.priorArtifactSha256 === value.regeneratedArtifactSha256 ||
    !isUniquePhase3IdArray(value.invalidatedClipIds, 1, 2_000) ||
    !isUniquePhase3IdArray(value.preservedClipIds, 1, 2_000) ||
    !validPersistedInvalidatedGateStates(
      value.persistedInvalidatedGateStates,
    ) ||
    value.invalidatedClipIds.some((id) =>
      value.preservedClipIds.includes(id),
    ) ||
    value.invalidatedClipIds.some(
      (clipId) =>
        !auditions.some(
          (audition) =>
            audition.auditionClipId === clipId &&
            audition.roleId === value.impactedRoleId,
        ),
    ) ||
    value.preservedClipIds.some(
      (clipId) =>
        !auditions.some(
          (audition) => audition.auditionClipId === clipId,
        ),
    ) ||
    auditions.some(
      (audition) =>
        audition.requestFingerprint === value.regeneratedRequestFingerprint &&
        value.preservedClipIds.includes(audition.auditionClipId),
    ) ||
    value.targetedOnly !== true
  ) {
    return false;
  }
  return true;
}

function validPersistedInvalidatedGateStates(value) {
  if (!Array.isArray(value) || value.length !== 2) {
    return false;
  }
  const expectedGateIds = new Set([
    "pronunciation_review",
    "voice_readiness_review",
  ]);
  const observedGateIds = new Set();
  for (const item of value) {
    if (
      !isPlainObject(item) ||
      !hasExactKeys(item, [
        "gateId",
        "reviewId",
        "state",
        "evidenceFingerprint",
      ]) ||
      !expectedGateIds.has(item.gateId) ||
      observedGateIds.has(item.gateId) ||
      !isPhase3Id(item.reviewId) ||
      !["blocked", "pending", "invalidated"].includes(item.state) ||
      !isSha256(item.evidenceFingerprint)
    ) {
      return false;
    }
    observedGateIds.add(item.gateId);
  }
  return [...expectedGateIds].every((gateId) =>
    observedGateIds.has(gateId)
  );
}

function validPhase3bGateDecisions(value) {
  if (!Array.isArray(value) || value.length < 5 || value.length > 304) {
    return false;
  }
  const gateIds = new Set();
  const decisionIds = new Set();
  for (const item of value) {
    if (
      !isPlainObject(item) ||
      !hasExactKeys(item, [
        "gateId",
        "reviewId",
        "decisionId",
        "roleId",
        "evidenceFingerprint",
        "decision",
        "immutable",
      ]) ||
      !PHASE_3B_GATE_IDS.includes(item.gateId) ||
      !isPhase3Id(item.reviewId) ||
      !isPhase3Id(item.decisionId) ||
      decisionIds.has(item.decisionId) ||
      (item.roleId !== null && !isPhase3Id(item.roleId)) ||
      !isSha256(item.evidenceFingerprint) ||
      item.decision !== "approved" ||
      item.immutable !== true
    ) {
      return false;
    }
    gateIds.add(item.gateId);
    decisionIds.add(item.decisionId);
  }
  return PHASE_3B_GATE_IDS.every((gateId) => gateIds.has(gateId));
}

function validPhase3bRestart(value) {
  const trueKeys = [
    "runtimeProfilePersisted",
    "modelVerificationPersisted",
    "pronunciationDictionaryPersisted",
    "pronunciationDecisionsPersisted",
    "auditionSessionsPersisted",
    "auditionScriptsPersisted",
    "auditionClipsPersisted",
    "cacheRecordsPersisted",
    "audioQualityRecordsPersisted",
    "auditionDecisionsPersisted",
    "voiceReadinessDecisionPersisted",
    "authenticatedRestoredAudioLoaded",
  ];
  return (
    isPlainObject(value) &&
    hasExactKeys(value, [...trueKeys, "priorLaunchRuntimeExit"]) &&
    trueKeys.every((key) => value[key] === true) &&
    validPhase3bRuntimeExit(value.priorLaunchRuntimeExit)
  );
}

function validPhase3bRuntimeExit(value) {
  return (
    isPlainObject(value) &&
    hasExactKeys(value, [
      "runtimeInstanceId",
      "workerPid",
      "parentPid",
      "state",
      "stoppedAt",
      "stopReasonCode",
      "handshakeAuthenticated",
      "shutdownAcknowledged",
      "gracefulShutdownConfirmed",
      "exitCode",
      "terminatedByParent",
      "ownershipConfirmed",
      "confirmedExited",
      "ownedProcessesConfirmedExited",
      "jobObjectAssigned",
      "deniedNetworkAttemptCount",
    ]) &&
    isPhase3Id(value.runtimeInstanceId) &&
    isBoundedPositiveInteger(value.workerPid) &&
    isBoundedPositiveInteger(value.parentPid) &&
    value.workerPid !== value.parentPid &&
    value.state === "stopped" &&
    normalizeUtcTimestamp(value.stoppedAt) !== null &&
    value.stopReasonCode === "clean" &&
    value.handshakeAuthenticated === true &&
    value.shutdownAcknowledged === true &&
    value.gracefulShutdownConfirmed === true &&
    value.exitCode === 0 &&
    value.terminatedByParent === false &&
    value.ownershipConfirmed === true &&
    value.confirmedExited === true &&
    value.ownedProcessesConfirmedExited === true &&
    value.jobObjectAssigned === true &&
    value.deniedNetworkAttemptCount === 0
  );
}

function validPhase3bProcessProof(
  value,
  launches,
  priorLaunchRuntimeExit,
  runtimeInstanceIds,
) {
  if (
    !isPlainObject(value) ||
    !hasExactKeys(value, ["launches"]) ||
    !Array.isArray(value.launches) ||
    value.launches.length !== 2 ||
    !Array.isArray(launches) ||
    launches.length !== 2
  ) {
    return false;
  }
  const exitInstanceIds = new Set();
  return value.launches.every((item, index) => {
    const base = launches[index];
    if (
      !isPlainObject(item) ||
      !hasExactKeys(item, [
        "launch",
        "ownedProcesses",
        "providerRuntimeExit",
        "forcedPids",
        "remainingPids",
        "unrelatedProcessesInspected",
        "unrelatedProcessesTerminated",
      ]) ||
      item.launch !== index + 1 ||
      !Array.isArray(item.ownedProcesses) ||
      !Array.isArray(item.forcedPids) ||
      item.forcedPids.length !== 0 ||
      !Array.isArray(item.remainingPids) ||
      item.remainingPids.length !== 0 ||
      item.unrelatedProcessesInspected !== false ||
      item.unrelatedProcessesTerminated !== false ||
      base === undefined ||
      base.exitProof.graceful !== true ||
      base.exitProof.forcedPids.length !== 0 ||
      base.exitProof.remainingPids.length !== 0
    ) {
      return false;
    }
    const expected = base.ownership.processes.map((process) => ({
      pid: process.pid,
      parentPid: process.parentPid,
      kind: process.kind === "app" ? "electron" : process.kind,
      executableName: process.executableName,
      creationIdentity: process.creationDate,
      goneAfterShutdown: true,
    }));
    const runtimeExit = item.providerRuntimeExit;
    if (
      !validPhase3bRuntimeExit(runtimeExit) ||
      !Array.isArray(runtimeInstanceIds) ||
      !runtimeInstanceIds.includes(runtimeExit.runtimeInstanceId) ||
      exitInstanceIds.has(runtimeExit.runtimeInstanceId) ||
      !expected.some(
        (process) =>
          process.kind === "provider_worker" &&
          process.pid === runtimeExit.workerPid &&
          process.parentPid === runtimeExit.parentPid,
      ) ||
      !expected.some(
        (process) =>
          process.kind === "service" &&
          process.pid === runtimeExit.parentPid,
      ) ||
      (index === 0 &&
        !jsonValuesEqual(runtimeExit, priorLaunchRuntimeExit))
    ) {
      return false;
    }
    exitInstanceIds.add(runtimeExit.runtimeInstanceId);
    return (
      expected.some((process) => process.kind === "electron") &&
      expected.some((process) => process.kind === "service") &&
      expected.some((process) => process.kind === "provider_worker") &&
      jsonValuesEqual(item.ownedProcesses, expected)
    );
  });
}

function validPhase3bScreenshot(value, screenshotEvidence) {
  return (
    isPlainObject(value) &&
    hasExactKeys(value, ["artifactId", "captured"]) &&
    value.artifactId === "packaged-ui-screenshot" &&
    value.captured === true &&
    screenshotEvidence.exists === true &&
    typeof screenshotEvidence.sizeBytes === "number" &&
    screenshotEvidence.sizeBytes > 0
  );
}

function isAllTrueRecord(value, keys) {
  return (
    isPlainObject(value) &&
    hasExactKeys(value, keys) &&
    keys.every((key) => value[key] === true)
  );
}

function isBoundedPositiveInteger(value) {
  return (
    Number.isSafeInteger(value) &&
    value >= 1 &&
    value <= 1_000_000_000
  );
}

function containsPrivatePhase3bEvidence(value) {
  const serialized = JSON.stringify(value);
  return (
    /(?:[A-Za-z]:\\\\|\\\\\\\\|\/Users\/|\/home\/)/u.test(serialized) ||
    /"(?:text|script|manuscript|absolutePath|filePath|token|credential)"\s*:/iu.test(
      serialized,
    )
  );
}

async function inspectPhase3HarnessResult(
  resultPath,
  resultEvidence,
  screenshotEvidence,
  phase2HarnessResult,
) {
  const invalid = invalidPhase3HarnessResult();
  if (
    !resultEvidence.exists ||
    typeof resultEvidence.sizeBytes !== "number" ||
    resultEvidence.sizeBytes <= 0 ||
    resultEvidence.sizeBytes > MAX_HARNESS_RESULT_BYTES
  ) {
    return invalid;
  }
  try {
    const value = JSON.parse(await readFile(resultPath, "utf8"));
    if (
      !isPlainObject(value) ||
      !hasExactKeys(value, [
        "schemaVersion",
        "contractVersion",
        "result",
        "fixture",
        "flow",
        "casting",
        "screenshot",
        "processOwnership",
        "assertions",
        "completedAt",
      ]) ||
      value.schemaVersion !==
        PHASE_3_PACKAGED_E2E_RESULT_SCHEMA_VERSION ||
      value.contractVersion !== VOICE_CASTING_CONTRACT_VERSION ||
      value.result !== "passed" ||
      value.fixture !==
        "fixtures/synthetic-story/sample-story.docx.base64" ||
      !equalStringArrays(value.flow, PHASE_3_PACKAGED_FLOW)
    ) {
      return invalid;
    }
    const casting = sanitizePhase3CastingProof(value.casting);
    const screenshot = sanitizePhase3Screenshot(value.screenshot);
    const processOwnership = sanitizePhase3ProcessOwnership(
      value.processOwnership,
    );
    const assertions = sanitizePhase3Assertions(value.assertions);
    const completedAt = normalizeUtcTimestamp(value.completedAt);
    const expectedElectronPids = sortedUniquePids(
      phase2HarnessResult.launches.flatMap((launch) =>
        launch.ownership.processes
          .filter((process) => process.kind === "app")
          .map((process) => process.pid),
      ),
    );
    const expectedServicePids = sortedUniquePids(
      phase2HarnessResult.launches.flatMap((launch) =>
        launch.ownership.processes
          .filter((process) => process.kind === "service")
          .map((process) => process.pid),
      ),
    );
    const contractValid =
      casting !== null &&
      screenshot !== null &&
      processOwnership !== null &&
      assertions !== null &&
      phase2HarnessResult.contractValid === true &&
      phase2HarnessResult.reportedStatus === "passed" &&
      phase2HarnessResult.ownershipExitProven === true &&
      phase2HarnessResult.completedAt === completedAt &&
      screenshotEvidence.exists === true &&
      screenshot.relativePath === screenshotEvidence.path &&
      screenshot.byteSize === screenshotEvidence.sizeBytes &&
      screenshot.sha256 === screenshotEvidence.sha256 &&
      Array.isArray(expectedElectronPids) &&
      expectedElectronPids.length > 0 &&
      equalNumberArrays(
        processOwnership.electronOwnedPids,
        expectedElectronPids,
      ) &&
      Array.isArray(expectedServicePids) &&
      expectedServicePids.length > 0 &&
      equalNumberArrays(
        processOwnership.serviceOwnedPids,
        expectedServicePids,
      ) &&
      phase3LaunchShutdownsMatch(
        processOwnership,
        phase2HarnessResult.launches,
      );
    if (!contractValid) {
      return invalid;
    }
    return {
      contractValid: true,
      completedAt,
      value: {
        schemaVersion: value.schemaVersion,
        contractVersion: value.contractVersion,
        result: value.result,
        fixture: value.fixture,
        flow: [...PHASE_3_PACKAGED_FLOW],
        casting,
        screenshot,
        processOwnership,
        assertions,
        completedAt,
      },
    };
  } catch {
    return invalid;
  }
}

function invalidPhase3HarnessResult() {
  return {
    contractValid: false,
    completedAt: null,
    value: null,
  };
}

function sanitizePhase3CastingProof(value) {
  const narratorAssignment = sanitizePhase3RoleVoiceAssignment(
    value?.narratorAssignment,
    PHASE_3_NARRATOR_ROLE_TYPES,
  );
  const characterAssignments = sanitizePhase3RoleVoiceAssignments(
    value?.characterAssignments,
    PHASE_3_CHARACTER_ROLE_TYPES,
    2,
    300,
  );
  if (
    !isPlainObject(value) ||
    !hasExactKeys(value, [
      "profileId",
      "profileFingerprint",
      "catalogRevisionId",
      "catalogFingerprint",
      "castingRunId",
      "approvedCastSnapshotId",
      "approvedCastSnapshotRevision",
      "narratorAssignmentId",
      "characterAssignmentIds",
      "narratorAssignment",
      "characterAssignments",
      "conflictId",
      "conflictDispositionCorrectionId",
      "restrictedRightsWarningId",
      "ineligibleApprovalRejected",
      "gateDecisionIds",
      "restartRestored",
    ]) ||
    value.profileId !== GOVERNED_VOICE_CASTING_PROFILE_ID ||
    value.profileFingerprint !==
      GOVERNED_VOICE_CASTING_PROFILE_FINGERPRINT ||
    value.catalogRevisionId !== SYNTHETIC_VOICE_CATALOG_REVISION_ID ||
    value.catalogFingerprint !==
      SYNTHETIC_VOICE_CATALOG_FINGERPRINT ||
    !isPhase3Id(value.castingRunId) ||
    !isPhase3Id(value.approvedCastSnapshotId) ||
    !Number.isSafeInteger(value.approvedCastSnapshotRevision) ||
    value.approvedCastSnapshotRevision < 1 ||
    !isPhase3Id(value.narratorAssignmentId) ||
    !isUniquePhase3IdArray(value.characterAssignmentIds, 2, 300) ||
    value.characterAssignmentIds.includes(value.narratorAssignmentId) ||
    narratorAssignment === null ||
    characterAssignments === null ||
    narratorAssignment.assignmentId !== value.narratorAssignmentId ||
    !equalStringArrays(
      characterAssignments.map(
        (assignment) => assignment.assignmentId,
      ),
      value.characterAssignmentIds,
    ) ||
    narratorAssignment.rightsState !== "restricted" ||
    new Set([
      narratorAssignment.roleId,
      ...characterAssignments.map(
        (assignment) => assignment.roleId,
      ),
    ]).size !==
      1 + characterAssignments.length ||
    !isPhase3Id(value.conflictId) ||
    !isPhase3Id(value.conflictDispositionCorrectionId) ||
    !isPhase3Id(value.restrictedRightsWarningId) ||
    value.ineligibleApprovalRejected !== true ||
    value.restartRestored !== true ||
    !isPlainObject(value.gateDecisionIds) ||
    !hasExactKeys(value.gateDecisionIds, [
      "narratorCastingReview",
      "characterCastingReview",
      "completeCastReview",
    ]) ||
    !Object.values(value.gateDecisionIds).every(isPhase3Id) ||
    new Set(Object.values(value.gateDecisionIds)).size !== 3
  ) {
    return null;
  }
  return {
    profileId: value.profileId,
    profileFingerprint: value.profileFingerprint,
    catalogRevisionId: value.catalogRevisionId,
    catalogFingerprint: value.catalogFingerprint,
    castingRunId: value.castingRunId,
    approvedCastSnapshotId: value.approvedCastSnapshotId,
    approvedCastSnapshotRevision: value.approvedCastSnapshotRevision,
    narratorAssignmentId: value.narratorAssignmentId,
    characterAssignmentIds: [...value.characterAssignmentIds],
    narratorAssignment,
    characterAssignments,
    conflictId: value.conflictId,
    conflictDispositionCorrectionId:
      value.conflictDispositionCorrectionId,
    restrictedRightsWarningId: value.restrictedRightsWarningId,
    ineligibleApprovalRejected: true,
    gateDecisionIds: {
      narratorCastingReview:
        value.gateDecisionIds.narratorCastingReview,
      characterCastingReview:
        value.gateDecisionIds.characterCastingReview,
      completeCastReview: value.gateDecisionIds.completeCastReview,
    },
    restartRestored: true,
  };
}

function sanitizePhase3RoleVoiceAssignments(
  value,
  allowedRoleTypes,
  minimum,
  maximum,
) {
  if (
    !Array.isArray(value) ||
    value.length < minimum ||
    value.length > maximum
  ) {
    return null;
  }
  const assignments = value.map((assignment) =>
    sanitizePhase3RoleVoiceAssignment(
      assignment,
      allowedRoleTypes,
    ),
  );
  if (
    assignments.some((assignment) => assignment === null) ||
    new Set(
      assignments.map((assignment) => assignment.assignmentId),
    ).size !== assignments.length ||
    new Set(
      assignments.map((assignment) => assignment.roleId),
    ).size !== assignments.length
  ) {
    return null;
  }
  return assignments;
}

function sanitizePhase3RoleVoiceAssignment(
  value,
  allowedRoleTypes,
) {
  if (
    !isPlainObject(value) ||
    !hasExactKeys(value, [
      "assignmentId",
      "roleId",
      "roleType",
      "voiceProfileId",
      "voiceProfileVersion",
      "voiceEvidenceFingerprint",
      "rightsRecordId",
      "rightsRecordRevision",
      "rightsEvidenceFingerprint",
      "catalogRevisionId",
      "castingProfileFingerprint",
      "phase2SnapshotFingerprint",
      "effectiveCorrectionSetFingerprint",
      "authority",
      "rightsState",
      "revision",
      "supersedesAssignmentId",
    ]) ||
    !isPhase3Id(value.assignmentId) ||
    !isPhase3Id(value.roleId) ||
    !allowedRoleTypes.includes(value.roleType) ||
    !isPhase3Id(value.voiceProfileId) ||
    !isBoundedEvidenceText(value.voiceProfileVersion, 80) ||
    !/^[0-9A-Za-z][0-9A-Za-z.+-]{0,79}$/u.test(
      value.voiceProfileVersion,
    ) ||
    !isSha256(value.voiceEvidenceFingerprint) ||
    !isPhase3Id(value.rightsRecordId) ||
    !Number.isSafeInteger(value.rightsRecordRevision) ||
    value.rightsRecordRevision < 1 ||
    !isSha256(value.rightsEvidenceFingerprint) ||
    value.catalogRevisionId !== SYNTHETIC_VOICE_CATALOG_REVISION_ID ||
    value.castingProfileFingerprint !==
      GOVERNED_VOICE_CASTING_PROFILE_FINGERPRINT ||
    !isSha256(value.phase2SnapshotFingerprint) ||
    !isSha256(value.effectiveCorrectionSetFingerprint) ||
    value.authority !== "human_locked" ||
    !PHASE_3_APPROVED_ASSIGNMENT_RIGHTS_STATES.includes(
      value.rightsState,
    ) ||
    !Number.isSafeInteger(value.revision) ||
    value.revision < 1 ||
    !isPhase3Id(value.supersedesAssignmentId) ||
    value.supersedesAssignmentId === value.assignmentId
  ) {
    return null;
  }
  return {
    assignmentId: value.assignmentId,
    roleId: value.roleId,
    roleType: value.roleType,
    voiceProfileId: value.voiceProfileId,
    voiceProfileVersion: value.voiceProfileVersion,
    voiceEvidenceFingerprint: value.voiceEvidenceFingerprint,
    rightsRecordId: value.rightsRecordId,
    rightsRecordRevision: value.rightsRecordRevision,
    rightsEvidenceFingerprint: value.rightsEvidenceFingerprint,
    catalogRevisionId: value.catalogRevisionId,
    castingProfileFingerprint: value.castingProfileFingerprint,
    phase2SnapshotFingerprint: value.phase2SnapshotFingerprint,
    effectiveCorrectionSetFingerprint:
      value.effectiveCorrectionSetFingerprint,
    authority: "human_locked",
    rightsState: value.rightsState,
    revision: value.revision,
    supersedesAssignmentId: value.supersedesAssignmentId,
  };
}

function sanitizePhase3Screenshot(value) {
  if (
    !isPlainObject(value) ||
    !hasExactKeys(value, [
      "relativePath",
      "byteSize",
      "sha256",
      "captureStatus",
    ]) ||
    !isBoundedEvidenceText(value.relativePath, 2_000) ||
    !Number.isSafeInteger(value.byteSize) ||
    value.byteSize < 1 ||
    !isSha256(value.sha256) ||
    value.captureStatus !== "success"
  ) {
    return null;
  }
  return {
    relativePath: value.relativePath,
    byteSize: value.byteSize,
    sha256: value.sha256,
    captureStatus: "success",
  };
}

function sanitizePhase3ProcessOwnership(value) {
  const launchShutdowns = sanitizePhase3LaunchShutdowns(
    value?.launchShutdowns,
  );
  if (
    !isPlainObject(value) ||
    !hasExactKeys(value, [
      "ownershipEstablished",
      "electronOwnedPids",
      "serviceOwnedPids",
      "launchShutdowns",
      "forcedPids",
      "remainingOwnedPids",
      "unrelatedProcessesTerminated",
    ]) ||
    value.ownershipEstablished !== true ||
    !isOrderedUniquePidArray(value.electronOwnedPids, 1, 100) ||
    !isOrderedUniquePidArray(value.serviceOwnedPids, 1, 20) ||
    launchShutdowns === null ||
    !Array.isArray(value.forcedPids) ||
    value.forcedPids.length !== 0 ||
    !Array.isArray(value.remainingOwnedPids) ||
    value.remainingOwnedPids.length !== 0 ||
    value.unrelatedProcessesTerminated !== false
  ) {
    return null;
  }
  return {
    ownershipEstablished: true,
    electronOwnedPids: [...value.electronOwnedPids],
    serviceOwnedPids: [...value.serviceOwnedPids],
    launchShutdowns,
    forcedPids: [],
    remainingOwnedPids: [],
    unrelatedProcessesTerminated: false,
  };
}

function sanitizePhase3LaunchShutdowns(value) {
  if (!Array.isArray(value) || value.length !== 2) {
    return null;
  }
  const shutdowns = [];
  for (const [index, shutdown] of value.entries()) {
    if (
      !isPlainObject(shutdown) ||
      !hasExactKeys(shutdown, ["launch", "electron", "service"]) ||
      shutdown.launch !== index + 1 ||
      !isPlainObject(shutdown.electron) ||
      !hasExactKeys(shutdown.electron, [
        "launcherPid",
        "rootPid",
        "exitCode",
        "forceKillUsed",
      ]) ||
      !isPositivePid(shutdown.electron.launcherPid) ||
      !isPositivePid(shutdown.electron.rootPid) ||
      shutdown.electron.exitCode !== 0 ||
      shutdown.electron.forceKillUsed !== false ||
      !isPlainObject(shutdown.service) ||
      !hasExactKeys(shutdown.service, [
        "pid",
        "method",
        "exitCode",
        "signalCode",
        "forceKillUsed",
      ]) ||
      !isPositivePid(shutdown.service.pid) ||
      shutdown.service.method !== "stdin_eof" ||
      shutdown.service.exitCode !== 0 ||
      shutdown.service.signalCode !== null ||
      shutdown.service.forceKillUsed !== false
    ) {
      return null;
    }
    shutdowns.push({
      launch: shutdown.launch,
      electron: {
        launcherPid: shutdown.electron.launcherPid,
        rootPid: shutdown.electron.rootPid,
        exitCode: 0,
        forceKillUsed: false,
      },
      service: {
        pid: shutdown.service.pid,
        method: "stdin_eof",
        exitCode: 0,
        signalCode: null,
        forceKillUsed: false,
      },
    });
  }
  return shutdowns;
}

function phase3LaunchShutdownsMatch(processOwnership, launches) {
  return (
    Array.isArray(launches) &&
    launches.length === 2 &&
    processOwnership.launchShutdowns.every((shutdown, index) => {
      const launch = launches[index];
      if (
        launch === undefined ||
        shutdown.launch !== launch.launch ||
        shutdown.electron.launcherPid !==
          launch.ownership.launcherPid ||
        shutdown.electron.rootPid !== launch.ownership.rootPid ||
        !launch.exitProof.ownedPids.includes(
          shutdown.electron.rootPid,
        ) ||
        !launch.exitProof.ownedPids.includes(shutdown.service.pid)
      ) {
        return false;
      }
      const serviceRoots = launch.ownership.processes.filter(
        (process) =>
          process.kind === "service" &&
          process.parentPid === launch.ownership.rootPid,
      );
      return (
        serviceRoots.length === 1 &&
        serviceRoots[0].pid === shutdown.service.pid
      );
    })
  );
}

function sanitizePhase3Assertions(value) {
  if (
    !isPlainObject(value) ||
    !hasExactKeys(value, PHASE_3_ASSERTION_KEYS) ||
    !PHASE_3_ASSERTION_KEYS.every((key) => value[key] === true)
  ) {
    return null;
  }
  return Object.fromEntries(
    PHASE_3_ASSERTION_KEYS.map((key) => [key, true]),
  );
}

async function inspectPhase3VoiceCastingEvidence(
  evidencePath,
  evidenceFile,
  phase3HarnessResult,
  phase2HarnessResult,
  voiceCatalogEvidence,
) {
  if (
    !evidenceFile.exists ||
    typeof evidenceFile.sizeBytes !== "number" ||
    evidenceFile.sizeBytes <= 0 ||
    evidenceFile.sizeBytes > MAX_HARNESS_RESULT_BYTES ||
    !phase3HarnessResult.contractValid ||
    phase3HarnessResult.value === null ||
    phase2HarnessResult.storyAnalysis === null
  ) {
    return null;
  }
  try {
    const value = JSON.parse(await readFile(evidencePath, "utf8"));
    if (
      !isPlainObject(value) ||
      !hasExactKeys(value, [
        "castingProfile",
        "providers",
        "models",
        "catalogRevision",
        "rightsPolicyId",
        "phase2Evidence",
        "counts",
        "castingRunId",
        "approvedCastSnapshot",
        "assignments",
        "rightsEligibility",
        "correctionPersistence",
        "conflictDispositionPersistence",
        "gateDecisions",
        "restartPersistence",
        "packagedE2e",
        "assertions",
      ])
    ) {
      return null;
    }
    const profile = sanitizePhase3CastingProfileEvidence(
      value.castingProfile,
    );
    const phase2Evidence = sanitizePhase3Phase2Evidence(
      value.phase2Evidence,
    );
    const counts = sanitizePhase3Counts(value.counts);
    const snapshot = sanitizePhase3SnapshotEvidence(
      value.approvedCastSnapshot,
    );
    const assignments = sanitizePhase3AssignmentEvidence(
      value.assignments,
    );
    const gateDecisions = isUniquePhase3IdArray(
      value.gateDecisions,
      3,
      3,
    )
      ? [...value.gateDecisions]
      : null;
    const assertions = sanitizePhase3Assertions(value.assertions);
    const castingProof = phase3HarnessResult.value.casting;
    const storyAnalysis = phase2HarnessResult.storyAnalysis;
    const expectedPhase2GateDecisions = storyAnalysis.gates.map(
      (gate) => gate.afterRestart.decisionId,
    );
    const expectedCastingGateDecisions = [
      castingProof.gateDecisionIds.narratorCastingReview,
      castingProof.gateDecisionIds.characterCastingReview,
      castingProof.gateDecisionIds.completeCastReview,
    ];
    if (
      profile === null ||
      phase2Evidence === null ||
      counts === null ||
      snapshot === null ||
      assignments === null ||
      gateDecisions === null ||
      assertions === null ||
      !jsonValuesEqual(value.providers, voiceCatalogEvidence.providers) ||
      !jsonValuesEqual(value.models, voiceCatalogEvidence.models) ||
      !jsonValuesEqual(
        value.catalogRevision,
        voiceCatalogEvidence.catalogRevision,
      ) ||
      value.rightsPolicyId !== voiceCatalogEvidence.rightsPolicyId ||
      phase2Evidence.analysisRunId !== storyAnalysis.run.runId ||
      phase2Evidence.snapshotId !== storyAnalysis.run.snapshotId ||
      phase2Evidence.snapshotRevision !==
        storyAnalysis.run.snapshotRevision ||
      phase2Evidence.snapshotFingerprint !==
        storyAnalysis.run.snapshotFingerprint ||
      phase2Evidence.correctionSetFingerprint !==
        storyAnalysis.run.correctionSetFingerprint ||
      !equalStringArrays(
        phase2Evidence.gateDecisionIds,
        expectedPhase2GateDecisions,
      ) ||
      value.castingRunId !== castingProof.castingRunId ||
      snapshot.snapshotId !== castingProof.approvedCastSnapshotId ||
      snapshot.revision !== castingProof.approvedCastSnapshotRevision ||
      assignments.narratorAssignmentId !==
        castingProof.narratorAssignmentId ||
      !equalStringArrays(
        assignments.characterAssignmentIds,
        castingProof.characterAssignmentIds,
      ) ||
      !jsonValuesEqual(
        assignments.narratorAssignment,
        castingProof.narratorAssignment,
      ) ||
      !jsonValuesEqual(
        assignments.characterAssignments,
        castingProof.characterAssignments,
      ) ||
      [
        assignments.narratorAssignment,
        ...assignments.characterAssignments,
      ].some(
        (assignment) => {
          const expected =
            voiceCatalogEvidence.assignmentEvidenceByVoiceId.get(
              assignment.voiceProfileId,
            );
          return (
            expected === undefined ||
            assignment.voiceProfileVersion !==
              expected.voiceProfileVersion ||
            assignment.voiceEvidenceFingerprint !==
              expected.voiceEvidenceFingerprint ||
            assignment.rightsRecordId !== expected.rightsRecordId ||
            assignment.rightsRecordRevision !==
              expected.rightsRecordRevision ||
            assignment.rightsEvidenceFingerprint !==
              expected.rightsEvidenceFingerprint ||
            assignment.rightsState !== expected.rightsState ||
            assignment.catalogRevisionId !==
            voiceCatalogEvidence.catalogRevision.catalogRevisionId ||
            assignment.castingProfileFingerprint !==
              profile.fingerprint ||
            assignment.phase2SnapshotFingerprint !==
              phase2Evidence.snapshotFingerprint
          );
        },
      ) ||
      counts.productionRoles !==
        counts.narratorRoles + counts.characterRoles ||
      counts.narratorRoles < 1 ||
      counts.characterRoles < 2 ||
      counts.preReductionCandidates < counts.finalCandidates ||
      counts.preReductionCandidates >
        counts.productionRoles * 50 ||
      counts.finalCandidates > counts.productionRoles * 12 ||
      counts.finalCandidates < counts.productionRoles ||
      counts.conflicts < 1 ||
      counts.assignments !==
        1 + assignments.characterAssignmentIds.length ||
      counts.corrections < 7 ||
      value.rightsEligibility !==
        "eligible_after_required_acknowledgements" ||
      value.correctionPersistence !== true ||
      value.conflictDispositionPersistence !== true ||
      !equalStringArrays(
        gateDecisions,
        expectedCastingGateDecisions,
      ) ||
      value.restartPersistence !== true ||
      !jsonValuesEqual(
        value.packagedE2e,
        phase3HarnessResult.value,
      ) ||
      !jsonValuesEqual(assertions, phase3HarnessResult.value.assertions)
    ) {
      return null;
    }
    return {
      castingProfile: profile,
      providers: voiceCatalogEvidence.providers,
      models: voiceCatalogEvidence.models,
      catalogRevision: voiceCatalogEvidence.catalogRevision,
      rightsPolicyId: voiceCatalogEvidence.rightsPolicyId,
      phase2Evidence,
      counts,
      castingRunId: value.castingRunId,
      approvedCastSnapshot: snapshot,
      assignments,
      rightsEligibility: value.rightsEligibility,
      correctionPersistence: true,
      conflictDispositionPersistence: true,
      gateDecisions,
      restartPersistence: true,
      packagedE2e: phase3HarnessResult.value,
      assertions,
    };
  } catch {
    return null;
  }
}

function sanitizePhase3CastingProfileEvidence(value) {
  if (
    !isPlainObject(value) ||
    !hasExactKeys(value, [
      "profileId",
      "fingerprint",
      "producerId",
    ]) ||
    value.profileId !== GOVERNED_VOICE_CASTING_PROFILE_ID ||
    value.fingerprint !==
      GOVERNED_VOICE_CASTING_PROFILE_FINGERPRINT ||
    value.producerId !== VOICE_CASTING_PRODUCER_ID
  ) {
    return null;
  }
  return {
    profileId: value.profileId,
    fingerprint: value.fingerprint,
    producerId: value.producerId,
  };
}

function sanitizePhase3Phase2Evidence(value) {
  if (
    !isPlainObject(value) ||
    !hasExactKeys(value, [
      "analysisRunId",
      "snapshotId",
      "snapshotRevision",
      "snapshotFingerprint",
      "correctionSetFingerprint",
      "gateDecisionIds",
    ]) ||
    !isPhase3Id(value.analysisRunId) ||
    !isPhase3Id(value.snapshotId) ||
    !Number.isSafeInteger(value.snapshotRevision) ||
    value.snapshotRevision < 1 ||
    !isSha256(value.snapshotFingerprint) ||
    !isSha256(value.correctionSetFingerprint) ||
    !isUniquePhase3IdArray(value.gateDecisionIds, 4, 4)
  ) {
    return null;
  }
  return {
    analysisRunId: value.analysisRunId,
    snapshotId: value.snapshotId,
    snapshotRevision: value.snapshotRevision,
    snapshotFingerprint: value.snapshotFingerprint,
    correctionSetFingerprint: value.correctionSetFingerprint,
    gateDecisionIds: [...value.gateDecisionIds],
  };
}

function sanitizePhase3Counts(value) {
  const bounds = {
    productionRoles: 300,
    narratorRoles: 300,
    characterRoles: 300,
    preReductionCandidates: 15_000,
    finalCandidates: 3_600,
    conflicts: 10_000,
    assignments: 300,
    corrections: MAX_CASTING_CORRECTIONS_PER_RUN,
  };
  if (
    !isPlainObject(value) ||
    !hasExactKeys(value, Object.keys(bounds)) ||
    !Object.entries(bounds).every(
      ([key, maximum]) =>
        Number.isSafeInteger(value[key]) &&
        value[key] >= 0 &&
        value[key] <= maximum,
    )
  ) {
    return null;
  }
  return Object.fromEntries(
    Object.keys(bounds).map((key) => [key, value[key]]),
  );
}

function sanitizePhase3SnapshotEvidence(value) {
  if (
    !isPlainObject(value) ||
    !hasExactKeys(value, [
      "snapshotId",
      "revision",
      "fingerprint",
    ]) ||
    !isPhase3Id(value.snapshotId) ||
    !Number.isSafeInteger(value.revision) ||
    value.revision < 1 ||
    !isSha256(value.fingerprint)
  ) {
    return null;
  }
  return {
    snapshotId: value.snapshotId,
    revision: value.revision,
    fingerprint: value.fingerprint,
  };
}

function sanitizePhase3AssignmentEvidence(value) {
  const narratorAssignment = sanitizePhase3RoleVoiceAssignment(
    value?.narratorAssignment,
    PHASE_3_NARRATOR_ROLE_TYPES,
  );
  const characterAssignments = sanitizePhase3RoleVoiceAssignments(
    value?.characterAssignments,
    PHASE_3_CHARACTER_ROLE_TYPES,
    2,
    300,
  );
  if (
    !isPlainObject(value) ||
    !hasExactKeys(value, [
      "narratorAssignmentId",
      "characterAssignmentIds",
      "narratorAssignment",
      "characterAssignments",
    ]) ||
    !isPhase3Id(value.narratorAssignmentId) ||
    !isUniquePhase3IdArray(value.characterAssignmentIds, 2, 300) ||
    value.characterAssignmentIds.includes(value.narratorAssignmentId) ||
    narratorAssignment === null ||
    characterAssignments === null ||
    narratorAssignment.assignmentId !== value.narratorAssignmentId ||
    !equalStringArrays(
      characterAssignments.map(
        (assignment) => assignment.assignmentId,
      ),
      value.characterAssignmentIds,
    ) ||
    narratorAssignment.rightsState !== "restricted"
  ) {
    return null;
  }
  return {
    narratorAssignmentId: value.narratorAssignmentId,
    characterAssignmentIds: [...value.characterAssignmentIds],
    narratorAssignment,
    characterAssignments,
  };
}

function sanitizeImportReviewEvidence(value) {
  if (
    !isPlainObject(value) ||
    !hasExactKeys(value, [
      "format",
      "sourceSha256",
      "extractedTextSha256",
      "extractionRevision",
      "warningCount",
      "approvalDecision",
      "approvalPersistedAfterRestart",
      "extractionPersistedAfterRestart",
      "analysisPersistedAfterRestart",
    ]) ||
    value.format !== "docx" ||
    !isSha256(value.sourceSha256) ||
    !isSha256(value.extractedTextSha256) ||
    !Number.isSafeInteger(value.extractionRevision) ||
    value.extractionRevision < 1 ||
    value.extractionRevision > 1_000_000 ||
    !Number.isSafeInteger(value.warningCount) ||
    value.warningCount < 0 ||
    value.warningCount > 256 ||
    value.approvalDecision !== "approved" ||
    typeof value.approvalPersistedAfterRestart !== "boolean" ||
    typeof value.extractionPersistedAfterRestart !== "boolean" ||
    typeof value.analysisPersistedAfterRestart !== "boolean"
  ) {
    return null;
  }
  return {
    format: value.format,
    sourceSha256: value.sourceSha256,
    extractedTextSha256: value.extractedTextSha256,
    extractionRevision: value.extractionRevision,
    warningCount: value.warningCount,
    approvalDecision: value.approvalDecision,
    approvalPersistedAfterRestart:
      value.approvalPersistedAfterRestart,
    extractionPersistedAfterRestart:
      value.extractionPersistedAfterRestart,
    analysisPersistedAfterRestart:
      value.analysisPersistedAfterRestart,
  };
}

function sanitizeStoryAnalysisEvidence(value) {
  if (
    !isPlainObject(value) ||
    !hasExactKeys(value, [
      "profile",
      "agents",
      "approvedInput",
      "run",
      "observedStages",
      "counts",
      "assertions",
      "corrections",
      "gates",
      "restart",
    ]) ||
    !isPlainObject(value.profile) ||
    !hasExactKeys(value.profile, [
      "profileId",
      "semanticVersion",
      "profileFingerprint",
      "producerId",
      "producerVersion",
    ]) ||
    value.profile.profileId !== WHOLE_BOOK_ANALYSIS_PROFILE_ID ||
    value.profile.semanticVersion !==
      WHOLE_BOOK_ANALYSIS_PROFILE_VERSION ||
    value.profile.profileFingerprint !==
      WHOLE_BOOK_ANALYSIS_PROFILE_FINGERPRINT ||
    value.profile.producerId !== WHOLE_BOOK_ANALYSIS_PRODUCER_ID ||
    value.profile.producerVersion !==
      WHOLE_BOOK_ANALYSIS_PRODUCER_VERSION
  ) {
    return null;
  }
  const agents = sanitizePhase2AgentEvidence(value.agents);
  const approvedInput = sanitizeApprovedAnalysisInput(
    value.approvedInput,
  );
  const run = sanitizeAnalysisRunEvidence(value.run);
  const counts = sanitizeAnalysisCounts(value.counts);
  const assertions = sanitizeAnalysisAssertions(value.assertions);
  const corrections = sanitizeAnalysisCorrectionEvidence(
    value.corrections,
  );
  const gates = sanitizeAnalysisGateEvidence(value.gates, {
    profileFingerprint: value.profile.profileFingerprint,
    runFingerprint: run?.runFingerprint,
    snapshotId: run?.snapshotId,
    snapshotRevision: run?.snapshotRevision,
    snapshotFingerprint: run?.snapshotFingerprint,
  });
  const restart = sanitizeAnalysisRestartEvidence(value.restart);
  if (
    agents === null ||
    approvedInput === null ||
    run === null ||
    counts === null ||
    assertions === null ||
    corrections === null ||
    gates === null ||
    restart === null ||
    !equalStringArrays(value.observedStages, PHASE_2_JOB_STAGES)
  ) {
    return null;
  }
  return {
    profile: {
      profileId: value.profile.profileId,
      semanticVersion: value.profile.semanticVersion,
      profileFingerprint: value.profile.profileFingerprint,
      producerId: value.profile.producerId,
      producerVersion: value.profile.producerVersion,
    },
    agents,
    approvedInput,
    run,
    observedStages: [...value.observedStages],
    counts,
    assertions,
    corrections,
    gates,
    restart,
  };
}

function sanitizePhase2AgentEvidence(value) {
  if (
    !Array.isArray(value) ||
    value.length !== PHASE_2_RUNTIME_AGENTS.length
  ) {
    return null;
  }
  const seenExecutions = new Set();
  const agents = [];
  for (const [index, item] of value.entries()) {
    const expected = PHASE_2_RUNTIME_AGENTS[index];
    if (
      !isPlainObject(item) ||
      !hasExactKeys(item, [
        "agentId",
        "agentVersion",
        "executionId",
        "status",
        "outputFingerprint",
      ]) ||
      item.agentId !== expected.agentId ||
      item.agentVersion !== expected.agentVersion ||
      !isPublicId(item.executionId) ||
      seenExecutions.has(item.executionId) ||
      item.status !== "succeeded" ||
      !isSha256(item.outputFingerprint)
    ) {
      return null;
    }
    seenExecutions.add(item.executionId);
    agents.push({
      agentId: item.agentId,
      agentVersion: item.agentVersion,
      executionId: item.executionId,
      status: item.status,
      outputFingerprint: item.outputFingerprint,
    });
  }
  return agents;
}

function sanitizeApprovedAnalysisInput(value) {
  if (
    !isPlainObject(value) ||
    !hasExactKeys(value, [
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
      "storyId",
      "storyRevision",
      "storyFingerprint",
    ])
  ) {
    return null;
  }
  for (const key of [
    "sourceDocumentId",
    "extractionId",
    "importReviewId",
    "importReviewDecisionId",
    "storyId",
  ]) {
    if (!isPublicId(value[key])) {
      return null;
    }
  }
  for (const key of [
    "sourceRevision",
    "extractionRevision",
    "importReviewRevision",
    "storyRevision",
  ]) {
    if (
      !Number.isSafeInteger(value[key]) ||
      value[key] < 1 ||
      value[key] > 1_000_000
    ) {
      return null;
    }
  }
  for (const key of [
    "sourceSha256",
    "extractedTextSha256",
    "approvedEvidenceFingerprint",
    "storyFingerprint",
  ]) {
    if (!isSha256(value[key])) {
      return null;
    }
  }
  return { ...value };
}

function sanitizeAnalysisRunEvidence(value) {
  if (
    !isPlainObject(value) ||
    !hasExactKeys(value, [
      "runId",
      "inputFingerprint",
      "runFingerprint",
      "jobId",
      "status",
      "snapshotId",
      "snapshotRevision",
      "snapshotFingerprint",
      "correctionSetFingerprint",
    ]) ||
    !isPublicId(value.runId) ||
    !isPublicId(value.jobId) ||
    value.status !== "succeeded" ||
    !isPublicId(value.snapshotId) ||
    !Number.isSafeInteger(value.snapshotRevision) ||
    value.snapshotRevision < 1 ||
    value.snapshotRevision > 1_000_000
  ) {
    return null;
  }
  for (const key of [
    "inputFingerprint",
    "runFingerprint",
    "snapshotFingerprint",
    "correctionSetFingerprint",
  ]) {
    if (!isSha256(value[key])) {
      return null;
    }
  }
  return { ...value };
}

function sanitizeAnalysisCounts(value) {
  const keys = [
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
    "corrections",
  ];
  if (!isPlainObject(value) || !hasExactKeys(value, keys)) {
    return null;
  }
  const counts = {};
  for (const key of keys) {
    if (
      !Number.isSafeInteger(value[key]) ||
      value[key] < 0 ||
      value[key] > 10_000_000
    ) {
      return null;
    }
    counts[key] = value[key];
  }
  return counts;
}

function sanitizeAnalysisAssertions(value) {
  const keys = [
    "structureDetected",
    "characterRegistryDetected",
    "ambiguousIdentityPreserved",
    "ambiguousDialoguePreserved",
    "narrationDistinctionDetected",
    "povShiftDetected",
    "locationsDetected",
    "timelineFlashbackDetected",
    "relationshipChangeDetected",
    "emotionalProgressionDetected",
    "continuityAnomalyDetected",
  ];
  if (
    !isPlainObject(value) ||
    !hasExactKeys(value, keys) ||
    !keys.every((key) => typeof value[key] === "boolean")
  ) {
    return null;
  }
  return Object.fromEntries(keys.map((key) => [key, value[key]]));
}

function sanitizeAnalysisCorrectionEvidence(value) {
  const expected = {
    characterIdentity: "character_identity",
    dialogueSpeaker: "dialogue_speaker",
    continuityDisposition: "continuity_disposition",
  };
  if (
    !isPlainObject(value) ||
    !hasExactKeys(value, Object.keys(expected))
  ) {
    return null;
  }
  const corrections = {};
  const seenIds = new Set();
  for (const [name, category] of Object.entries(expected)) {
    const item = value[name];
    if (
      !isPlainObject(item) ||
      !hasExactKeys(item, [
        "correctionId",
        "category",
        "targetEntityId",
        "reasonFingerprint",
        "previousValueFingerprint",
        "correctedValueFingerprint",
        "effectiveValueFingerprintBeforeRestart",
        "effectiveValueFingerprintAfterRestart",
        "effectiveAuthorityBeforeRestart",
        "effectiveAuthorityAfterRestart",
        "immutable",
        "lockedAgainstAutomation",
        "persistedAfterRestart",
      ]) ||
      !isPublicId(item.correctionId) ||
      seenIds.has(item.correctionId) ||
      item.category !== category ||
      !isPublicId(item.targetEntityId) ||
      item.reasonFingerprint !==
        PHASE_2_CORRECTION_REASON_FINGERPRINTS[category] ||
      !isSha256(item.previousValueFingerprint) ||
      !isSha256(item.correctedValueFingerprint) ||
      item.effectiveValueFingerprintBeforeRestart !==
        item.correctedValueFingerprint ||
      item.effectiveValueFingerprintAfterRestart !==
        item.correctedValueFingerprint ||
      item.effectiveAuthorityBeforeRestart !== "human" ||
      item.effectiveAuthorityAfterRestart !== "human" ||
      item.immutable !== true ||
      item.lockedAgainstAutomation !== true ||
      typeof item.persistedAfterRestart !== "boolean"
    ) {
      return null;
    }
    seenIds.add(item.correctionId);
    corrections[name] = { ...item };
  }
  return corrections;
}

function sanitizeAnalysisGateEvidence(value, expected) {
  if (
    !Array.isArray(value) ||
    value.length !== PHASE_2_GATE_IDS.length
  ) {
    return null;
  }
  const gates = [];
  const seenDecisionIds = new Set();
  for (const [index, item] of value.entries()) {
    if (
      !isPlainObject(item) ||
      !hasExactKeys(item, [
        "gateId",
        "beforeRestart",
        "afterRestart",
        "immutable",
      ]) ||
      item.gateId !== PHASE_2_GATE_IDS[index] ||
      item.immutable !== true
    ) {
      return null;
    }
    const beforeRestart = sanitizeGateStateEvidence(
      item.beforeRestart,
      expected,
    );
    const afterRestart = sanitizeGateStateEvidence(
      item.afterRestart,
      expected,
    );
    if (
      beforeRestart === null ||
      afterRestart === null ||
      seenDecisionIds.has(beforeRestart.decisionId) ||
      JSON.stringify(beforeRestart) !== JSON.stringify(afterRestart)
    ) {
      return null;
    }
    seenDecisionIds.add(beforeRestart.decisionId);
    gates.push({
      gateId: item.gateId,
      beforeRestart,
      afterRestart,
      immutable: true,
    });
  }
  return gates;
}

function sanitizeGateStateEvidence(value, expected) {
  if (
    !isPlainObject(value) ||
    !hasExactKeys(value, [
      "reviewId",
      "decisionId",
      "state",
      "profileFingerprint",
      "runFingerprint",
      "snapshotId",
      "snapshotRevision",
      "snapshotFingerprint",
      "decisionRecordFingerprint",
      "artifactFingerprint",
      "evidenceFingerprint",
    ]) ||
    !isPublicId(value.reviewId) ||
    !isPublicId(value.decisionId) ||
    value.state !== "approved" ||
    value.profileFingerprint !== expected.profileFingerprint ||
    value.runFingerprint !== expected.runFingerprint ||
    value.snapshotId !== expected.snapshotId ||
    value.snapshotRevision !== expected.snapshotRevision ||
    value.snapshotFingerprint !== expected.snapshotFingerprint ||
    !isSha256(value.profileFingerprint) ||
    !isSha256(value.runFingerprint) ||
    !isPublicId(value.snapshotId) ||
    !Number.isSafeInteger(value.snapshotRevision) ||
    value.snapshotRevision < 1 ||
    value.snapshotRevision > 1_000_000 ||
    !isSha256(value.snapshotFingerprint) ||
    !isSha256(value.decisionRecordFingerprint) ||
    !isSha256(value.artifactFingerprint) ||
    !isSha256(value.evidenceFingerprint)
  ) {
    return null;
  }
  return { ...value };
}

function sanitizeAnalysisRestartEvidence(value) {
  const keys = [
    "runPersisted",
    "snapshotPersisted",
    "correctionSetPersisted",
    "gateDecisionsPersisted",
    "agentExecutionsPersisted",
  ];
  if (
    !isPlainObject(value) ||
    !hasExactKeys(value, keys) ||
    !keys.every((key) => typeof value[key] === "boolean")
  ) {
    return null;
  }
  return Object.fromEntries(keys.map((key) => [key, value[key]]));
}

function sanitizePackagedFlow(value) {
  if (
    !Array.isArray(value) ||
    value.length < 12 ||
    value.length > 64 ||
    !value.every(
      (item) =>
        typeof item === "string" &&
        /^[a-z][a-z0-9_]{0,79}$/u.test(item),
    )
  ) {
    return null;
  }
  return [...value];
}

function validFailureCodeForStage(stage, code) {
  if (
    (stage === "prelaunch_inventory_1" ||
      stage === "prelaunch_inventory_2") &&
    PACKAGED_INVENTORY_FAILURE_CODES.has(code)
  ) {
    return true;
  }
  if (
    (stage === "root_ownership_1" ||
      stage === "root_ownership_2" ||
      stage === "service_ownership_1" ||
      stage === "service_ownership_2" ||
      stage === "shutdown_1" ||
      stage === "shutdown_2") &&
    PACKAGED_INVENTORY_FAILURE_CODES.has(code)
  ) {
    return true;
  }
  const expectedCode = {
    isolation_setup: "ISOLATION_SETUP_FAILED",
    launch_1: "APPLICATION_LAUNCH_FAILED",
    root_ownership_1: "ROOT_OWNERSHIP_NOT_ESTABLISHED",
    readiness_1: "APPLICATION_READINESS_FAILED",
    service_ownership_1: "SERVICE_OWNERSHIP_NOT_ESTABLISHED",
    workflow_1: "APPLICATION_WORKFLOW_FAILED",
    shutdown_1: "SHUTDOWN_VERIFICATION_FAILED",
    launch_2: "APPLICATION_LAUNCH_FAILED",
    root_ownership_2: "ROOT_OWNERSHIP_NOT_ESTABLISHED",
    readiness_2: "APPLICATION_READINESS_FAILED",
    service_ownership_2: "SERVICE_OWNERSHIP_NOT_ESTABLISHED",
    restore_2: "APPLICATION_WORKFLOW_FAILED",
    screenshot: "SCREENSHOT_CAPTURE_FAILED",
    shutdown_2: "SHUTDOWN_VERIFICATION_FAILED",
    evidence_generation: "EVIDENCE_GENERATION_FAILED",
    cleanup: "CLEANUP_FAILED",
  }[stage];
  return code === expectedCode;
}

function validFailedProgress({
  stage,
  launches,
  preexistingRelevantProcesses,
  preexistingRelevantProcessesWereUnavailable,
  applicationLaunchBegan,
  ownershipEstablished,
  cleanupCompleted,
  screenshotCaptured,
  actualOwnershipExitProof,
}) {
  if (!Array.isArray(launches)) {
    return false;
  }
  const launchCount = launches.length;
  const firstLaunchExitProven =
    launchCount > 0 && launchOwnershipExitProven(launches[0]);

  switch (stage) {
    case "prelaunch_inventory_1":
      return (
        preexistingRelevantProcessesWereUnavailable &&
        preexistingRelevantProcesses === null &&
        !applicationLaunchBegan &&
        !ownershipEstablished &&
        !screenshotCaptured &&
        launchCount === 0
      );
    case "isolation_setup":
      return (
        !preexistingRelevantProcessesWereUnavailable &&
        Array.isArray(preexistingRelevantProcesses) &&
        !applicationLaunchBegan &&
        !ownershipEstablished &&
        !screenshotCaptured &&
        launchCount === 0
      );
    case "launch_1":
      return (
        !preexistingRelevantProcessesWereUnavailable &&
        Array.isArray(preexistingRelevantProcesses) &&
        applicationLaunchBegan &&
        !ownershipEstablished &&
        !screenshotCaptured &&
        launchCount === 0
      );
    case "root_ownership_1":
    case "readiness_1":
    case "service_ownership_1":
      return (
        !preexistingRelevantProcessesWereUnavailable &&
        Array.isArray(preexistingRelevantProcesses) &&
        applicationLaunchBegan &&
        !ownershipEstablished &&
        !screenshotCaptured &&
        launchCount === 0
      );
    case "workflow_1":
    case "shutdown_1":
      return (
        !preexistingRelevantProcessesWereUnavailable &&
        Array.isArray(preexistingRelevantProcesses) &&
        applicationLaunchBegan &&
        ownershipEstablished &&
        !screenshotCaptured &&
        launchCount === 0
      );
    case "prelaunch_inventory_2":
      return (
        !preexistingRelevantProcessesWereUnavailable &&
        Array.isArray(preexistingRelevantProcesses) &&
        applicationLaunchBegan &&
        ownershipEstablished &&
        !screenshotCaptured &&
        launchCount === 1 &&
        firstLaunchExitProven
      );
    case "launch_2":
      return (
        !preexistingRelevantProcessesWereUnavailable &&
        Array.isArray(preexistingRelevantProcesses) &&
        applicationLaunchBegan &&
        !ownershipEstablished &&
        !screenshotCaptured &&
        launchCount === 1 &&
        firstLaunchExitProven
      );
    case "root_ownership_2":
    case "readiness_2":
    case "service_ownership_2":
      return (
        !preexistingRelevantProcessesWereUnavailable &&
        Array.isArray(preexistingRelevantProcesses) &&
        applicationLaunchBegan &&
        !ownershipEstablished &&
        !screenshotCaptured &&
        launchCount === 1 &&
        firstLaunchExitProven
      );
    case "restore_2":
    case "screenshot":
      return (
        !preexistingRelevantProcessesWereUnavailable &&
        Array.isArray(preexistingRelevantProcesses) &&
        applicationLaunchBegan &&
        ownershipEstablished &&
        !screenshotCaptured &&
        launchCount === 1 &&
        firstLaunchExitProven
      );
    case "shutdown_2":
      return (
        !preexistingRelevantProcessesWereUnavailable &&
        Array.isArray(preexistingRelevantProcesses) &&
        applicationLaunchBegan &&
        ownershipEstablished &&
        screenshotCaptured &&
        launchCount === 1 &&
        firstLaunchExitProven
      );
    case "evidence_generation":
      return (
        !preexistingRelevantProcessesWereUnavailable &&
        Array.isArray(preexistingRelevantProcesses) &&
        applicationLaunchBegan &&
        ownershipEstablished &&
        cleanupCompleted &&
        screenshotCaptured &&
        launchCount === 2 &&
        actualOwnershipExitProof
      );
    case "cleanup":
      return (
        !preexistingRelevantProcessesWereUnavailable &&
        Array.isArray(preexistingRelevantProcesses) &&
        applicationLaunchBegan &&
        ownershipEstablished &&
        !cleanupCompleted &&
        screenshotCaptured &&
        launchCount === 2 &&
        actualOwnershipExitProof
      );
    default:
      return false;
  }
}

function launchOwnershipExitProven(launch) {
  return (
    launch.ownership.processes.some(
      (process) =>
        process.pid === launch.ownership.rootPid &&
        process.kind === "app",
    ) &&
    launch.ownership.processes.some(
      (process) => process.kind === "service",
    ) &&
    launch.exitProof.ownedPids.length > 0 &&
    launch.exitProof.ownedPids.includes(launch.ownership.rootPid) &&
    launch.exitProof.graceful &&
    launch.exitProof.forcedPids.length === 0 &&
    launch.exitProof.remainingPids.length === 0
  );
}

function invalidHarnessResult() {
  return {
    contractValid: false,
    reportedStatus: null,
    screenshotCaptured: null,
    completedAt: null,
    launches: [],
    ownershipExitProven: false,
    failureStage: null,
    failureCode: null,
    applicationLaunchBegan: null,
    ownershipEstablished: null,
    cleanupCompleted: null,
    completedLaunches: [],
    importReview: null,
    storyAnalysis: null,
    flow: [],
  };
}

function sanitizeLaunchProof(value) {
  if (!Array.isArray(value) || value.length > 2) {
    return null;
  }
  const launches = [];
  const seenLaunches = new Set();
  for (const item of value) {
    if (
      !isPlainObject(item) ||
      !hasExactKeys(item, [
        "launch",
        "preexistingRelevantProcesses",
        "ownership",
        "exitProof",
      ]) ||
      (item.launch !== 1 && item.launch !== 2) ||
      item.launch !== launches.length + 1 ||
      seenLaunches.has(item.launch) ||
      !Array.isArray(item.preexistingRelevantProcesses) ||
      !isPlainObject(item.ownership) ||
      !hasExactKeys(item.ownership, [
        "launcherPid",
        "rootPid",
        "processes",
      ]) ||
      !isPositivePid(item.ownership.launcherPid) ||
      !isPositivePid(item.ownership.rootPid) ||
      !Array.isArray(item.ownership.processes) ||
      item.ownership.processes.length === 0 ||
      item.ownership.processes.length > 256 ||
      !isPlainObject(item.exitProof) ||
      !hasExactKeys(item.exitProof, [
        "ownedPids",
        "graceful",
        "forcedPids",
        "remainingPids",
      ]) ||
      typeof item.exitProof.graceful !== "boolean"
    ) {
      return null;
    }
    seenLaunches.add(item.launch);
    const preexistingRelevantProcesses = sanitizePreexistingProcesses(
      item.preexistingRelevantProcesses,
    );
    if (preexistingRelevantProcesses === null) {
      return null;
    }
    const processes = [];
    const seenProcessPids = new Set();
    for (const process of item.ownership.processes) {
      if (
        !isPlainObject(process) ||
        !hasExactKeys(process, [
          "pid",
          "parentPid",
          "kind",
          "executableName",
          "creationDate",
        ]) ||
        !isPositivePid(process.pid) ||
        seenProcessPids.has(process.pid) ||
        !Number.isSafeInteger(process.parentPid) ||
        process.parentPid < 0 ||
        (process.kind !== "app" &&
          process.kind !== "service" &&
          process.kind !== "provider_worker") ||
        typeof process.executableName !== "string" ||
        (process.kind === "app" &&
          process.executableName !== APP_EXECUTABLE_NAME) ||
        (process.kind === "service" &&
          process.executableName !== SERVICE_EXECUTABLE_NAME) ||
        (process.kind === "provider_worker" &&
          process.executableName !== SERVICE_EXECUTABLE_NAME) ||
        typeof process.creationDate !== "string"
      ) {
        return null;
      }
      seenProcessPids.add(process.pid);
      processes.push({
        pid: process.pid,
        parentPid: process.parentPid,
        kind: process.kind,
        executableName: process.executableName,
        creationDate: normalizeProcessCreationDate(process.creationDate),
      });
    }
    processes.sort((left, right) => left.pid - right.pid);
    if (
      !validRootedProcessTree(
        processes,
        item.ownership.launcherPid,
        item.ownership.rootPid,
      ) ||
      processes.some((process) =>
        preexistingRelevantProcesses.some(
          (baseline) =>
            baseline.pid === process.pid &&
            baseline.name === process.executableName &&
            baseline.creationDate === process.creationDate,
        ),
      )
    ) {
      return null;
    }
    const ownedPids = sortedUniquePids(item.exitProof.ownedPids);
    const forcedPids = sortedUniquePids(item.exitProof.forcedPids);
    const remainingPids = sortedUniquePids(item.exitProof.remainingPids);
    const expectedOwnedPids = processes
      .map((process) => process.pid)
      .sort((left, right) => left - right);
    if (
      ownedPids === null ||
      forcedPids === null ||
      remainingPids === null ||
      !equalNumberArrays(ownedPids, expectedOwnedPids) ||
      !forcedPids.every((pid) => ownedPids.includes(pid)) ||
      !remainingPids.every((pid) => ownedPids.includes(pid))
    ) {
      return null;
    }
    launches.push({
      launch: item.launch,
      preexistingRelevantProcesses,
      ownership: {
        launcherPid: item.ownership.launcherPid,
        rootPid: item.ownership.rootPid,
        processes,
      },
      exitProof: {
        ownedPids,
        graceful: item.exitProof.graceful,
        forcedPids,
        remainingPids,
      },
    });
  }
  const sortedLaunches = launches.sort(
    (left, right) => left.launch - right.launch,
  );
  if (
    sortedLaunches.length === 2 &&
    !validCrossLaunchProcessProof(sortedLaunches[0], sortedLaunches[1])
  ) {
    return null;
  }
  return sortedLaunches;
}

function validCrossLaunchProcessProof(first, second) {
  const firstIdentities = new Set(
    first.ownership.processes.map(processIdentityEvidenceKey),
  );
  if (
    second.ownership.processes.some((process) =>
      firstIdentities.has(processIdentityEvidenceKey(process)),
    )
  ) {
    return false;
  }
  const latestFirstCreation = first.ownership.processes.reduce(
    (latest, process) =>
      process.creationDate > latest ? process.creationDate : latest,
    first.ownership.processes[0].creationDate,
  );
  const secondRoot = second.ownership.processes.find(
    (process) => process.pid === second.ownership.rootPid,
  );
  return (
    secondRoot !== undefined &&
    secondRoot.creationDate > latestFirstCreation
  );
}

function processIdentityEvidenceKey(process) {
  return JSON.stringify([
    process.pid,
    process.executableName,
    process.creationDate,
  ]);
}

function sanitizePreexistingProcesses(value) {
  if (!Array.isArray(value) || value.length > 256) {
    return null;
  }
  const processes = [];
  const seenPids = new Set();
  for (const item of value) {
    if (
      !isPlainObject(item) ||
      !hasExactKeys(item, ["pid", "name", "creationDate"]) ||
      !isPositivePid(item.pid) ||
      seenPids.has(item.pid) ||
      (item.name !== APP_EXECUTABLE_NAME &&
        item.name !== SERVICE_EXECUTABLE_NAME) ||
      typeof item.creationDate !== "string"
    ) {
      return null;
    }
    seenPids.add(item.pid);
    processes.push({
      pid: item.pid,
      name: item.name,
      creationDate: normalizeProcessCreationDate(item.creationDate),
    });
  }
  return processes.sort((left, right) => left.pid - right.pid);
}

function sanitizeCompletedLaunchNumbers(value) {
  if (
    !Array.isArray(value) ||
    value.length > 2 ||
    !value.every((item, index) => item === index + 1)
  ) {
    return null;
  }
  return [...value];
}

function equalPreexistingProcesses(left, right) {
  return (
    left.length === right.length &&
    left.every(
      (value, index) =>
        value.pid === right[index].pid &&
        value.name === right[index].name &&
        value.creationDate === right[index].creationDate,
    )
  );
}

function validRootedProcessTree(processes, launcherPid, rootPid) {
  const byPid = new Map(processes.map((process) => [process.pid, process]));
  const root = byPid.get(rootPid);
  if (
    root === undefined ||
    root.kind !== "app" ||
    root.executableName !== APP_EXECUTABLE_NAME ||
    root.parentPid !== launcherPid ||
    launcherPid === rootPid ||
    byPid.has(launcherPid)
  ) {
    return false;
  }

  for (const process of processes) {
    if (process.pid === rootPid) {
      continue;
    }
    const visited = new Set([process.pid]);
    let child = process;
    while (child.pid !== rootPid) {
      const parent = byPid.get(child.parentPid);
      if (
        parent === undefined ||
        visited.has(parent.pid) ||
        parent.creationDate > child.creationDate
      ) {
        return false;
      }
      visited.add(parent.pid);
      child = parent;
    }
  }
  return true;
}

function sortedUniquePids(value) {
  if (!Array.isArray(value) || !value.every(isPositivePid)) {
    return null;
  }
  const unique = new Set(value);
  if (unique.size !== value.length) {
    return null;
  }
  return [...unique].sort((left, right) => left - right);
}

function isPositivePid(value) {
  return Number.isSafeInteger(value) && value > 0;
}

function isSha256(value) {
  return typeof value === "string" && /^[a-f0-9]{64}$/u.test(value);
}

function isPublicId(value) {
  return (
    typeof value === "string" &&
    /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/u.test(value)
  );
}

function isPhase3Id(value) {
  return (
    typeof value === "string" &&
    /^[A-Za-z0-9][A-Za-z0-9@._:-]{0,199}$/u.test(value)
  );
}

function isBoundedEvidenceText(value, maximumLength) {
  return (
    typeof value === "string" &&
    value.length >= 1 &&
    value.length <= maximumLength &&
    /\S/u.test(value) &&
    !value.includes("\0") &&
    !/[\r\n]/u.test(value)
  );
}

function isUniquePhase3IdArray(value, minimum, maximum) {
  return (
    Array.isArray(value) &&
    value.length >= minimum &&
    value.length <= maximum &&
    value.every(isPhase3Id) &&
    new Set(value).size === value.length
  );
}

function isOrderedUniquePidArray(value, minimum, maximum) {
  const normalized = sortedUniquePids(value);
  return (
    Array.isArray(normalized) &&
    normalized.length >= minimum &&
    normalized.length <= maximum &&
    equalNumberArrays(value, normalized)
  );
}

function canonicalizeJsonValue(value) {
  if (Array.isArray(value)) {
    return value.map(canonicalizeJsonValue);
  }
  if (isPlainObject(value)) {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, canonicalizeJsonValue(value[key])]),
    );
  }
  return value;
}

function equalStringArrays(value, expected) {
  return (
    Array.isArray(value) &&
    value.length === expected.length &&
    value.every((item, index) => item === expected[index])
  );
}

function equalNumberArrays(left, right) {
  return (
    left.length === right.length &&
    left.every((value, index) => value === right[index])
  );
}

function equalAgentVersionEvidence(actual, expected) {
  return (
    Array.isArray(actual) &&
    actual.length === expected.length &&
    actual.every(
      (item, index) =>
        item.agentId === expected[index].agentId &&
        item.agentVersion === expected[index].agentVersion,
    )
  );
}

function storyAnalysisCountsMeetFixture(counts, expected) {
  return (
    counts.chapters >= expected.chapters &&
    counts.scenes >= expected.scenes &&
    counts.characters >= expected.namedCharacters &&
    counts.locations >= expected.locations &&
    counts.dialogueLines >= expected.dialogueLines &&
    counts.povSegments >= expected.povShifts &&
    counts.continuityFindings >= expected.continuityAnomalies &&
    counts.narrationSpans >= 1 &&
    counts.timelineEvents >= 2 &&
    counts.temporalConstraints >= 1 &&
    counts.relationships >= 2 &&
    counts.emotionalStates >= 3 &&
    counts.dramaticIntents >= 1
  );
}

async function sha256File(target) {
  const digest = createHash("sha256");
  for await (const chunk of createReadStream(target)) {
    digest.update(chunk);
  }
  return digest.digest("hex");
}

function sha256Bytes(value) {
  return createHash("sha256").update(value).digest("hex");
}

function normalizeHeadSha(value) {
  const normalized = requiredBoundedString(value, "head SHA", 64);
  if (!/^[0-9a-f]{40,64}$/iu.test(normalized)) {
    throw new Error("The build-evidence head SHA is invalid.");
  }
  return normalized.toLowerCase();
}

function normalizeOptionalHeadSha(value) {
  if (value === "") {
    return null;
  }
  return normalizeHeadSha(value);
}

function normalizeTimestamp(value) {
  const normalized = requiredBoundedString(value, "timestamp", 80);
  const parsed = new Date(normalized);
  if (Number.isNaN(parsed.valueOf())) {
    throw new Error("The build-evidence timestamp is invalid.");
  }
  return parsed.toISOString();
}

function normalizeUtcTimestamp(value) {
  const normalized = requiredBoundedString(value, "test timestamp", 80);
  if (!normalized.endsWith("Z")) {
    throw new Error("The packaged E2E test timestamp is not UTC.");
  }
  return normalizeTimestamp(normalized);
}

function normalizeProcessCreationDate(value) {
  const normalized = requiredBoundedString(
    value,
    "process creation date",
    40,
  );
  const match =
    /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\.(\d{7})Z$/u.exec(
      normalized,
    );
  if (match === null) {
    throw new Error(
      "The packaged E2E process creation date is not an invariant UTC timestamp.",
    );
  }
  const parsed = new Date(normalized);
  if (
    Number.isNaN(parsed.valueOf()) ||
    parsed.toISOString() !== `${match[1]}.${match[2].slice(0, 3)}Z`
  ) {
    throw new Error("The packaged E2E process creation date is invalid.");
  }
  return normalized;
}

function normalizeStepOutcome(value) {
  const normalized = requiredBoundedString(
    value,
    "packaged E2E step outcome",
    16,
  ).toLowerCase();
  if (normalized !== "success" && normalized !== "failure") {
    throw new Error("The packaged E2E step outcome is invalid.");
  }
  return normalized;
}

function normalizeRunner(value) {
  if (!isPlainObject(value)) {
    throw new Error("The runner identity is invalid.");
  }
  const runner = {
    name: requiredBoundedString(value.name, "runner name", 200),
    os: requiredBoundedString(value.os, "runner operating system", 40),
    architecture: requiredBoundedString(
      value.architecture,
      "runner architecture",
      40,
    ),
    environment: requiredBoundedString(
      value.environment,
      "runner environment",
      80,
    ),
    runId: requiredBoundedString(value.runId, "run ID", 40),
    runAttempt: requiredBoundedString(
      value.runAttempt,
      "run attempt",
      20,
    ),
    workflow: requiredBoundedString(value.workflow, "workflow name", 200),
    job: requiredBoundedString(value.job, "job name", 200),
  };
  if (
    runner.os !== "Windows" ||
    runner.architecture !== "X64" ||
    runner.environment !== "github-hosted" ||
    runner.workflow !== "Phase 3B Windows CI" ||
    runner.job !== "verify-and-build"
  ) {
    throw new Error(
      "The build-evidence runner does not match the Phase 3B Windows CI job.",
    );
  }
  return runner;
}

function requiredBoundedString(value, label, maximumLength) {
  if (
    typeof value !== "string" ||
    value.length === 0 ||
    value.length > maximumLength ||
    value !== value.trim() ||
    value.includes("\0") ||
    /[\r\n]/u.test(value)
  ) {
    throw new Error(`The ${label} is invalid.`);
  }
  return value;
}

function assertExactPath(actual, expected, label) {
  if (
    typeof actual !== "string" ||
    actual.length === 0 ||
    actual.length > 2048 ||
    actual.includes("\0") ||
    !path.isAbsolute(actual) ||
    normalizedPath(actual) !== normalizedPath(expected)
  ) {
    throw new Error(`The ${label} path is invalid.`);
  }
}

function relativeRepositoryPath(repositoryRoot, target, label) {
  assertRepositoryChild(repositoryRoot, target, label);
  return path
    .relative(path.resolve(repositoryRoot), path.resolve(target))
    .split(path.sep)
    .join("/");
}

function assertRepositoryChild(repositoryRoot, target, label) {
  const relative = path.relative(
    path.resolve(repositoryRoot),
    path.resolve(target),
  );
  if (
    relative.length === 0 ||
    relative === ".." ||
    relative.startsWith(`..${path.sep}`) ||
    path.isAbsolute(relative)
  ) {
    throw new Error(`The ${label} must be inside the repository.`);
  }
}

function normalizedPath(value) {
  const resolved = path.resolve(value);
  return process.platform === "win32" ? resolved.toLowerCase() : resolved;
}

function isMissingFileError(error) {
  return (
    typeof error === "object" &&
    error !== null &&
    "code" in error &&
    error.code === "ENOENT"
  );
}

function isPlainObject(value) {
  return (
    typeof value === "object" &&
    value !== null &&
    !Array.isArray(value)
  );
}

function hasExactKeys(value, expected) {
  if (!isPlainObject(value)) {
    return false;
  }
  const actual = Object.keys(value).sort();
  const sortedExpected = [...expected].sort();
  return (
    actual.length === sortedExpected.length &&
    actual.every((key, index) => key === sortedExpected[index])
  );
}

function jsonValuesEqual(left, right) {
  if (Object.is(left, right)) {
    return true;
  }
  if (Array.isArray(left) || Array.isArray(right)) {
    return (
      Array.isArray(left) &&
      Array.isArray(right) &&
      left.length === right.length &&
      left.every((item, index) =>
        jsonValuesEqual(item, right[index]),
      )
    );
  }
  if (isPlainObject(left) || isPlainObject(right)) {
    if (!isPlainObject(left) || !isPlainObject(right)) {
      return false;
    }
    const leftKeys = Object.keys(left).sort();
    const rightKeys = Object.keys(right).sort();
    return (
      equalStringArrays(leftKeys, rightKeys) &&
      leftKeys.every((key) =>
        jsonValuesEqual(left[key], right[key]),
      )
    );
  }
  return false;
}

function environmentValue(name) {
  const value = process.env[name];
  if (value === undefined) {
    throw new Error(`Missing required build-evidence environment: ${name}.`);
  }
  return value;
}

async function main() {
  const arguments_ = process.argv.slice(2);
  if (arguments_[0] === "--validate-manifest") {
    if (arguments_.length !== 2) {
      throw new Error(
        "Usage: node scripts/ci/build-evidence.mjs --validate-manifest <path>.",
      );
    }
    const result = await validateBuildEvidenceManifest({
      manifestPath: arguments_[1],
    });
    process.stdout.write(
      `Build evidence validated: ${relativeRepositoryPath(
        defaultRepositoryRoot,
        result.manifestPath,
        "build-evidence manifest",
      )}\n`,
    );
    return;
  }
  if (arguments_.length !== 0) {
    throw new Error(
      "Usage: node scripts/ci/build-evidence.mjs [--validate-manifest <path>].",
    );
  }
  const result = await generateBuildEvidence({
    workflowHeadSha: environmentValue(
      BUILD_EVIDENCE_ENVIRONMENT.workflowHeadSha,
    ),
    testedCheckoutSha: environmentValue(
      BUILD_EVIDENCE_ENVIRONMENT.testedCheckoutSha,
    ),
    pullRequestHeadSha: environmentValue(
      BUILD_EVIDENCE_ENVIRONMENT.pullRequestHeadSha,
    ),
    timestamp: environmentValue(BUILD_EVIDENCE_ENVIRONMENT.timestamp),
    runner: {
      name: environmentValue(BUILD_EVIDENCE_ENVIRONMENT.runnerName),
      os: environmentValue(BUILD_EVIDENCE_ENVIRONMENT.runnerOs),
      architecture: environmentValue(
        BUILD_EVIDENCE_ENVIRONMENT.runnerArchitecture,
      ),
      environment: environmentValue(
        BUILD_EVIDENCE_ENVIRONMENT.runnerEnvironment,
      ),
      runId: environmentValue(BUILD_EVIDENCE_ENVIRONMENT.runId),
      runAttempt: environmentValue(
        BUILD_EVIDENCE_ENVIRONMENT.runAttempt,
      ),
      workflow: environmentValue(BUILD_EVIDENCE_ENVIRONMENT.workflow),
      job: environmentValue(BUILD_EVIDENCE_ENVIRONMENT.job),
    },
    packagedE2e: {
      executablePath: environmentValue(
        BUILD_EVIDENCE_ENVIRONMENT.executablePath,
      ),
      screenshotPath: environmentValue(
        BUILD_EVIDENCE_ENVIRONMENT.screenshotPath,
      ),
      resultPath: environmentValue(
        BUILD_EVIDENCE_ENVIRONMENT.resultPath,
      ),
      phase3ResultPath: environmentValue(
        BUILD_EVIDENCE_ENVIRONMENT.phase3ResultPath,
      ),
      phase3VoiceCastingEvidencePath: environmentValue(
        BUILD_EVIDENCE_ENVIRONMENT
          .phase3VoiceCastingEvidencePath,
      ),
      phase3bResultPath: environmentValue(
        BUILD_EVIDENCE_ENVIRONMENT.phase3bResultPath,
      ),
      stepOutcome: environmentValue(
        BUILD_EVIDENCE_ENVIRONMENT.stepOutcome,
      ),
    },
  });
  process.stdout.write(
    `Build evidence written: ${relativeRepositoryPath(
      defaultRepositoryRoot,
      result.manifestPath,
      "build-evidence manifest",
    )}\n`,
  );
}

if (
  process.argv[1] !== undefined &&
  path.resolve(process.argv[1]) === path.resolve(fileURLToPath(import.meta.url))
) {
  main().catch((error) => {
    process.stderr.write(`Build evidence failed: ${error.message}\n`);
    process.exitCode = 1;
  });
}
