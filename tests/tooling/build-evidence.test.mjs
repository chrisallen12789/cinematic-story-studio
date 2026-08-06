import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import {
  mkdir,
  mkdtemp,
  readFile,
  rm,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

import {
  createSyntheticFixtures,
  createSyntheticStoryExpectations,
} from "../../fixtures/synthetic-story/generate-fixtures.mjs";
import {
  generateBuildEvidence,
  validateBuildEvidenceManifest,
} from "../../scripts/ci/build-evidence.mjs";
import { capture } from "../../scripts/lib/process.mjs";
import {
  repositoryRoot,
  servicePython,
} from "../../scripts/lib/paths.mjs";

const APP_VERSION = "0.1.0";
const PR_HEAD_SHA = "0123456789abcdef0123456789abcdef01234567";
const WORKFLOW_HEAD_SHA =
  "89abcdef0123456789abcdef0123456789abcdef";
const FALLBACK_TIMESTAMP = "2026-07-29T18:00:00.000Z";
const COMPLETED_AT = "2026-07-29T18:15:21.123Z";
const SYNTHETIC_FIXTURES = await createSyntheticFixtures();
const SYNTHETIC_EXPECTATIONS =
  await createSyntheticStoryExpectations();
const SYNTHETIC_MARKDOWN_BYTES = await readFile(
  new URL(
    "../../fixtures/synthetic-story/sample-story.md",
    import.meta.url,
  ),
);
const SYNTHETIC_VOICE_CATALOG_BYTES = await readFile(
  new URL(
    "../../apps/local-service/src/cinematic_story_service/catalogs/synthetic_voice_catalog.v1.json",
    import.meta.url,
  ),
);
const SYNTHETIC_VOICE_CATALOG = JSON.parse(
  SYNTHETIC_VOICE_CATALOG_BYTES.toString("utf8"),
);
const SYNTHETIC_DOCX_BYTES = SYNTHETIC_FIXTURES.docx;
const SYNTHETIC_DOCX_SHA256 = createHash("sha256")
  .update(SYNTHETIC_DOCX_BYTES)
  .digest("hex");
const EXTRACTED_TEXT_SHA256 =
  "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789";
const RUNNER = Object.freeze({
  name: "GitHub Actions 1000000000",
  os: "Windows",
  architecture: "X64",
  environment: "github-hosted",
  runId: "30478862847",
  runAttempt: "2",
  workflow: "Phase 3B.1 Windows CI",
  job: "verify-and-build",
});
const PACKAGED_FIXTURE =
  "fixtures/synthetic-story/sample-story.docx.base64";
const PACKAGED_FLOW = Object.freeze([
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
const CASTING_PROFILE_ID = "governed-voice-casting-v1@1.0.1";
const CASTING_PROFILE_FINGERPRINT =
  "5377949573018b5d3a4f4cd343392155071640364d3ba36be80a1bf4ad58de97";
const GOVERNED_CATALOG_REVISION_ID =
  "governed-local-voice-catalog-v2@2.0.0";
const GOVERNED_CATALOG_FINGERPRINT =
  "994a2f77daed881cc4e24201d628ef32a732aa6ee0ff0815745a19772d2828cc";
const PROFILE_FINGERPRINT =
  "6ae73e83e89fbcfc0261ff339950407913cd990093fa13cdcc83ce3b1da810ec";
const CORRECTION_REASON_FINGERPRINTS = Object.freeze({
  character_identity:
    "b42b6091d0bde37b4dd15f99a321e86dd965f2272a4ca68da6703b6f5ba2f0da",
  dialogue_speaker:
    "6db94e679f663d3dfbed36c173eb8445d6a364beb114c7f8c070e30983247300",
  continuity_disposition:
    "d7497ae908f34096c6bbbfdcbf7ea8287967882880cd229af078eee2383c2554",
});
const AGENTS = Object.freeze([
  "story-structure",
  "story-beats",
  "character-identity",
  "dialogue-attribution",
  "point-of-view",
  "story-setting",
  "story-timeline",
  "character-relationships",
  "emotion-dramatic-intent",
  "story-continuity",
  "analysis-synthesis",
]);
const OBSERVED_STAGES = Object.freeze([
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
const GATE_IDS = Object.freeze([
  "story_structure_review",
  "character_registry_review",
  "dialogue_attribution_review",
  "whole_book_analysis_review",
]);
const IMPORT_REVIEW = Object.freeze({
  format: "docx",
  sourceSha256: SYNTHETIC_DOCX_SHA256,
  extractedTextSha256: EXTRACTED_TEXT_SHA256,
  extractionRevision: 1,
  warningCount: 0,
  approvalDecision: "approved",
  approvalPersistedAfterRestart: true,
  extractionPersistedAfterRestart: true,
  analysisPersistedAfterRestart: true,
});
const PARSER_PROFILE = Object.freeze({
  archiveExpandedBytes: 200 * 1024 * 1024,
  archiveMemberBytes: 32 * 1024 * 1024,
  archiveMemberNameCodePoints: 512,
  archiveMembers: 2_048,
  archivePathDepth: 20,
  canonicalTextCodePoints: 10_000_000,
  extractedSections: 10_000,
  ingestContractVersion: "1.0.0",
  maximumCompressionRatio: 100,
  parserDeadlineMs: 30_000,
  parserProcessMemoryBytes: 768 * 1024 * 1024,
  pdfPages: 2_000,
  profileId: "secure-ingest-v1",
});
const PARSER_PROFILE_FINGERPRINT =
  "3c9fef89ac411e84ef0ef8962b3d43ef3469d090035a537c9bf72c6d93cdd922";
const PARSER_PROFILE_CANONICAL_JSON =
  '{"archiveExpandedBytes":209715200,"archiveMemberBytes":33554432,"archiveMemberNameCodePoints":512,"archiveMembers":2048,"archivePathDepth":20,"canonicalTextCodePoints":10000000,"extractedSections":10000,"ingestContractVersion":"1.0.0","maximumCompressionRatio":100.0,"parserDeadlineMs":30000,"parserProcessMemoryBytes":805306368,"pdfPages":2000,"profileId":"secure-ingest-v1"}';

function storyAnalysisEvidence() {
  const fingerprint = (label) => sha256(Buffer.from(label, "utf8"));
  const correction = (name, category) => {
    const correctedValueFingerprint = fingerprint(
      `${name}-corrected-value`,
    );
    return {
      correctionId: `correction-${name}`,
      category,
      targetEntityId: `entity-${name}`,
      reasonFingerprint: CORRECTION_REASON_FINGERPRINTS[category],
      previousValueFingerprint: fingerprint(`${name}-previous-value`),
      correctedValueFingerprint,
      effectiveValueFingerprintBeforeRestart:
        correctedValueFingerprint,
      effectiveValueFingerprintAfterRestart:
        correctedValueFingerprint,
      effectiveAuthorityBeforeRestart: "human",
      effectiveAuthorityAfterRestart: "human",
      immutable: true,
      lockedAgainstAutomation: true,
      persistedAfterRestart: true,
    };
  };
  return {
    profile: {
      profileId: "whole-book-intelligence-v1",
      semanticVersion: "1.0.0",
      profileFingerprint: PROFILE_FINGERPRINT,
      producerId: "whole-book-analysis-orchestrator",
      producerVersion: "1.0.0",
    },
    agents: AGENTS.map((agentId, index) => ({
      agentId,
      agentVersion: "1.0.0",
      executionId: `execution-${String(index + 1).padStart(2, "0")}`,
      status: "succeeded",
      outputFingerprint: fingerprint(`${agentId}-output`),
    })),
    approvedInput: {
      sourceDocumentId: "source-document-1",
      sourceRevision: 1,
      sourceSha256: SYNTHETIC_DOCX_SHA256,
      extractionId: "extraction-1",
      extractionRevision: 1,
      extractedTextSha256: EXTRACTED_TEXT_SHA256,
      importReviewId: "import-review-1",
      importReviewRevision: 2,
      importReviewDecisionId: "import-decision-1",
      approvedEvidenceFingerprint: fingerprint(
        "approved-import-evidence",
      ),
      storyId: "story-1",
      storyRevision: 1,
      storyFingerprint: fingerprint("story-1"),
    },
    run: {
      runId: "analysis-run-1",
      inputFingerprint: fingerprint("analysis-input"),
      runFingerprint: fingerprint("analysis-run"),
      jobId: "whole-book-job-1",
      status: "succeeded",
      snapshotId: "analysis-snapshot-4",
      snapshotRevision: 4,
      snapshotFingerprint: fingerprint("analysis-snapshot-4"),
      correctionSetFingerprint: fingerprint("correction-set-3"),
    },
    observedStages: [...OBSERVED_STAGES],
    counts: {
      agentExecutions: 11,
      chapters: 3,
      scenes: 6,
      beats: 24,
      characters: 10,
      mentions: 48,
      dialogueLines: 10,
      narrationSpans: 15,
      povSegments: 6,
      locations: 6,
      timelineEvents: 8,
      temporalConstraints: 7,
      relationships: 4,
      emotionalStates: 6,
      dramaticIntents: 5,
      continuityFindings: 1,
      corrections: 3,
    },
    assertions: {
      structureDetected: true,
      characterRegistryDetected: true,
      ambiguousIdentityPreserved: true,
      ambiguousDialoguePreserved: true,
      narrationDistinctionDetected: true,
      povShiftDetected: true,
      locationsDetected: true,
      timelineFlashbackDetected: true,
      relationshipChangeDetected: true,
      emotionalProgressionDetected: true,
      continuityAnomalyDetected: true,
    },
    corrections: {
      characterIdentity: correction(
        "character-identity",
        "character_identity",
      ),
      dialogueSpeaker: correction(
        "dialogue-speaker",
        "dialogue_speaker",
      ),
      continuityDisposition: correction(
        "continuity-disposition",
        "continuity_disposition",
      ),
    },
    gates: GATE_IDS.map((gateId, index) => {
      const state = {
        reviewId: `gate-review-${index + 1}`,
        decisionId: `gate-decision-${index + 1}`,
        state: "approved",
        profileFingerprint: PROFILE_FINGERPRINT,
        runFingerprint: fingerprint("analysis-run"),
        snapshotId: "analysis-snapshot-4",
        snapshotRevision: 4,
        snapshotFingerprint: fingerprint("analysis-snapshot-4"),
        decisionRecordFingerprint: fingerprint(
          `gate-${index + 1}-decision-record`,
        ),
        artifactFingerprint: fingerprint(
          `gate-${index + 1}-artifact`,
        ),
        evidenceFingerprint: fingerprint(
          `gate-${index + 1}-evidence`,
        ),
      };
      return {
        gateId,
        beforeRestart: state,
        afterRestart: { ...state },
        immutable: true,
      };
    }),
    restart: {
      runPersisted: true,
      snapshotPersisted: true,
      correctionSetPersisted: true,
      gateDecisionsPersisted: true,
      agentExecutionsPersisted: true,
    },
  };
}

function phase3Assertions() {
  return {
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
    processOwnershipExitProven: true,
  };
}

function phase3PackagedResult(screenshotBytes) {
  return {
    schemaVersion: "5.0.0",
    contractVersion: "3.0.0",
    result: "passed",
    fixture: PACKAGED_FIXTURE,
    flow: [...PHASE_3_PACKAGED_FLOW],
    casting: {
      profileId: CASTING_PROFILE_ID,
      profileFingerprint: CASTING_PROFILE_FINGERPRINT,
      catalogRevisionId: GOVERNED_CATALOG_REVISION_ID,
      catalogFingerprint: GOVERNED_CATALOG_FINGERPRINT,
      castingRunId: "casting-run-1",
      approvedCastSnapshotId: "approved-cast-snapshot-1",
      approvedCastSnapshotRevision: 1,
      narratorAssignmentId: "assignment-narrator-1",
      characterAssignmentIds: [
        "assignment-character-1",
        "assignment-character-2",
      ],
      narratorAssignment: {
        assignmentId: "assignment-narrator-1",
        roleId: "role-primary-narrator",
        roleType: "primary_narrator",
        ...syntheticCatalogAssignmentEvidence(
          "synthetic-narrator-02",
        ),
        castingProfileFingerprint: CASTING_PROFILE_FINGERPRINT,
        phase2SnapshotFingerprint: fingerprint("analysis-snapshot-4"),
        effectiveCorrectionSetFingerprint: fingerprint(
          "casting-corrections-narrator",
        ),
        authority: "human_locked",
        revision: 3,
        supersedesAssignmentId: "assignment-narrator-selection",
      },
      characterAssignments: [
        {
          assignmentId: "assignment-character-1",
          roleId: "role-character-1",
          roleType: "named_character",
          ...syntheticCatalogAssignmentEvidence(
            "synthetic-character-01",
          ),
          castingProfileFingerprint: CASTING_PROFILE_FINGERPRINT,
          phase2SnapshotFingerprint: fingerprint(
            "analysis-snapshot-4",
          ),
          effectiveCorrectionSetFingerprint: fingerprint(
            "casting-corrections-character-1",
          ),
          authority: "human_locked",
          revision: 3,
          supersedesAssignmentId: "assignment-character-1-selection",
        },
        {
          assignmentId: "assignment-character-2",
          roleId: "role-character-2",
          roleType: "named_character",
          ...syntheticCatalogAssignmentEvidence(
            "synthetic-character-02",
          ),
          castingProfileFingerprint: CASTING_PROFILE_FINGERPRINT,
          phase2SnapshotFingerprint: fingerprint(
            "analysis-snapshot-4",
          ),
          effectiveCorrectionSetFingerprint: fingerprint(
            "casting-corrections-character-2",
          ),
          authority: "human_locked",
          revision: 3,
          supersedesAssignmentId: "assignment-character-2-selection",
        },
      ],
      conflictId: "casting-conflict-1",
      conflictDispositionCorrectionId: "casting-correction-7",
      restrictedRightsWarningId: "rights-warning-1",
      ineligibleApprovalRejected: true,
      gateDecisionIds: {
        narratorCastingReview: "casting-gate-decision-1",
        characterCastingReview: "casting-gate-decision-2",
        completeCastReview: "casting-gate-decision-3",
      },
      restartRestored: true,
    },
    screenshot: {
      relativePath:
        "apps/desktop/release/0.1.0/packaged-e2e.png",
      byteSize: screenshotBytes.length,
      sha256: sha256(screenshotBytes),
      captureStatus: "success",
    },
    processOwnership: {
      ownershipEstablished: true,
      electronOwnedPids: [4100, 4200],
      serviceOwnedPids: [4101, 4201],
      launchShutdowns: [
        {
          launch: 1,
          electron: {
            launcherPid: 5100,
            rootPid: 4100,
            exitCode: 0,
            forceKillUsed: false,
          },
          service: {
            pid: 4101,
            method: "stdin_eof",
            exitCode: 0,
            signalCode: null,
            forceKillUsed: false,
          },
        },
        {
          launch: 2,
          electron: {
            launcherPid: 5200,
            rootPid: 4200,
            exitCode: 0,
            forceKillUsed: false,
          },
          service: {
            pid: 4201,
            method: "stdin_eof",
            exitCode: 0,
            signalCode: null,
            forceKillUsed: false,
          },
        },
      ],
      forcedPids: [],
      remainingOwnedPids: [],
      unrelatedProcessesTerminated: false,
    },
    assertions: phase3Assertions(),
    completedAt: COMPLETED_AT,
  };
}

function phase3VoiceCastingEvidence(packagedResult) {
  const analysis = storyAnalysisEvidence();
  return {
    castingProfile: {
      profileId: CASTING_PROFILE_ID,
      fingerprint: CASTING_PROFILE_FINGERPRINT,
      producerId: "voice-casting-orchestrator@1.0.0",
    },
    providers: [
      ...SYNTHETIC_VOICE_CATALOG.providers.map((provider) => ({
        descriptorId: provider.providerId,
        version: provider.providerVersion,
      })),
      { descriptorId: "kokoro-local-onnx", version: "1.0.0" },
    ],
    models: [
      ...SYNTHETIC_VOICE_CATALOG.models.map((model) => ({
        descriptorId: model.modelId,
        version: model.modelVersion,
      })),
      {
        descriptorId: "onnx-community/Kokoro-82M-v1.0-ONNX",
        version: "1.0",
      },
    ],
    catalogRevision: {
      catalogRevisionId: GOVERNED_CATALOG_REVISION_ID,
      revision: 2,
      fingerprint: GOVERNED_CATALOG_FINGERPRINT,
    },
    rightsPolicyId: "voice-rights-policy-v1",
    phase2Evidence: {
      analysisRunId: analysis.run.runId,
      snapshotId: analysis.run.snapshotId,
      snapshotRevision: analysis.run.snapshotRevision,
      snapshotFingerprint: analysis.run.snapshotFingerprint,
      correctionSetFingerprint: analysis.run.correctionSetFingerprint,
      gateDecisionIds: analysis.gates.map(
        (gate) => gate.afterRestart.decisionId,
      ),
    },
    counts: {
      productionRoles: 3,
      narratorRoles: 1,
      characterRoles: 2,
      preReductionCandidates: 42,
      finalCandidates: 30,
      conflicts: 1,
      assignments: 3,
      corrections: 7,
    },
    castingRunId: packagedResult.casting.castingRunId,
    approvedCastSnapshot: {
      snapshotId:
        packagedResult.casting.approvedCastSnapshotId,
      revision:
        packagedResult.casting.approvedCastSnapshotRevision,
      fingerprint: sha256(Buffer.from("approved-cast-snapshot")),
    },
    assignments: {
      narratorAssignmentId:
        packagedResult.casting.narratorAssignmentId,
      characterAssignmentIds:
        packagedResult.casting.characterAssignmentIds,
      narratorAssignment:
        packagedResult.casting.narratorAssignment,
      characterAssignments:
        packagedResult.casting.characterAssignments,
    },
    rightsEligibility: "eligible_after_required_acknowledgements",
    correctionPersistence: true,
    conflictDispositionPersistence: true,
    gateDecisions: Object.values(
      packagedResult.casting.gateDecisionIds,
    ),
    restartPersistence: true,
    packagedE2e: packagedResult,
    assertions: packagedResult.assertions,
  };
}

function phase3b1SyntheticMetadata() {
  return {
    catalog: {
      catalogRevisionId: "governed-local-voice-catalog-v2@2.0.0",
      catalogRevisionFingerprint:
        "994a2f77daed881cc4e24201d628ef32a732aa6ee0ff0815745a19772d2828cc",
      castingProfileId: "governed-voice-casting-v1@1.0.1",
      castingProfileFingerprint:
        "5377949573018b5d3a4f4cd343392155071640364d3ba36be80a1bf4ad58de97",
      totalVoiceCount: 15,
      deterministicFixtureVoiceCount: 14,
      governedRealVoiceCount: 1,
    },
    governedRealVoice: {
      neutralDisplayLabel: "Local Voice 001",
      providerId: "kokoro-local-onnx",
      providerVersion: "1.0.0",
      modelId: "onnx-community/Kokoro-82M-v1.0-ONNX",
      modelVersion: "1.0",
      modelPackageId: "kokoro-82m-v1.0-onnx-q8-af-heart",
      modelPackageVersion:
        "1.0.0+1939ad2a8e416c0acfeecc08a694d14ef25f2231",
      modelPackageFingerprint:
        "03702762c09a71ee54b7ea3bfa4939d1c622b01d68709e2180a39ca62ec264b0",
      voiceProfileId: "kokoro-local-voice-001",
      voiceProfileVersion: "1.0.0",
      voiceProfileFingerprint:
        "dd81588a36a17b429e90ee9b21a80187c10368bab6bd5b8fa584ea01c455a210",
      providerVoiceId: "af_heart",
      tensor: {
        relativePath: "voices/af_heart.bin",
        byteSize: 522_240,
        sha256:
          "d583ccff3cdca2f7fae535cb998ac07e9fcb90f09737b9a41fa2734ec44a8f0b",
        scalarFormat: "float32_le",
        shape: [510, 256],
        elementCount: 130_560,
      },
      rights: {
        rightsRecordId: "kokoro-local-voice-001-rights-v1",
        rightsRecordRevision: 1,
        rightsRecordFingerprint:
          "e801171e684b1125b54bfc4317ae17dac4ca5b92c1500b82b333dc6da357c038",
        rightsState: "restricted",
        consentStatus: "unknown",
        productionExportEligible: false,
      },
    },
    restriction: {
      warningText:
        "Private local audition only. This voice is not cleared by Cinematic Story Studio for production export, commercial distribution, marketplace resale, cloning, or real-person imitation.",
      warningFingerprint:
        "13b8747ea2ced9de9cc1d0f67b5c018b25de7de02359a1480744db4a37939645",
      activationGovernance:
        "restricted_audition_acknowledgement_required",
      packagePresentInCi: false,
      activationEligibleInCi: false,
      failClosedReason: "model_package_absent",
      humanListeningStatus: "pending",
      humanListeningClaimed: false,
      realSynthesisClaimed: false,
    },
    artifactBoundary: {
      realModelBytesPresent: false,
      realProviderAudioPresent: false,
    },
  };
}

function phase3b1AbsentPackageProof() {
  return {
    schemaVersion: 1,
    classification: "verified_absent_package_fail_closed",
    packagePresent: false,
    providerId: "kokoro-local-onnx",
    modelId: "onnx-community/Kokoro-82M-v1.0-ONNX",
    modelPackageId: "kokoro-82m-v1.0-onnx-q8-af-heart",
    modelPackageVersion:
      "1.0.0+1939ad2a8e416c0acfeecc08a694d14ef25f2231",
    manifestFingerprint:
      "03702762c09a71ee54b7ea3bfa4939d1c622b01d68709e2180a39ca62ec264b0",
    runtimeBindingStatus: "unavailable",
    runtimeBindingReason: "VERIFIED_ACTIVE_MODEL_PACKAGE_REQUIRED",
    sessionEvidencePresent: false,
    generationRequestPresent: false,
    counts: {
      auditionSessions: 0,
      providerDispatches: 0,
      runtimeInstances: 0,
      audioArtifacts: 0,
      auditionClips: 0,
    },
    fallbackUsed: false,
    productionExportEligible: false,
  };
}

function phase3bPackagedResult(launches) {
  const hash = (label) => fingerprint(`phase3b-${label}`);
  const assignmentIdByRoleId = Object.freeze({
    "role-primary-narrator": "assignment-narrator-1",
    "role-character-1": "assignment-character-1",
    "role-character-2": "assignment-character-2",
  });
  const audition = (roleType, roleId, clipId, artifactId, artifactHash) => ({
    roleId,
    roleType,
    assignmentId: assignmentIdByRoleId[roleId],
    assignmentRevision: 3,
    voiceRuntimeBindingId: `binding-${roleId}`,
    voiceRuntimeBindingFingerprint: hash(`binding-${roleId}`),
    providerVoiceId: `fixture-voice-${roleId}`,
    auditionSessionId: `session-${roleId}`,
    providerRequestId: `provider-request-${roleId}`,
    requestFingerprint: hash(`request-${roleId}`),
    executionClassification: "provider_execution",
    providerDispatchCount: 1,
    sourceProviderRequestId: null,
    runtimeInstanceId: "runtime-instance-1",
    normalizedTextSha256: hash(`normalized-${roleId}`),
    pronunciationPlanFingerprint: hash(`pronunciation-plan-${roleId}`),
    cacheKey: hash(`cache-${roleId}`),
    cacheStatus: "miss",
    auditionClipId: clipId,
    clipFingerprint: hash(`clip-${roleId}`),
    audioArtifactId: artifactId,
    audio: {
      mediaType: "audio/wav",
      codec: "pcm_s16le",
      sampleRateHz: 24_000,
      channels: 1,
      sampleWidthBytes: 2,
      durationMilliseconds: 1_000,
      byteSize: 48_044,
      sha256: artifactHash,
      nonSilencePassed: true,
      clippingPassed: true,
      blockingFindingCount: 0,
    },
    authenticatedAudioLoaded: true,
    fixtureEvidenceOnly: true,
  });
  const auditions = [
    audition(
      "narrator",
      "role-primary-narrator",
      "audition-clip-narrator",
      "audio-artifact-narrator",
      hash("audio-narrator"),
    ),
    audition(
      "character",
      "role-character-1",
      "audition-clip-character-1",
      "audio-artifact-character-1",
      hash("audio-character-1"),
    ),
    audition(
      "character",
      "role-character-2",
      "audition-clip-character-2",
      "audio-artifact-character-2",
      hash("audio-character-2"),
    ),
  ];
  auditions.push(
    {
      ...audition(
        "narrator",
        "role-primary-narrator",
        "audition-clip-narrator-cache-hit",
        "audio-artifact-narrator-cache-hit",
        auditions[0].audio.sha256,
      ),
      providerRequestId: "provider-request-role-primary-narrator-cache-hit",
      requestFingerprint: hash("request-narrator-cache-hit"),
      executionClassification: "verified_cache_lookup",
      providerDispatchCount: 0,
      sourceProviderRequestId: auditions[0].providerRequestId,
      runtimeInstanceId: null,
      cacheKey: auditions[0].cacheKey,
      cacheStatus: "verified_hit",
    },
    {
      ...audition(
        "character",
        "role-character-1",
        "audition-clip-character-1-regenerated",
        "audio-artifact-character-1-regenerated",
        hash("audio-character-1-regenerated"),
      ),
      requestFingerprint: hash("request-character-1-regenerated"),
      cacheKey: hash("cache-character-1-regenerated"),
    },
  );
  const gate = (gateId, roleId, index) => ({
    gateId,
    reviewId: `audition-review-${index}`,
    decisionId: `audition-decision-${index}`,
    roleId,
    evidenceFingerprint: hash(`gate-evidence-${index}`),
    decision: "approved",
    immutable: true,
  });
  const runtimeExits = launches.map((launch, index) => {
    const worker = launch.ownership.processes.find(
      (process) => process.kind === "provider_worker",
    );
    const service = launch.ownership.processes.find(
      (process) => process.kind === "service",
    );
    if (worker === undefined || service === undefined) {
      throw new Error("The fixture runtime process tree was incomplete.");
    }
    return {
      runtimeInstanceId: `runtime-instance-${index + 1}`,
      workerPid: worker.pid,
      parentPid: service.pid,
      state: "stopped",
      stoppedAt: COMPLETED_AT,
      stopReasonCode: "clean",
      handshakeAuthenticated: true,
      shutdownAcknowledged: true,
      gracefulShutdownConfirmed: true,
      exitCode: 0,
      terminatedByParent: false,
      ownershipConfirmed: true,
      confirmedExited: true,
      ownedProcessesConfirmedExited: true,
      jobObjectAssigned: true,
      deniedNetworkAttemptCount: 0,
    };
  });
  return {
    schemaVersion: "8.0.0",
    completedAt: COMPLETED_AT,
    status: "passed",
    evidenceClassification: "deterministic_fixture_lifecycle_only",
    fixtureClaims: {
      lifecycleEvidenceOnly: true,
      naturalSpeechQualityProven: false,
      productionExportEligible: false,
      humanListeningClaimed: false,
    },
    phase3b1SyntheticMetadata: phase3b1SyntheticMetadata(),
    runtime: {
      profileId: "deterministic-pcm-wav-fixture-windows-v1-0-1",
      profileFingerprint:
        "f3e5801f836ab4061eb76e52f4a3e1b4c7ba162238e0c70857e74fff705f75d6",
      protocolVersion: "1.0.0",
      runtimeInstanceIds: ["runtime-instance-1", "runtime-instance-2"],
      networkPolicy: "python_socket_api_denied",
      observedNetworkRequestCount: null,
      externalNetworkObservation: {
        method: "owned_pid_tcp_endpoint_inventory",
        ownedPidsOnly: true,
        observedNonLoopbackEndpointCount: 0,
      },
    },
    fixtureProvider: {
      providerId: "deterministic-pcm-wav-fixture",
      providerVersion: "1.0.0",
    },
    realProviderAdapter: {
      providerId: "kokoro-local-onnx",
      providerVersion: "1.0.0",
    },
    model: {
      modelPackageId: "deterministic-pcm-wav-fixture-package",
      manifestVersion: "1.0.0",
      modelPackageFingerprint:
        "e0352282af67ff3675fe6067a63feca5d9d4fcaeef5a3f12b80a5e4d2c9635d6",
      installationRevision: 2,
      verificationId: "model-verification-1",
      verificationFingerprint: hash("model-verification"),
      verified: true,
      active: true,
    },
    pronunciation: {
      dictionaryId: "pronunciation-dictionary-1",
      initialRevision: 2,
      initialFingerprint: hash("dictionary-initial"),
      initialEntryId: "pronunciation-entry-1",
      initialEntryFingerprint: hash("pronunciation-entry-1"),
      initialDecision: "approved",
      supersedingEntryId: "pronunciation-entry-2",
      supersedingEntryFingerprint: hash("pronunciation-entry-2"),
      supersedesEntryId: "pronunciation-entry-1",
      supersedingDecision: "approved",
      finalRevision: 4,
      finalFingerprint: hash("dictionary-final"),
    },
    auditions,
    cacheHit: {
      originalClipId: auditions[0].auditionClipId,
      repeatedClipId: "audition-clip-narrator-cache-hit",
      originalRequestFingerprint: auditions[0].requestFingerprint,
      repeatedRequestFingerprint: hash("request-narrator-cache-hit"),
      originalCacheKey: auditions[0].cacheKey,
      repeatedCacheKey: auditions[0].cacheKey,
      artifactSha256: auditions[0].audio.sha256,
      repeatedArtifactSha256: auditions[0].audio.sha256,
      repeatedCacheStatus: "verified_hit",
      identicalCacheKeyInputsProven: true,
      lookupOnlyNoProviderExecutionProven: true,
    },
    targetedInvalidation: {
      supersededEntryId: "pronunciation-entry-1",
      supersedingEntryId: "pronunciation-entry-2",
      beforeDictionaryFingerprint: hash("dictionary-initial"),
      afterDictionaryFingerprint: hash("dictionary-final"),
      impactedRoleId: "role-character-1",
      priorRequestFingerprint: auditions[1].requestFingerprint,
      regeneratedRequestFingerprint: hash("request-character-1-regenerated"),
      priorCacheKey: auditions[1].cacheKey,
      regeneratedCacheKey: hash("cache-character-1-regenerated"),
      priorArtifactSha256: auditions[1].audio.sha256,
      regeneratedArtifactSha256: hash("audio-character-1-regenerated"),
      invalidatedClipIds: [auditions[1].auditionClipId],
      preservedClipIds: [
        auditions[0].auditionClipId,
        auditions[2].auditionClipId,
      ],
      persistedInvalidatedGateStates: [
        {
          gateId: "pronunciation_review",
          reviewId: "audition-review-pronunciation-invalidated",
          state: "pending",
          evidenceFingerprint: hash("pronunciation-invalidated-evidence"),
        },
        {
          gateId: "voice_readiness_review",
          reviewId: "audition-review-readiness-invalidated",
          state: "blocked",
          evidenceFingerprint: hash("readiness-invalidated-evidence"),
        },
      ],
      targetedOnly: true,
    },
    gateDecisions: [
      gate("per_role_audition_review", "role-primary-narrator", 1),
      gate("per_role_audition_review", "role-character-1", 2),
      gate("per_role_audition_review", "role-character-2", 3),
      gate("narrator_audition_review", null, 4),
      gate("character_audition_review", null, 5),
      gate("pronunciation_review", null, 6),
      gate("voice_readiness_review", null, 7),
    ],
    restart: {
      runtimeProfilePersisted: true,
      modelVerificationPersisted: true,
      pronunciationDictionaryPersisted: true,
      pronunciationDecisionsPersisted: true,
      auditionSessionsPersisted: true,
      auditionScriptsPersisted: true,
      auditionClipsPersisted: true,
      cacheRecordsPersisted: true,
      audioQualityRecordsPersisted: true,
      auditionDecisionsPersisted: true,
      voiceReadinessDecisionPersisted: true,
      authenticatedRestoredAudioLoaded: true,
      priorLaunchRuntimeExit: runtimeExits[0],
    },
    process: {
      launches: launches.map((launch, index) => ({
        launch: launch.launch,
        ownedProcesses: launch.ownership.processes.map((process) => ({
          pid: process.pid,
          parentPid: process.parentPid,
          kind: process.kind === "app" ? "electron" : process.kind,
          executableName: process.executableName,
          creationIdentity: process.creationDate,
          goneAfterShutdown: true,
        })),
        providerRuntimeExit: runtimeExits[index],
        forcedPids: [],
        remainingPids: [],
        unrelatedProcessesInspected: false,
        unrelatedProcessesTerminated: false,
      })),
    },
    screenshot: {
      artifactId: "packaged-ui-screenshot",
      captured: true,
    },
    assertions: {
      phase0ThroughPhase3aPrerequisitesCurrent: true,
      fixtureProviderClearlyClassified: true,
      modelVerifiedAndActivated: true,
      pronunciationDecisionApproved: true,
      narratorAndTwoCharacterAuditionsGenerated: true,
      authenticatedWavLoadsPassed: true,
      verifiedCacheHitProven: true,
      targetedInvalidationProven: true,
      fiveGateTypesApproved: true,
      restartPersistenceProven: true,
      runtimeNetworkPolicyAndOwnedPidEndpointObservationProven: true,
      electronServiceAndProviderWorkerOwnershipProven: true,
      allExactOwnedProcessesExited: true,
      noUnrelatedProcessInspectedOrTerminated: true,
    },
  };
}

test("writes stable relative-path evidence for a successful packaged gate", async (t) => {
  const fixture = await createFixture(t);
  const options = generationOptions(fixture, "success");

  const first = await generateBuildEvidence(options);
  const firstBytes = await readFile(first.manifestPath, "utf8");
  const second = await generateBuildEvidence(options);
  const secondBytes = await readFile(second.manifestPath, "utf8");

  assert.equal(secondBytes, firstBytes);
  assert.equal(first.manifest.schemaVersion, "7.0.0");
  assert.equal(first.manifest.artifactPathScope, "repository-root");
  assert.equal(first.manifest.workflowHeadSha, WORKFLOW_HEAD_SHA);
  assert.equal(first.manifest.testedCheckoutSha, WORKFLOW_HEAD_SHA);
  assert.equal(first.manifest.pullRequestHeadSha, PR_HEAD_SHA);
  assert.equal(first.manifest.appVersion, APP_VERSION);
  assert.deepEqual(first.manifest.artifacts.desktopApplication, {
    path:
      "apps/desktop/release/0.1.0/win-unpacked/Cinematic Story Studio.exe",
    sizeBytes: fixture.appBytes.length,
    sha256: sha256(fixture.appBytes),
  });
  assert.deepEqual(first.manifest.artifacts.stagedService, {
    path:
      "apps/desktop/build-resources/service/cinematic-story-service.exe",
    sizeBytes: fixture.serviceBytes.length,
    sha256: sha256(fixture.serviceBytes),
  });
  assert.deepEqual(first.manifest.artifacts.embeddedService, {
    path:
      "apps/desktop/release/0.1.0/win-unpacked/resources/service/cinematic-story-service.exe",
    sizeBytes: fixture.serviceBytes.length,
    sha256: sha256(fixture.serviceBytes),
  });
  assert.deepEqual(first.manifest.assertions, {
    stagedServiceMatchesEmbeddedService: true,
    packagedE2eHarnessResultMatchesStepOutcome: true,
    packagedE2eOwnershipExitProven: true,
    phase1DocxImportReviewProven: true,
    phase2ProfileAndAgentsProven: true,
    phase2ApprovedInputProven: true,
    phase2RunSnapshotAndStagesProven: true,
    phase2StoryAssertionsProven: true,
    phase2CorrectionsProven: true,
    phase2FourGateDecisionsProven: true,
    phase2DecisionRecordsPersisted: true,
    phase2RestartDurabilityProven: true,
    phase2WholeBookAnalysisProven: true,
    phase3bLocalSpeechAuditionsProven: true,
    phase3b1RealProviderFailsClosedWithoutPackageProven: true,
    publicArtifactInventoryExcludesModelVoiceAndAudioData: true,
    packagedE2eEvidenceComplete: true,
  });
  assert.deepEqual(first.manifest.phase3b1AbsentPackage, {
    testCase:
      "apps/local-service/tests/test_phase3b1_governed_real_voice_activation.py::test_real_provider_fails_closed_when_exact_package_is_absent",
    databaseIsolation: "fresh_pytest_temporary_directory",
    testResult: "passed",
    evidenceFile: {
      path:
        "apps/desktop/release/0.1.0/phase-3b1-absent-package-evidence.json",
      sizeBytes: fixture.phase3b1AbsentPackageEvidenceBytes.length,
      sha256: sha256(fixture.phase3b1AbsentPackageEvidenceBytes),
    },
    proof: phase3b1AbsentPackageProof(),
  });
  assert.deepEqual(
    first.manifest.secureIngest.supportedFormats,
    ["txt", "markdown", "docx", "epub", "pdf"],
  );
  assert.deepEqual(first.manifest.secureIngest.parserDependencies, [
    {
      name: "lxml",
      version: "6.1.1",
      license: "BSD-3-Clause",
      purpose:
        "Strict XML parsing for validated DOCX and EPUB package parts.",
    },
    {
      name: "pypdf",
      version: "6.14.2",
      license: "BSD-3-Clause",
      purpose:
        "Bounded page-aware text extraction from text-based PDF files.",
    },
  ]);
  assert.equal(
    first.manifest.secureIngest.syntheticDocx.decodedSha256,
    SYNTHETIC_DOCX_SHA256,
  );
  assert.deepEqual(first.manifest.secureIngest.boundaryLimits, {
    sourceBytes: 100 * 1024 * 1024,
    previewCodePoints: 8_000,
  });
  assert.deepEqual(first.manifest.secureIngest.parserProfile, {
    values: PARSER_PROFILE,
    canonicalJson: PARSER_PROFILE_CANONICAL_JSON,
    fingerprint: PARSER_PROFILE_FINGERPRINT,
  });
  assert.equal(
    sha256(
      Buffer.from(
        first.manifest.secureIngest.parserProfile.canonicalJson,
        "utf8",
      ),
    ),
    first.manifest.secureIngest.parserProfile.fingerprint,
  );
  assert.equal(first.manifest.packagedE2e.result, "passed");
  assert.deepEqual(first.manifest.packagedE2e.importReview, {
    format: "docx",
    sourceSha256: SYNTHETIC_DOCX_SHA256,
    extractedTextSha256: EXTRACTED_TEXT_SHA256,
    extractionRevision: 1,
    warningCount: 0,
    approvalDecision: "approved",
    approvalPersistedAfterRestart: true,
    extractionPersistedAfterRestart: true,
    analysisPersistedAfterRestart: true,
  });
  assert.deepEqual(
    first.manifest.packagedE2e.machineResult.completedLaunches,
    [1, 2],
  );
  assert.deepEqual(
    first.manifest.packagedE2e.storyAnalysis,
    storyAnalysisEvidence(),
  );
  assert.equal(
    first.manifest.storyAnalysisContract.profile.fingerprint,
    PROFILE_FINGERPRINT,
  );
  assert.equal(
    first.manifest.storyAnalysisContract.fixture.expectedSpanCount >=
      40,
    true,
  );
  assert.equal(
    first.manifest.packagedE2e.machineResult.applicationLaunchBegan,
    true,
  );
  assert.equal(
    first.manifest.packagedE2e.machineResult.ownershipEstablished,
    true,
  );
  assert.equal(
    first.manifest.packagedE2e.machineResult.cleanupCompleted,
    true,
  );
  assert.equal(
    first.manifest.packagedE2e.machineResult.failureStage,
    null,
  );
  assert.equal(
    first.manifest.packagedE2e.machineResult.failureCode,
    null,
  );
  assert.equal(
    first.manifest.packagedE2e.screenshot.path,
    "apps/desktop/release/0.1.0/packaged-e2e.png",
  );
  assert.equal(
    first.manifest.packagedE2e.machineResult.path,
    "apps/desktop/release/0.1.0/packaged-e2e-result.json",
  );
  assert.deepEqual(first.manifest.packagedE2e.launches, [
    launchEvidence(1, 4100, 4101),
    launchEvidence(2, 4200, 4201),
  ]);
  assert.equal(
    first.manifest.voiceCastingContract.castingProfile.profileId,
    CASTING_PROFILE_ID,
  );
  assert.equal(
    first.manifest.voiceCastingContract.castingProfile.fingerprint,
    CASTING_PROFILE_FINGERPRINT,
  );
  assert.equal(
    first.manifest.voiceCastingContract.packagedE2e.schemaVersion,
    "5.0.0",
  );
  assert.deepEqual(
    first.manifest.voiceCastingContract.packagedE2e.flow,
    PHASE_3_PACKAGED_FLOW,
  );
  assert.deepEqual(
    first.manifest.voiceCastingContract.packagedE2e.processOwnership
      .launchShutdowns,
    [
      {
        launch: 1,
        electron: {
          launcherPid: 5100,
          rootPid: 4100,
          exitCode: 0,
          forceKillUsed: false,
        },
        service: {
          pid: 4101,
          method: "stdin_eof",
          exitCode: 0,
          signalCode: null,
          forceKillUsed: false,
        },
      },
      {
        launch: 2,
        electron: {
          launcherPid: 5200,
          rootPid: 4200,
          exitCode: 0,
          forceKillUsed: false,
        },
        service: {
          pid: 4201,
          method: "stdin_eof",
          exitCode: 0,
          signalCode: null,
          forceKillUsed: false,
        },
      },
    ],
  );
  assert.equal(
    first.manifest.voiceCastingContract.catalogRevision.fingerprint,
    GOVERNED_CATALOG_FINGERPRINT,
  );
  assert.deepEqual(
    first.manifest.voiceCastingContract.counts,
    {
      productionRoles: 3,
      narratorRoles: 1,
      characterRoles: 2,
      preReductionCandidates: 42,
      finalCandidates: 30,
      conflicts: 1,
      assignments: 3,
      corrections: 7,
    },
  );
  assert.deepEqual(
    first.manifest.voiceCastingContract.assignments,
    {
      narratorAssignmentId: "assignment-narrator-1",
      characterAssignmentIds: [
        "assignment-character-1",
        "assignment-character-2",
      ],
      narratorAssignment: {
        assignmentId: "assignment-narrator-1",
        roleId: "role-primary-narrator",
        roleType: "primary_narrator",
        ...syntheticCatalogAssignmentEvidence(
          "synthetic-narrator-02",
        ),
        castingProfileFingerprint: CASTING_PROFILE_FINGERPRINT,
        phase2SnapshotFingerprint: fingerprint("analysis-snapshot-4"),
        effectiveCorrectionSetFingerprint: fingerprint(
          "casting-corrections-narrator",
        ),
        authority: "human_locked",
        revision: 3,
        supersedesAssignmentId: "assignment-narrator-selection",
      },
      characterAssignments: [
        {
          assignmentId: "assignment-character-1",
          roleId: "role-character-1",
          roleType: "named_character",
          ...syntheticCatalogAssignmentEvidence(
            "synthetic-character-01",
          ),
          castingProfileFingerprint: CASTING_PROFILE_FINGERPRINT,
          phase2SnapshotFingerprint: fingerprint(
            "analysis-snapshot-4",
          ),
          effectiveCorrectionSetFingerprint: fingerprint(
            "casting-corrections-character-1",
          ),
          authority: "human_locked",
          revision: 3,
          supersedesAssignmentId: "assignment-character-1-selection",
        },
        {
          assignmentId: "assignment-character-2",
          roleId: "role-character-2",
          roleType: "named_character",
          ...syntheticCatalogAssignmentEvidence(
            "synthetic-character-02",
          ),
          castingProfileFingerprint: CASTING_PROFILE_FINGERPRINT,
          phase2SnapshotFingerprint: fingerprint(
            "analysis-snapshot-4",
          ),
          effectiveCorrectionSetFingerprint: fingerprint(
            "casting-corrections-character-2",
          ),
          authority: "human_locked",
          revision: 3,
          supersedesAssignmentId: "assignment-character-2-selection",
        },
      ],
    },
  );
  assert.equal(first.manifest.testTimestamp, COMPLETED_AT);
  assert.deepEqual(first.manifest.runner, RUNNER);
  assert.equal(firstBytes.endsWith("\n"), true);
  assert.equal(firstBytes.includes(fixture.root), false);
  for (const privateExcerpt of collectExactStoryText(
    SYNTHETIC_EXPECTATIONS,
  )) {
    assert.equal(
      objectContainsString(first.manifest, privateExcerpt),
      false,
      "build evidence must not contain manuscript excerpts",
    );
  }
  assert.equal(firstBytes.includes('"cacheKey"'), true);
  assert.equal(
    firstBytes.includes(
      fingerprint("phase3b-cache-role-primary-narrator"),
    ),
    true,
    "build evidence must contain the deterministic hashed cache identity",
  );
  const workflow = await readFile(
    new URL("../../.github/workflows/ci.yml", import.meta.url),
    "utf8",
  );
  const desktopEvidenceSource = await readFile(
    new URL(
      "../../apps/desktop/src/verification/packaged-e2e-evidence.ts",
      import.meta.url,
    ),
    "utf8",
  );
  const phase3DesktopEvidenceSource = await readFile(
    new URL(
      "../../apps/desktop/src/verification/phase3-packaged-e2e-evidence.ts",
      import.meta.url,
    ),
    "utf8",
  );
  const packagedRunnerSource = await readFile(
    new URL(
      "../../apps/desktop/scripts/run-packaged-e2e.mjs",
      import.meta.url,
    ),
    "utf8",
  );
  const desktopFlowStart = desktopEvidenceSource.indexOf(
    "export const packagedFlow",
  );
  const desktopFlowEnd = desktopEvidenceSource.indexOf(
    "]);",
    desktopFlowStart,
  );
  assert.notEqual(desktopFlowStart, -1);
  assert.notEqual(desktopFlowEnd, -1);
  assert.deepEqual(
    [
      ...desktopEvidenceSource
        .slice(desktopFlowStart, desktopFlowEnd)
        .matchAll(/"([a-z][a-z0-9_]*)"/gu),
    ].map((match) => match[1]),
    PACKAGED_FLOW,
  );
  const phase3FlowStart = phase3DesktopEvidenceSource.indexOf(
    "export const phase3PackagedFlow",
  );
  const phase3FlowEnd = phase3DesktopEvidenceSource.indexOf(
    "] as const",
    phase3FlowStart,
  );
  assert.notEqual(phase3FlowStart, -1);
  assert.notEqual(phase3FlowEnd, -1);
  assert.deepEqual(
    [
      ...phase3DesktopEvidenceSource
        .slice(phase3FlowStart, phase3FlowEnd)
        .matchAll(/"([a-z][a-z0-9_]*)"/gu),
    ].map((match) => match[1]),
    PHASE_3_PACKAGED_FLOW,
  );
  const workflowStep = (name) => {
    const marker = `      - name: ${name}`;
    const start = workflow.indexOf(marker);
    assert.notEqual(start, -1, `missing workflow step: ${name}`);
    const next = workflow.indexOf("\n      - name:", start + marker.length);
    return workflow.slice(start, next === -1 ? undefined : next);
  };
  const developmentPreparationIndex = workflow.indexOf(
    "- name: Prepare development Electron assets",
  );
  const developmentGateIndex = workflow.indexOf(
    "id: development_e2e",
  );
  const buildIndex = workflow.indexOf("id: build");
  const frozenRuntimeGateIndex = workflow.indexOf(
    "- name: Verify exact staged frozen service runtime",
  );
  const resolveEvidenceIndex = workflow.indexOf(
    "- name: Resolve packaged E2E evidence paths",
  );
  const absentPackageGateIndex = workflow.indexOf(
    "id: phase3b1_absent_package",
  );
  const packagedGateIndex = workflow.indexOf("id: packaged_e2e");

  assert.notEqual(developmentPreparationIndex, -1);
  assert.notEqual(developmentGateIndex, -1);
  assert.notEqual(buildIndex, -1);
  assert.equal(
    developmentGateIndex > developmentPreparationIndex,
    true,
  );
  assert.equal(buildIndex > developmentGateIndex, true);
  assert.equal(frozenRuntimeGateIndex > buildIndex, true);
  assert.equal(resolveEvidenceIndex > frozenRuntimeGateIndex, true);
  assert.equal(absentPackageGateIndex > resolveEvidenceIndex, true);
  assert.equal(packagedGateIndex > absentPackageGateIndex, true);
  for (const environmentName of [
    "CSS_PACKAGED_E2E_EXECUTABLE",
    "CSS_PACKAGED_E2E_EVIDENCE_PATH",
    "CSS_PACKAGED_E2E_RESULT_PATH",
    "CSS_PHASE3_PACKAGED_E2E_RESULT_PATH",
    "CSS_PHASE3_VOICE_CASTING_EVIDENCE_PATH",
    "CSS_PHASE3B_PACKAGED_E2E_RESULT_PATH",
  ]) {
    assert.match(workflow, new RegExp(environmentName, "u"));
    assert.match(
      packagedRunnerSource,
      new RegExp(environmentName, "u"),
    );
  }
  assert.match(
    workflow,
    /CSS_PHASE3B1_ABSENT_PACKAGE_EVIDENCE_PATH/u,
  );
  assert.match(
    workflow,
    /CSS_PHASE3B1_ABSENT_PACKAGE_STEP_OUTCOME/u,
  );
  assert.match(
    workflow,
    /apps\/desktop\/release\/\$version/u,
  );
  assert.match(
    workflow,
    /win-unpacked\/Cinematic Story Studio\.exe/u,
  );
  assert.match(workflow, /node scripts\/ci\/build-evidence\.mjs/u);
  assert.match(workflow, /name: Phase 3B\.1 Windows CI/u);
  assert.match(workflow, /group: phase-3b1-windows-/u);
  assert.match(
    workflow,
    /--validate-manifest \$env:CSS_BUILD_EVIDENCE_MANIFEST_PATH/u,
  );
  for (const focusedTest of [
    "test_audio_qc.py",
    "test_database_v3_migration.py",
    "test_whole_book_analysis.py",
    "test_phase2_api.py",
    "test_analysis_corrections_restart.py",
    "test_phase2_correction_scope_regressions.py",
    "test_phase2_structure_graph_regressions.py",
    "test_phase2_scale.py",
    "test_database_v4_migration.py",
    "test_phase3a_casting.py",
    "test_phase3a_custom_roles.py",
    "test_phase3a_assignment_invalidation.py",
    "test_phase3a_governance.py",
    "test_phase3a_jobs.py",
    "test_phase3a_scale.py",
    "test_database_v5_migration.py",
    "test_phase3b_model_packages.py",
    "test_phase3b_model_transaction_compensation.py",
    "test_phase3b_api.py",
    "test_phase3b_pronunciation.py",
    "test_phase3b_pronunciation_lineage.py",
    "test_phase3b_audition_jobs.py",
    "test_phase3b_audition_repository.py",
    "test_phase3b_auditions.py",
    "test_phase3b_workflow.py",
    "test_phase3b_speech_runtime.py",
    "test_phase3b_speech_providers.py",
    "test_phase3b_real_provider.py",
    "test_phase3b_real_provider_command.py",
    "test_phase3b_atomic_publication.py",
    "test_phase3b_integrity_closure.py",
    "test_phase3b_review_idempotency.py",
    "test_phase3b_review_integrity.py",
    "test_phase3b_invalidation_reconciliation.py",
    "test_phase3b_text_normalization.py",
    "test_phase3b_schemas.py",
    "test_phase3b_scale.py",
    "test_phase3b1_governed_real_voice_activation.py",
  ]) {
    assert.match(workflow, new RegExp(focusedTest, "u"));
  }
  assert.match(
    workflow,
    /cinematic-story-studio-phase-3b1-windows-unpacked-/u,
  );
  assert.match(
    workflow,
    /phase-3-packaged-e2e-result\.json/u,
  );
  assert.match(
    workflow,
    /phase-3-voice-casting-evidence\.json/u,
  );
  assert.match(
    workflow,
    /phase-3b-packaged-e2e-result\.json/u,
  );
  assert.match(
    workflow,
    /phase-3b1-absent-package-evidence\.json/u,
  );
  assert.match(workflow, /sys\.version_info\[:3\] == \(3, 12, 10\)/u);
  assert.match(workflow, /piptools compile --allow-unsafe --generate-hashes --strip-extras/u);
  assert.match(workflow, /git diff --exit-code -- apps\/local-service\/requirements\.lock/u);
  assert.match(workflow, /id: resolve_evidence/u);
  assert.match(
    workflow,
    /"version=\$version".*\$env:GITHUB_OUTPUT/u,
  );
  assert.match(
    workflow,
    /github\.event\.pull_request\.head\.sha \|\| github\.sha/u,
  );
  assert.match(
    workflowStep("Run development governed local-speech auditions Electron E2E"),
    /CSS_E2E: "1"/u,
  );
  assert.match(
    workflowStep("Run development governed local-speech auditions Electron E2E"),
    /playwright test tests\/e2e\/persistence\.spec\.ts/u,
  );
  const phase3b1BackendStep = workflowStep(
    "Run focused Phase 3B.1 governed real-voice backend suite",
  );
  assert.match(
    phase3b1BackendStep,
    /pytest -q apps\/local-service\/tests\/test_phase3b1_governed_real_voice_activation\.py/u,
  );
  const phase3b1AbsentPackageStep = workflowStep(
    "Prove exact real provider fails closed without model package",
  );
  assert.match(
    phase3b1AbsentPackageStep,
    /test_phase3b1_governed_real_voice_activation\.py::test_real_provider_fails_closed_when_exact_package_is_absent/u,
  );
  assert.match(
    phase3b1AbsentPackageStep,
    /CSS_PHASE3B1_ABSENT_PACKAGE_EVIDENCE_PATH/u,
  );
  const phase3b1PublicStep = workflowStep(
    "Run focused Phase 3B.1 desktop, schema, evidence, and repository-policy suites",
  );
  for (const focusedPublicTest of [
    "src/main/audition-validation.test.ts",
    "src/renderer/AuditionsWorkspace.test.tsx",
    "src/verification/phase3b-packaged-e2e-evidence.test.ts",
    "schemas/tests/phase3b-schema-structure.test.mjs",
    "tests/tooling/build-evidence.test.mjs",
    "tests/tooling/repository-policy.test.mjs",
  ]) {
    assert.match(
      phase3b1PublicStep,
      new RegExp(focusedPublicTest.replaceAll(".", "\\."), "u"),
    );
  }
  const frozenRuntimeStep = workflowStep(
    "Verify exact staged frozen service runtime",
  );
  assert.match(
    frozenRuntimeStep,
    /apps\/desktop\/build-resources\/service\/cinematic-story-service\.exe/u,
  );
  assert.match(
    frozenRuntimeStep,
    /CINEMATIC_STORY_TEST_FROZEN_SERVICE/u,
  );
  assert.match(
    frozenRuntimeStep,
    /test_phase3b_speech_runtime\.py -k frozen/u,
  );
  assert.match(
    workflowStep(
      "Run exact packaged governed local-speech auditions persistence E2E",
    ),
    /run test:e2e:packaged/u,
  );
  const printEvidenceStep = workflowStep(
    "Print sanitized build evidence",
  );
  assert.match(
    printEvidenceStep,
    /CSS_BUILD_EVIDENCE_MANIFEST_PATH/u,
  );
  assert.doesNotMatch(
    printEvidenceStep,
    /CSS_(?:PACKAGED_E2E_RESULT|PHASE3_PACKAGED_E2E_RESULT|PHASE3_VOICE_CASTING_EVIDENCE|PHASE3B_PACKAGED_E2E_RESULT|PHASE3B1_ABSENT_PACKAGE_EVIDENCE)_PATH/u,
    "raw packaged results must not be echoed into CI logs",
  );
  for (const [gateName, gateId, enforcementName] of [
    [
      "Run development governed local-speech auditions Electron E2E",
      "development_e2e",
      "Enforce development Electron E2E result",
    ],
    [
      "Run exact packaged governed local-speech auditions persistence E2E",
      "packaged_e2e",
      "Enforce packaged E2E result",
    ],
    [
      "Prove exact real provider fails closed without model package",
      "phase3b1_absent_package",
      "Enforce absent-package fail-closed result",
    ],
    [
      "Generate deterministic build-evidence manifest",
      "build_evidence",
      "Enforce build-evidence generation",
    ],
    [
      "Validate schema-v8 Phase 3B result and schema-v7 build manifest",
      "manifest_validation",
      "Enforce build-evidence manifest validation",
    ],
    [
      "Revalidate exact artifact bytes immediately before upload",
      "artifact_revalidation",
      "Enforce exact artifact revalidation",
    ],
  ]) {
    assert.match(
      workflowStep(gateName),
      /continue-on-error: true/u,
      `${gateId} must retain evidence before enforcement`,
    );
    assert.match(
      workflowStep(enforcementName),
      new RegExp(`steps\\.${gateId}\\.outcome != 'success'`, "u"),
      `${gateId} must have an explicit failing enforcement step`,
    );
  }
  const uploadIndex = workflow.indexOf(
    "- name: Upload short-lived development artifact",
  );
  const uploadStep = workflowStep(
    "Upload short-lived development artifact",
  );
  for (const prerequisiteGate of [
    "development_e2e",
    "phase3b1_absent_package",
    "packaged_e2e",
    "build_evidence",
    "manifest_validation",
    "tracked_scan",
    "clean_check",
    "artifact_revalidation",
  ]) {
    assert.match(
      uploadStep,
      new RegExp(
        `steps\\.${prerequisiteGate}\\.outcome == 'success'`,
        "u",
      ),
      `artifact upload must require successful ${prerequisiteGate}`,
    );
  }
  assert.match(uploadStep, /retention-days: 7/u);
  assert.match(uploadStep, /compression-level: 0/u);
  assert.match(uploadStep, /if-no-files-found: error/u);
  assert.match(
    uploadStep,
    /steps\.resolve_evidence\.outputs\.version/u,
  );
  assert.match(
    uploadStep,
    /win-unpacked\/\*\*/u,
  );
  assert.match(
    uploadStep,
    /apps\/desktop\/build-resources\/service\/cinematic-story-service\.exe/u,
  );
  assert.match(
    uploadStep,
    /phase-3b1-absent-package-evidence\.json/u,
  );
  assert.doesNotMatch(
    uploadStep,
    /release\/\*\/|build-resources\/service\/\*\*/u,
    "success artifacts must not include unvalidated release versions or the whole staging tree",
  );
  const diagnosticsIndex = workflow.indexOf(
    "- name: Upload failure diagnostics without application binaries",
  );
  const diagnosticsStep = workflowStep(
    "Upload failure diagnostics without application binaries",
  );
  assert.equal(
    diagnosticsIndex < uploadIndex,
    true,
    "failure diagnostics must be retained before the success-only artifact gate",
  );
  assert.match(
    diagnosticsStep,
    /always\(\) && !cancelled\(\)/u,
  );
  for (const failedGate of [
    "development_e2e",
    "phase3b1_absent_package",
    "packaged_e2e",
    "build_evidence",
    "manifest_validation",
    "tracked_scan",
    "clean_check",
    "artifact_revalidation",
  ]) {
    assert.match(
      diagnosticsStep,
      new RegExp(
        `steps\\.${failedGate}\\.outcome != 'success'`,
        "u",
      ),
      `diagnostics upload must cover failed ${failedGate}`,
    );
  }
  assert.match(
    diagnosticsStep,
    /phase-3b1-absent-package-evidence\.json/u,
  );
  assert.match(
    diagnosticsStep,
    /phase-3b1-ci-diagnostics-/u,
  );
  assert.match(
    diagnosticsStep,
    /apps\/desktop\/test-results\/\*\*/u,
  );
  assert.match(
    diagnosticsStep,
    /phase-3-packaged-e2e-result\.json/u,
  );
  assert.match(
    diagnosticsStep,
    /phase-3-voice-casting-evidence\.json/u,
  );
  assert.match(
    diagnosticsStep,
    /if-no-files-found: warn/u,
  );
  assert.match(diagnosticsStep, /retention-days: 7/u);
  assert.match(
    diagnosticsStep,
    /steps\.resolve_evidence\.outputs\.version/u,
  );
  assert.doesNotMatch(
    diagnosticsStep,
    /release\/\*\//u,
    "diagnostics must remain scoped to the validated application version",
  );
  assert.doesNotMatch(
    diagnosticsStep,
    /win-unpacked|build-resources\/service|\.exe\b|cinematic-story-service/u,
    "failure diagnostics must exclude packaged application and service binaries",
  );
  const artifactRevalidationIndex = workflow.indexOf(
    "- name: Revalidate exact artifact bytes immediately before upload",
  );
  assert.equal(
    artifactRevalidationIndex >
      workflow.indexOf("- name: Verify checks did not modify tracked files"),
    true,
    "artifact bytes must be revalidated after all post-build checks",
  );
  assert.equal(
    artifactRevalidationIndex < diagnosticsIndex,
    true,
    "artifact byte revalidation must be the final read gate before upload",
  );
  for (const enforcementName of [
    "Enforce packaged E2E result",
    "Enforce build-evidence generation",
    "Enforce build-evidence manifest validation",
    "Enforce exact artifact revalidation",
    "Enforce development Electron E2E result",
  ]) {
    assert.equal(
      workflow.indexOf(`- name: ${enforcementName}`) > uploadIndex,
      true,
      `${enforcementName} must run after evidence upload`,
    );
  }
  await assert.rejects(
    generateBuildEvidence({
      ...generationOptions(fixture, "success"),
      runner: {
        ...RUNNER,
        name: SYNTHETIC_EXPECTATIONS.dialogueLines[0].exactText
          .exactText,
      },
    }),
    /manifest contains private story text/u,
  );
  await assert.rejects(
    generateBuildEvidence({
      ...generationOptions(fixture, "success"),
      runner: {
        ...RUNNER,
        os: "Linux",
      },
    }),
    /runner does not match the Phase 3B\.1 Windows CI job/u,
  );
});

test("rejects model, voice, and audio data from the public artifact inventory", async (t) => {
  const fixture = await createFixture(t);
  const unpackedRoot = path.dirname(fixture.executablePath);
  await writeFile(
    path.join(unpackedRoot, "snapshot_blob.bin"),
    Buffer.from("electron-runtime-snapshot", "utf8"),
  );
  await generateBuildEvidence(generationOptions(fixture, "success"));

  for (const extension of [
    ".onnx",
    ".safetensors",
    ".pt",
    ".pth",
    ".npy",
    ".npz",
    ".bin",
    ".wav",
  ]) {
    const prohibitedPath = path.join(
      unpackedRoot,
      `private-real-provider-data${extension}`,
    );
    await writeFile(prohibitedPath, Buffer.from("private-data", "utf8"));
    await assert.rejects(
      generateBuildEvidence(generationOptions(fixture, "success")),
      /public artifact inventory contained prohibited model, voice, or audio data/u,
    );
    await rm(prohibitedPath);
  }
});

test("requires and hash-binds the exact fresh-database absent-package proof", async (t) => {
  const fixture = await createFixture(t);

  await rm(fixture.phase3b1AbsentPackageEvidencePath);
  await assert.rejects(
    generateBuildEvidence(generationOptions(fixture, "success")),
    /Phase 3B\.1 absent-package evidence is missing or unreadable/u,
  );

  await writeFile(
    fixture.phase3b1AbsentPackageEvidencePath,
    fixture.phase3b1AbsentPackageEvidenceBytes,
  );
  const failedTestOptions = generationOptions(fixture, "success");
  failedTestOptions.packagedE2e.phase3b1AbsentPackageStepOutcome =
    "failure";
  await assert.rejects(
    generateBuildEvidence(failedTestOptions),
    /Phase 3B\.1 absent-package test did not pass/u,
  );

  const tamperedProof = phase3b1AbsentPackageProof();
  tamperedProof.counts.providerDispatches = 1;
  await writeFile(
    fixture.phase3b1AbsentPackageEvidencePath,
    `${JSON.stringify(tamperedProof, null, 2)}\n`,
    "utf8",
  );
  await assert.rejects(
    generateBuildEvidence(generationOptions(fixture, "success")),
    /Phase 3B\.1 absent-package evidence is invalid or stale/u,
  );

  await writeFile(
    fixture.phase3b1AbsentPackageEvidencePath,
    fixture.phase3b1AbsentPackageEvidenceBytes,
  );
  const generated = await generateBuildEvidence(
    generationOptions(fixture, "success"),
  );
  await writeFile(
    fixture.phase3b1AbsentPackageEvidencePath,
    `${JSON.stringify(tamperedProof, null, 2)}\n`,
    "utf8",
  );
  await assert.rejects(
    validateBuildEvidenceManifest({
      repositoryRoot: fixture.root,
      manifestPath: generated.manifestPath,
    }),
    /Phase 3B\.1 absent-package evidence is invalid or stale/u,
  );
});

test("validates a complete Phase 3B manifest and rejects tampering", async (t) => {
  const fixture = await createFixture(t);
  const generated = await generateBuildEvidence(
    generationOptions(fixture, "success"),
  );

  const validated = await validateBuildEvidenceManifest({
    repositoryRoot: fixture.root,
    manifestPath: generated.manifestPath,
  });

  assert.equal(validated.manifestPath, generated.manifestPath);
  assert.equal(validated.manifest.schemaVersion, "7.0.0");
  assert.match(validated.manifestSha256, /^[a-f0-9]{64}$/u);

  const missingRepeatedAuditionTamper = structuredClone(generated.manifest);
  missingRepeatedAuditionTamper.localSpeechAuditionsContract.auditions =
    missingRepeatedAuditionTamper.localSpeechAuditionsContract.auditions.filter(
      (item) =>
        item.auditionClipId !==
        missingRepeatedAuditionTamper.localSpeechAuditionsContract.cacheHit
          .repeatedClipId,
    );
  await writeFile(
    generated.manifestPath,
    `${JSON.stringify(missingRepeatedAuditionTamper, null, 2)}\n`,
    "utf8",
  );
  await assert.rejects(
    validateBuildEvidenceManifest({
      repositoryRoot: fixture.root,
      manifestPath: generated.manifestPath,
    }),
    /Phase 3A voice-casting or Phase 3B local-speech evidence is invalid or stale/u,
  );

  const missingRegeneratedAuditionTamper = structuredClone(generated.manifest);
  missingRegeneratedAuditionTamper.localSpeechAuditionsContract.auditions =
    missingRegeneratedAuditionTamper.localSpeechAuditionsContract.auditions.filter(
      (item) =>
        item.requestFingerprint !==
        missingRegeneratedAuditionTamper.localSpeechAuditionsContract
          .targetedInvalidation.regeneratedRequestFingerprint,
    );
  await writeFile(
    generated.manifestPath,
    `${JSON.stringify(missingRegeneratedAuditionTamper, null, 2)}\n`,
    "utf8",
  );
  await assert.rejects(
    validateBuildEvidenceManifest({
      repositoryRoot: fixture.root,
      manifestPath: generated.manifestPath,
    }),
    /Phase 3A voice-casting or Phase 3B local-speech evidence is invalid or stale/u,
  );

  const cacheBindingTamper = structuredClone(generated.manifest);
  const repeatedClipId =
    cacheBindingTamper.localSpeechAuditionsContract.cacheHit.repeatedClipId;
  const repeatedAudition =
    cacheBindingTamper.localSpeechAuditionsContract.auditions.find(
      (item) => item.auditionClipId === repeatedClipId,
    );
  assert.ok(repeatedAudition);
  repeatedAudition.voiceRuntimeBindingFingerprint = "f".repeat(64);
  await writeFile(
    generated.manifestPath,
    `${JSON.stringify(cacheBindingTamper, null, 2)}\n`,
    "utf8",
  );
  await assert.rejects(
    validateBuildEvidenceManifest({
      repositoryRoot: fixture.root,
      manifestPath: generated.manifestPath,
    }),
    /Phase 3A voice-casting or Phase 3B local-speech evidence is invalid or stale/u,
  );

  for (const executionTamper of [
    { providerDispatchCount: 1 },
    { runtimeInstanceId: "runtime-instance-cache-lie" },
    { sourceProviderRequestId: "provider-request-unrelated" },
    { executionClassification: "provider_execution" },
  ]) {
    const cacheExecutionTamper = structuredClone(generated.manifest);
    const repeatedExecutionAudition =
      cacheExecutionTamper.localSpeechAuditionsContract.auditions.find(
        (item) =>
          item.auditionClipId ===
          cacheExecutionTamper.localSpeechAuditionsContract.cacheHit
            .repeatedClipId,
      );
    assert.ok(repeatedExecutionAudition);
    Object.assign(repeatedExecutionAudition, executionTamper);
    await writeFile(
      generated.manifestPath,
      `${JSON.stringify(cacheExecutionTamper, null, 2)}\n`,
      "utf8",
    );
    await assert.rejects(
      validateBuildEvidenceManifest({
        repositoryRoot: fixture.root,
        manifestPath: generated.manifestPath,
      }),
      /Phase 3A voice-casting or Phase 3B local-speech evidence is invalid or stale/u,
    );
  }

  const originalExecutionTamper = structuredClone(generated.manifest);
  const originalExecutionAudition =
    originalExecutionTamper.localSpeechAuditionsContract.auditions.find(
      (item) =>
        item.auditionClipId ===
        originalExecutionTamper.localSpeechAuditionsContract.cacheHit
          .originalClipId,
    );
  assert.ok(originalExecutionAudition);
  originalExecutionAudition.providerDispatchCount = 0;
  await writeFile(
    generated.manifestPath,
    `${JSON.stringify(originalExecutionTamper, null, 2)}\n`,
    "utf8",
  );
  await assert.rejects(
    validateBuildEvidenceManifest({
      repositoryRoot: fixture.root,
      manifestPath: generated.manifestPath,
    }),
    /Phase 3A voice-casting or Phase 3B local-speech evidence is invalid or stale/u,
  );

  const cacheAssertionTamper = structuredClone(generated.manifest);
  cacheAssertionTamper.localSpeechAuditionsContract.cacheHit
    .lookupOnlyNoProviderExecutionProven = false;
  await writeFile(
    generated.manifestPath,
    `${JSON.stringify(cacheAssertionTamper, null, 2)}\n`,
    "utf8",
  );
  await assert.rejects(
    validateBuildEvidenceManifest({
      repositoryRoot: fixture.root,
      manifestPath: generated.manifestPath,
    }),
    /Phase 3A voice-casting or Phase 3B local-speech evidence is invalid or stale/u,
  );

  const regeneratedBindingTamper = structuredClone(generated.manifest);
  const regeneratedRequestFingerprint =
    regeneratedBindingTamper.localSpeechAuditionsContract.targetedInvalidation
      .regeneratedRequestFingerprint;
  const regeneratedAudition =
    regeneratedBindingTamper.localSpeechAuditionsContract.auditions.find(
      (item) => item.requestFingerprint === regeneratedRequestFingerprint,
    );
  assert.ok(regeneratedAudition);
  regeneratedAudition.providerVoiceId = "fixture-voice-drift";
  await writeFile(
    generated.manifestPath,
    `${JSON.stringify(regeneratedBindingTamper, null, 2)}\n`,
    "utf8",
  );
  await assert.rejects(
    validateBuildEvidenceManifest({
      repositoryRoot: fixture.root,
      manifestPath: generated.manifestPath,
    }),
    /Phase 3A voice-casting or Phase 3B local-speech evidence is invalid or stale/u,
  );

  const cacheKeyLeakTamper = structuredClone(generated.manifest);
  cacheKeyLeakTamper.localSpeechAuditionsContract.cacheHit.originalCacheKey =
    "private manuscript cache material";
  await writeFile(
    generated.manifestPath,
    `${JSON.stringify(cacheKeyLeakTamper, null, 2)}\n`,
    "utf8",
  );
  await assert.rejects(
    validateBuildEvidenceManifest({
      repositoryRoot: fixture.root,
      manifestPath: generated.manifestPath,
    }),
    /unhashed cache-key value/u,
  );

  const leakTamper = structuredClone(generated.manifest);
  leakTamper.runner.name =
    SYNTHETIC_EXPECTATIONS.characters[0].firstMention.exactText;
  await writeFile(
    generated.manifestPath,
    `${JSON.stringify(leakTamper, null, 2)}\n`,
    "utf8",
  );
  await assert.rejects(
    validateBuildEvidenceManifest({
      repositoryRoot: fixture.root,
      manifestPath: generated.manifestPath,
    }),
    /manifest contains private story text/u,
  );

  const flowTamper = structuredClone(generated.manifest);
  flowTamper.packagedE2e.flow =
    flowTamper.packagedE2e.flow.filter(
      (action) => action !== "approve_whole_book_analysis_review",
    );
  await writeFile(
    generated.manifestPath,
    `${JSON.stringify(flowTamper, null, 2)}\n`,
    "utf8",
  );
  await assert.rejects(
    validateBuildEvidenceManifest({
      repositoryRoot: fixture.root,
      manifestPath: generated.manifestPath,
    }),
    /packaged E2E proof is invalid or stale/u,
  );

  const tampered = structuredClone(generated.manifest);
  tampered.artifacts.desktopApplication.sha256 = "0".repeat(64);
  await writeFile(
    generated.manifestPath,
    `${JSON.stringify(tampered, null, 2)}\n`,
    "utf8",
  );

  await assert.rejects(
    validateBuildEvidenceManifest({
      repositoryRoot: fixture.root,
      manifestPath: generated.manifestPath,
    }),
    /artifact hashes are stale/u,
  );
});

test("rejects stale or contradictory Phase 3A packaged evidence", async (t) => {
  const fixture = await createFixture(t);
  const phase3Result = JSON.parse(
    await readFile(fixture.phase3ResultPath, "utf8"),
  );
  phase3Result.processOwnership.serviceOwnedPids = [4101];
  await writeFile(
    fixture.phase3ResultPath,
    `${JSON.stringify(phase3Result)}\n`,
    "utf8",
  );
  await assert.rejects(
    generateBuildEvidence(generationOptions(fixture, "success")),
    /success without complete, valid machine evidence/u,
  );

  const nonzeroElectronExitResult = phase3PackagedResult(
    Buffer.from("png-evidence", "utf8"),
  );
  nonzeroElectronExitResult.processOwnership.launchShutdowns[0].electron.exitCode =
    1;
  await writeFile(
    fixture.phase3ResultPath,
    `${JSON.stringify(nonzeroElectronExitResult)}\n`,
    "utf8",
  );
  await assert.rejects(
    generateBuildEvidence(generationOptions(fixture, "success")),
    /success without complete, valid machine evidence/u,
  );

  const forcedShutdownResult = phase3PackagedResult(
    Buffer.from("png-evidence", "utf8"),
  );
  forcedShutdownResult.processOwnership.launchShutdowns[0].service.method =
    "force_kill";
  forcedShutdownResult.processOwnership.launchShutdowns[0].service.forceKillUsed =
    true;
  await writeFile(
    fixture.phase3ResultPath,
    `${JSON.stringify(forcedShutdownResult)}\n`,
    "utf8",
  );
  await assert.rejects(
    generateBuildEvidence(generationOptions(fixture, "success")),
    /success without complete, valid machine evidence/u,
  );

  const detachedServiceResult = phase3PackagedResult(
    Buffer.from("png-evidence", "utf8"),
  );
  detachedServiceResult.processOwnership.launchShutdowns[0].service.pid =
    4201;
  await writeFile(
    fixture.phase3ResultPath,
    `${JSON.stringify(detachedServiceResult)}\n`,
    "utf8",
  );
  await assert.rejects(
    generateBuildEvidence(generationOptions(fixture, "success")),
    /success without complete, valid machine evidence/u,
  );

  const invalidAssignmentResult = phase3PackagedResult(
    Buffer.from("png-evidence", "utf8"),
  );
  invalidAssignmentResult.casting.narratorAssignment.roleType =
    "named_character";
  await writeFile(
    fixture.phase3ResultPath,
    `${JSON.stringify(invalidAssignmentResult)}\n`,
    "utf8",
  );
  await assert.rejects(
    generateBuildEvidence(generationOptions(fixture, "success")),
    /success without complete, valid machine evidence/u,
  );

  const catalogDetachedAssignmentResult = phase3PackagedResult(
    Buffer.from("png-evidence", "utf8"),
  );
  catalogDetachedAssignmentResult.casting.narratorAssignment.voiceEvidenceFingerprint =
    "0".repeat(64);
  await Promise.all([
    writeFile(
      fixture.phase3ResultPath,
      `${JSON.stringify(catalogDetachedAssignmentResult)}\n`,
      "utf8",
    ),
    writeFile(
      fixture.phase3VoiceCastingEvidencePath,
      `${JSON.stringify(
        phase3VoiceCastingEvidence(catalogDetachedAssignmentResult),
      )}\n`,
      "utf8",
    ),
  ]);
  await assert.rejects(
    generateBuildEvidence(generationOptions(fixture, "success")),
    /success without complete, valid machine evidence/u,
  );

  const repairedResult = phase3PackagedResult(
    Buffer.from("png-evidence", "utf8"),
  );
  await writeFile(
    fixture.phase3ResultPath,
    `${JSON.stringify(repairedResult)}\n`,
    "utf8",
  );
  const staleCastingEvidence = phase3VoiceCastingEvidence(
    repairedResult,
  );
  staleCastingEvidence.assignments.characterAssignments[0].voiceProfileId =
    "synthetic-character-03";
  await writeFile(
    fixture.phase3VoiceCastingEvidencePath,
    `${JSON.stringify(staleCastingEvidence)}\n`,
    "utf8",
  );
  await assert.rejects(
    generateBuildEvidence(generationOptions(fixture, "success")),
    /success without complete, valid machine evidence/u,
  );

  const staleCatalogEvidence = phase3VoiceCastingEvidence(
    repairedResult,
  );
  staleCatalogEvidence.catalogRevision.fingerprint = "0".repeat(64);
  await writeFile(
    fixture.phase3VoiceCastingEvidencePath,
    `${JSON.stringify(staleCatalogEvidence)}\n`,
    "utf8",
  );
  await assert.rejects(
    generateBuildEvidence(generationOptions(fixture, "success")),
    /success without complete, valid machine evidence/u,
  );

  const unboundedCorrectionEvidence = phase3VoiceCastingEvidence(
    repairedResult,
  );
  unboundedCorrectionEvidence.counts.corrections = 201;
  await writeFile(
    fixture.phase3VoiceCastingEvidencePath,
    `${JSON.stringify(unboundedCorrectionEvidence)}\n`,
    "utf8",
  );
  await assert.rejects(
    generateBuildEvidence(generationOptions(fixture, "success")),
    /success without complete, valid machine evidence/u,
  );
});

test("rejects noncanonical Phase 3B runtime provenance", async (t) => {
  const fixture = await createFixture(t);
  const original = JSON.parse(
    await readFile(fixture.phase3bResultPath, "utf8"),
  );
  const mutations = [
    ["legacy runtime profile ID", (value) => {
      value.runtime.profileId = "deterministic-pcm-wav-fixture-windows";
    }],
    ["arbitrary runtime profile fingerprint", (value) => {
      value.runtime.profileFingerprint = "f".repeat(64);
    }],
    ["arbitrary fixture provider ID", (value) => {
      value.fixtureProvider.providerId = "deterministic-pcm-wav-fixture-drift";
    }],
    ["arbitrary fixture provider version", (value) => {
      value.fixtureProvider.providerVersion = "1.0.1";
    }],
    ["arbitrary real provider ID", (value) => {
      value.realProviderAdapter.providerId = "kokoro-onnx-local";
    }],
    ["arbitrary real provider version", (value) => {
      value.realProviderAdapter.providerVersion = "1.0.1";
    }],
    ["arbitrary fixture package ID", (value) => {
      value.model.modelPackageId = "deterministic-pcm-wav-fixture-package-drift";
    }],
    ["arbitrary fixture manifest version", (value) => {
      value.model.manifestVersion = "1.0.1";
    }],
    ["arbitrary fixture package fingerprint", (value) => {
      value.model.modelPackageFingerprint = "f".repeat(64);
    }],
    ["stale governed catalog fingerprint", (value) => {
      value.phase3b1SyntheticMetadata.catalog.catalogRevisionFingerprint =
        "f".repeat(64);
    }],
    ["real synthesis overclaim", (value) => {
      value.phase3b1SyntheticMetadata.restriction.realSynthesisClaimed = true;
    }],
    ["real model bytes overclaim", (value) => {
      value.phase3b1SyntheticMetadata.artifactBoundary.realModelBytesPresent =
        true;
    }],
  ];
  for (const [label, mutate] of mutations) {
    const tampered = structuredClone(original);
    mutate(tampered);
    await writeFile(
      fixture.phase3bResultPath,
      `${JSON.stringify(tampered)}\n`,
      "utf8",
    );
    await assert.rejects(
      generateBuildEvidence(generationOptions(fixture, "success")),
      /complete, valid machine evidence/u,
      label,
    );
  }
});

test("rejects Phase 3B fixture quality overclaims and stale process proof", async (t) => {
  const fixture = await createFixture(t);
  const original = JSON.parse(
    await readFile(fixture.phase3bResultPath, "utf8"),
  );
  await writeFile(
    fixture.phase3bResultPath,
    `${JSON.stringify({
      ...original,
      fixtureClaims: {
        ...original.fixtureClaims,
        naturalSpeechQualityProven: true,
      },
    })}\n`,
    "utf8",
  );
  await assert.rejects(
    generateBuildEvidence(generationOptions(fixture, "success")),
    /complete, valid machine evidence/u,
  );

  const staleProcess = structuredClone(original);
  staleProcess.process.launches[0].ownedProcesses =
    staleProcess.process.launches[0].ownedProcesses.filter(
      (process) => process.kind !== "provider_worker",
    );
  await writeFile(
    fixture.phase3bResultPath,
    `${JSON.stringify(staleProcess)}\n`,
    "utf8",
  );
  await assert.rejects(
    generateBuildEvidence(generationOptions(fixture, "success")),
    /complete, valid machine evidence/u,
  );

  const unauthenticatedRuntimeExit = structuredClone(original);
  unauthenticatedRuntimeExit.process.launches[0]
    .providerRuntimeExit.shutdownAcknowledged = false;
  await writeFile(
    fixture.phase3bResultPath,
    `${JSON.stringify(unauthenticatedRuntimeExit)}\n`,
    "utf8",
  );
  await assert.rejects(
    generateBuildEvidence(generationOptions(fixture, "success")),
    /complete, valid machine evidence/u,
  );

  const mismatchedPersistedExit = structuredClone(original);
  mismatchedPersistedExit.restart.priorLaunchRuntimeExit.workerPid += 100;
  await writeFile(
    fixture.phase3bResultPath,
    `${JSON.stringify(mismatchedPersistedExit)}\n`,
    "utf8",
  );
  await assert.rejects(
    generateBuildEvidence(generationOptions(fixture, "success")),
    /complete, valid machine evidence/u,
  );

  const mismatchedAssignmentRevision = structuredClone(original);
  mismatchedAssignmentRevision.auditions[0].assignmentRevision += 1;
  await writeFile(
    fixture.phase3bResultPath,
    `${JSON.stringify(mismatchedAssignmentRevision)}\n`,
    "utf8",
  );
  await assert.rejects(
    generateBuildEvidence(generationOptions(fixture, "success")),
    /complete, valid machine evidence/u,
  );
});

test("rejects incomplete, duplicate, and wrongly scoped Phase 3B gate topology", async (t) => {
  const fixture = await createFixture(t);
  const original = JSON.parse(
    await readFile(fixture.phase3bResultPath, "utf8"),
  );
  const mutations = [
    (value) => {
      value.gateDecisions = value.gateDecisions.filter(
        (decision) => decision.roleId !== "role-character-2",
      );
    },
    (value) => {
      value.gateDecisions.push({
        ...value.gateDecisions[0],
        reviewId: "audition-review-extra-per-role",
        decisionId: "audition-decision-extra-per-role",
      });
    },
    (value) => {
      value.gateDecisions[0].roleId = "role-not-auditioned";
    },
    (value) => {
      value.gateDecisions[3].roleId = "role-primary-narrator";
    },
    (value) => {
      value.gateDecisions[1].reviewId =
        value.gateDecisions[0].reviewId;
    },
    (value) => {
      value.gateDecisions[1].decisionId =
        value.gateDecisions[0].decisionId;
    },
  ];

  for (const mutate of mutations) {
    const invalid = structuredClone(original);
    mutate(invalid);
    await writeFile(
      fixture.phase3bResultPath,
      `${JSON.stringify(invalid)}\n`,
      "utf8",
    );
    await assert.rejects(
      generateBuildEvidence(generationOptions(fixture, "success")),
      /complete, valid machine evidence/u,
    );
  }
});

test("rejects Phase 3B evidence that omits an additional Phase 3A cast role", async (t) => {
  const fixture = await createFixture(t);
  const phase3Result = JSON.parse(
    await readFile(fixture.phase3ResultPath, "utf8"),
  );
  const additionalAssignment = {
    ...structuredClone(
      phase3Result.casting.characterAssignments.at(-1),
    ),
    assignmentId: "assignment-character-3",
    roleId: "role-character-3",
    ...syntheticCatalogAssignmentEvidence("synthetic-character-03"),
    effectiveCorrectionSetFingerprint: fingerprint(
      "casting-corrections-character-3",
    ),
    supersedesAssignmentId: "assignment-character-3-selection",
  };
  phase3Result.casting.characterAssignmentIds.push(
    additionalAssignment.assignmentId,
  );
  phase3Result.casting.characterAssignments.push(additionalAssignment);
  const castingEvidence = phase3VoiceCastingEvidence(phase3Result);
  castingEvidence.counts.productionRoles = 4;
  castingEvidence.counts.characterRoles = 3;
  castingEvidence.counts.assignments = 4;
  await Promise.all([
    writeFile(
      fixture.phase3ResultPath,
      `${JSON.stringify(phase3Result)}\n`,
      "utf8",
    ),
    writeFile(
      fixture.phase3VoiceCastingEvidencePath,
      `${JSON.stringify(castingEvidence)}\n`,
      "utf8",
    ),
  ]);

  await assert.rejects(
    generateBuildEvidence(generationOptions(fixture, "success")),
    /complete, valid machine evidence/u,
  );
});

test("build evidence matches the canonical Python parser profile and fingerprint", async () => {
  const python = [
    "import json",
    "from cinematic_story_service.document_ingest import parser_limits_fingerprint, parser_limits_profile",
    "from cinematic_story_service.util import canonical_json",
    "profile = parser_limits_profile(30.0)",
    "print(json.dumps({'canonicalJson': canonical_json(profile), 'profile': profile, 'fingerprint': parser_limits_fingerprint(30.0)}, ensure_ascii=False, sort_keys=True, separators=(',', ':')))",
  ].join("; ");
  const result = await capture(servicePython, ["-c", python], {
    cwd: repositoryRoot,
    label: "Python parser-profile parity check",
    maxBytes: 4096,
    timeoutMs: 10_000,
  });
  assert.equal(result.code, 0);
  assert.equal(result.stderr, "");
  assert.deepEqual(JSON.parse(result.stdout), {
    canonicalJson: PARSER_PROFILE_CANONICAL_JSON,
    fingerprint: PARSER_PROFILE_FINGERPRINT,
    profile: PARSER_PROFILE,
  });
});

test("build evidence matches Python voice and rights fingerprints", async () => {
  const voiceProfileIds = [
    "synthetic-narrator-02",
    "synthetic-character-01",
    "synthetic-character-02",
  ];
  const python = [
    "import json",
    "from cinematic_story_service.casting import load_governed_voice_catalog",
    "from cinematic_story_service.util import request_fingerprint",
    `ids = ${JSON.stringify(voiceProfileIds)}`,
    "catalog = load_governed_voice_catalog()",
    "voices = {value['voiceProfileId']: value for value in catalog.voices}",
    "rights = {value['voiceProfileId']: value for value in catalog.rights}",
    "result = {value: {'voiceEvidenceFingerprint': request_fingerprint(voices[value]), 'rightsEvidenceFingerprint': request_fingerprint(rights[value])} for value in ids}",
    "print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(',', ':')))",
  ].join("; ");
  const result = await capture(servicePython, ["-c", python], {
    cwd: repositoryRoot,
    label: "Python voice-evidence fingerprint parity check",
    maxBytes: 4096,
    timeoutMs: 10_000,
  });
  assert.equal(result.code, 0);
  assert.equal(result.stderr, "");
  assert.deepEqual(
    JSON.parse(result.stdout),
    Object.fromEntries(
      voiceProfileIds.map((voiceProfileId) => {
        const evidence =
          syntheticCatalogAssignmentEvidence(voiceProfileId);
        return [
          voiceProfileId,
          {
            voiceEvidenceFingerprint:
              evidence.voiceEvidenceFingerprint,
            rightsEvidenceFingerprint:
              evidence.rightsEvidenceFingerprint,
          },
        ];
      }),
    ),
  );
});

test("accepts an accumulated transient service descendant when identity and exit proof agree", async (t) => {
  const fixture = await createFixture(t);
  const value = JSON.parse(await readFile(fixture.resultPath, "utf8"));
  value.launches[0].ownership.processes.push({
    pid: 4103,
    parentPid: 4101,
    kind: "service",
    executableName: "cinematic-story-service.exe",
    creationDate: "2026-07-29T18:15:21.5000000Z",
  });
  value.launches[0].exitProof.ownedPids.push(4103);
  await writeFile(
    fixture.resultPath,
    `${JSON.stringify(value)}\n`,
    "utf8",
  );
  const phase3Result = JSON.parse(
    await readFile(fixture.phase3ResultPath, "utf8"),
  );
  phase3Result.processOwnership.serviceOwnedPids.splice(1, 0, 4103);
  await writeFile(
    fixture.phase3ResultPath,
    `${JSON.stringify(phase3Result)}\n`,
    "utf8",
  );
  const castingEvidence = JSON.parse(
    await readFile(fixture.phase3VoiceCastingEvidencePath, "utf8"),
  );
  castingEvidence.packagedE2e = phase3Result;
  await writeFile(
    fixture.phase3VoiceCastingEvidencePath,
    `${JSON.stringify(castingEvidence)}\n`,
    "utf8",
  );
  const phase3bResult = JSON.parse(
    await readFile(fixture.phase3bResultPath, "utf8"),
  );
  phase3bResult.process.launches[0].ownedProcesses.push({
    pid: 4103,
    parentPid: 4101,
    kind: "service",
    executableName: "cinematic-story-service.exe",
    creationIdentity: "2026-07-29T18:15:21.5000000Z",
    goneAfterShutdown: true,
  });
  phase3bResult.process.launches[0].ownedProcesses.sort(
    (left, right) => left.pid - right.pid,
  );
  await writeFile(
    fixture.phase3bResultPath,
    `${JSON.stringify(phase3bResult)}\n`,
    "utf8",
  );

  const { manifest } = await generateBuildEvidence(
    generationOptions(fixture, "success"),
  );

  assert.equal(
    manifest.packagedE2e.machineResult.contractValid,
    true,
  );
  assert.deepEqual(
    manifest.packagedE2e.launches[0].exitProof.ownedPids,
    [4100, 4101, 4102, 4103],
  );
  assert.equal(
    manifest.assertions.packagedE2eOwnershipExitProven,
    true,
  );
});

test("rejects invalid machine process kinds and multiple app-service boundaries", async (t) => {
  const fixture = await createFixture(t);
  const original = await readFile(fixture.resultPath, "utf8");
  const machineResult = JSON.parse(original);
  const firstLaunch = machineResult.launches[0];
  firstLaunch.ownership.processes.push({
    pid: 4103,
    parentPid: 4101,
    kind: "app",
    executableName: "Cinematic Story Studio.exe",
    creationDate: "2026-07-29T18:15:21.7500000Z",
  });
  firstLaunch.exitProof.ownedPids.push(4103);
  await writeFile(
    fixture.resultPath,
    `${JSON.stringify(machineResult)}\n`,
    "utf8",
  );
  await assert.rejects(
    generateBuildEvidence(generationOptions(fixture, "success")),
    /complete, valid machine evidence/u,
  );

  const secondBoundaryResult = JSON.parse(original);
  const secondBoundaryLaunch = secondBoundaryResult.launches[0];
  secondBoundaryLaunch.ownership.processes.push({
    pid: 4103,
    parentPid: 4100,
    kind: "service",
    executableName: "cinematic-story-service.exe",
    creationDate: "2026-07-29T18:15:21.7500000Z",
  });
  secondBoundaryLaunch.exitProof.ownedPids.push(4103);
  await writeFile(
    fixture.resultPath,
    `${JSON.stringify(secondBoundaryResult)}\n`,
    "utf8",
  );
  await assert.rejects(
    generateBuildEvidence(generationOptions(fixture, "success")),
    /complete, valid machine evidence/u,
  );
});

test("accepts a Windows provider-worker intermediary and rejects broken ancestry", async (t) => {
  const fixture = await createFixture(t);
  const machineResult = JSON.parse(
    await readFile(fixture.resultPath, "utf8"),
  );
  const phase3bResult = JSON.parse(
    await readFile(fixture.phase3bResultPath, "utf8"),
  );
  const machineLaunch = machineResult.launches[0];
  const phase3bLaunch = phase3bResult.process.launches[0];
  const runtimeExit = phase3bLaunch.providerRuntimeExit;
  const worker = machineLaunch.ownership.processes.find(
    (process) => process.pid === runtimeExit.workerPid,
  );
  const service = machineLaunch.ownership.processes.find(
    (process) => process.pid === runtimeExit.parentPid,
  );
  assert.notEqual(worker, undefined);
  assert.notEqual(service, undefined);
  const intermediary = {
    pid: 4103,
    parentPid: service.pid,
    kind: "provider_worker",
    executableName: "cinematic-story-service.exe",
    creationDate: "2026-07-29T18:15:21.2500000Z",
  };
  worker.parentPid = intermediary.pid;
  machineLaunch.ownership.processes.push(intermediary);
  machineLaunch.exitProof.ownedPids.push(intermediary.pid);
  phase3bLaunch.ownedProcesses = machineLaunch.ownership.processes.map(
    (process) => ({
      pid: process.pid,
      parentPid: process.parentPid,
      kind: process.kind === "app" ? "electron" : process.kind,
      executableName: process.executableName,
      creationIdentity: process.creationDate,
      goneAfterShutdown: true,
    }),
  );
  await Promise.all([
    writeFile(fixture.resultPath, `${JSON.stringify(machineResult)}\n`, "utf8"),
    writeFile(
      fixture.phase3bResultPath,
      `${JSON.stringify(phase3bResult)}\n`,
      "utf8",
    ),
  ]);

  await assert.doesNotReject(
    generateBuildEvidence(generationOptions(fixture, "success")),
  );

  intermediary.creationDate = "2026-07-29T18:15:21.5000001Z";
  phase3bLaunch.ownedProcesses.find(
    (process) => process.pid === intermediary.pid,
  ).creationIdentity = intermediary.creationDate;
  await Promise.all([
    writeFile(fixture.resultPath, `${JSON.stringify(machineResult)}\n`, "utf8"),
    writeFile(
      fixture.phase3bResultPath,
      `${JSON.stringify(phase3bResult)}\n`,
      "utf8",
    ),
  ]);
  await assert.rejects(
    generateBuildEvidence(generationOptions(fixture, "success")),
    /complete, valid machine evidence/u,
  );

  intermediary.creationDate = "2026-07-29T18:15:21.2500000Z";
  phase3bLaunch.ownedProcesses.find(
    (process) => process.pid === intermediary.pid,
  ).creationIdentity = intermediary.creationDate;

  intermediary.kind = "service";
  phase3bLaunch.ownedProcesses.find(
    (process) => process.pid === intermediary.pid,
  ).kind = "service";
  await Promise.all([
    writeFile(fixture.resultPath, `${JSON.stringify(machineResult)}\n`, "utf8"),
    writeFile(
      fixture.phase3bResultPath,
      `${JSON.stringify(phase3bResult)}\n`,
      "utf8",
    ),
  ]);
  await assert.rejects(
    generateBuildEvidence(generationOptions(fixture, "success")),
    /complete, valid machine evidence/u,
  );

  intermediary.kind = "provider_worker";
  const phase3bIntermediary = phase3bLaunch.ownedProcesses.find(
    (process) => process.pid === intermediary.pid,
  );
  phase3bIntermediary.kind = "provider_worker";
  const secondIntermediary = {
    ...intermediary,
    pid: 4104,
    parentPid: service.pid,
    creationDate: "2026-07-29T18:15:21.1250000Z",
  };
  intermediary.parentPid = secondIntermediary.pid;
  machineLaunch.ownership.processes.push(secondIntermediary);
  machineLaunch.exitProof.ownedPids.push(secondIntermediary.pid);
  phase3bLaunch.ownedProcesses = machineLaunch.ownership.processes.map(
    (process) => ({
      pid: process.pid,
      parentPid: process.parentPid,
      kind: process.kind === "app" ? "electron" : process.kind,
      executableName: process.executableName,
      creationIdentity: process.creationDate,
      goneAfterShutdown: true,
    }),
  );
  await Promise.all([
    writeFile(fixture.resultPath, `${JSON.stringify(machineResult)}\n`, "utf8"),
    writeFile(
      fixture.phase3bResultPath,
      `${JSON.stringify(phase3bResult)}\n`,
      "utf8",
    ),
  ]);
  await assert.rejects(
    generateBuildEvidence(generationOptions(fixture, "success")),
    /complete, valid machine evidence/u,
  );

  machineLaunch.ownership.processes = machineLaunch.ownership.processes.filter(
    (process) => process.pid !== secondIntermediary.pid,
  );
  machineLaunch.exitProof.ownedPids = machineLaunch.exitProof.ownedPids.filter(
    (pid) => pid !== secondIntermediary.pid,
  );
  intermediary.parentPid = service.pid;
  machineLaunch.ownership.processes.push({
    ...intermediary,
    pid: 4105,
    parentPid: service.pid,
    creationDate: "2026-07-29T18:15:21.3750000Z",
  });
  machineLaunch.exitProof.ownedPids.push(4105);
  phase3bLaunch.ownedProcesses = machineLaunch.ownership.processes.map(
    (process) => ({
      pid: process.pid,
      parentPid: process.parentPid,
      kind: process.kind === "app" ? "electron" : process.kind,
      executableName: process.executableName,
      creationIdentity: process.creationDate,
      goneAfterShutdown: true,
    }),
  );
  await Promise.all([
    writeFile(fixture.resultPath, `${JSON.stringify(machineResult)}\n`, "utf8"),
    writeFile(
      fixture.phase3bResultPath,
      `${JSON.stringify(phase3bResult)}\n`,
      "utf8",
    ),
  ]);
  await assert.rejects(
    generateBuildEvidence(generationOptions(fixture, "success")),
    /complete, valid machine evidence/u,
  );
});

test("records and rejects a staged service that differs from the embedded service", async (t) => {
  const fixture = await createFixture(t);
  await writeFile(fixture.embeddedServicePath, "different-service", "utf8");

  await assert.rejects(
    generateBuildEvidence(generationOptions(fixture, "success")),
    /staged service does not match/u,
  );

  const manifest = JSON.parse(
    await readFile(fixture.manifestPath, "utf8"),
  );
  assert.equal(
    manifest.assertions.stagedServiceMatchesEmbeddedService,
    false,
  );
});

test("rejects an unhashed secure-ingest parser pin", async (t) => {
  const fixture = await createFixture(t);
  await writeFile(
    path.join(
      fixture.root,
      "apps",
      "local-service",
      "requirements.lock",
    ),
    [
      "lxml==6.1.1 \\",
      "    --hash=sha256:not-a-digest",
      "pypdf==6.14.2 \\",
      `    --hash=sha256:${"2".repeat(64)}`,
      "",
    ].join("\n"),
    "utf8",
  );

  await assert.rejects(
    generateBuildEvidence(generationOptions(fixture, "success")),
    /lxml hash lock is invalid/u,
  );
});

test("rejects a successful step without exact harness ownership evidence", async (t) => {
  const fixture = await createFixture(t);
  const validResult = JSON.parse(
    await readFile(fixture.resultPath, "utf8"),
  );
  await rm(fixture.resultPath);

  await assert.rejects(
    generateBuildEvidence(generationOptions(fixture, "success")),
    /without complete, valid machine evidence/u,
  );

  const manifest = JSON.parse(
    await readFile(fixture.manifestPath, "utf8"),
  );
  assert.equal(manifest.packagedE2e.result, "passed");
  assert.equal(
    manifest.assertions.packagedE2eHarnessResultMatchesStepOutcome,
    false,
  );

  const malformedResults = [
    {
      name: "non-UTC process creation date",
      mutate(value) {
        value.launches[0].ownership.processes[0].creationDate =
          "2026-07-29T18:15:20.0000000+00:00";
      },
    },
    {
      name: "root not parented by the recorded launcher",
      mutate(value) {
        value.launches[0].ownership.launcherPid = 9999;
      },
    },
    {
      name: "service outside the exact root ancestry",
      mutate(value) {
        value.launches[0].ownership.processes[1].parentPid = 9999;
      },
    },
    {
      name: "owned identity present in the pre-launch baseline",
      mutate(value) {
        const root = value.launches[0].ownership.processes[0];
        value.launches[0].preexistingRelevantProcesses.push({
          pid: root.pid,
          name: root.executableName,
          creationDate: root.creationDate,
        });
      },
    },
    {
      name: "duplicate owned PID proof",
      mutate(value) {
        value.launches[0].exitProof.ownedPids.push(
          value.launches[0].exitProof.ownedPids[0],
        );
      },
    },
    {
      name: "stable process identity cloned across launches",
      mutate(value) {
        value.launches[1].ownership =
          structuredClone(value.launches[0].ownership);
        value.launches[1].exitProof =
          structuredClone(value.launches[0].exitProof);
      },
    },
    {
      name: "second launch root not created after first launch",
      mutate(value) {
        value.launches[1].ownership.processes[0].creationDate =
          value.launches[0].ownership.processes[1].creationDate;
      },
    },
    {
      name: "forced owned PID",
      mutate(value) {
        value.launches[0].exitProof.forcedPids.push(
          value.launches[0].ownership.rootPid,
        );
      },
    },
    {
      name: "remaining owned PID",
      mutate(value) {
        value.launches[0].exitProof.remainingPids.push(
          value.launches[0].ownership.rootPid,
        );
      },
    },
    {
      name: "invalid import review source digest",
      mutate(value) {
        value.importReview.sourceSha256 = "not-a-sha256";
      },
    },
    {
      name: "unexpected correction reason fingerprint",
      mutate(value) {
        value.storyAnalysis.corrections.characterIdentity.reasonFingerprint =
          "f".repeat(64);
      },
    },
    {
      name: "gate not linked to the canonical profile",
      mutate(value) {
        value.storyAnalysis.gates[0].beforeRestart.profileFingerprint =
          "f".repeat(64);
        value.storyAnalysis.gates[0].afterRestart.profileFingerprint =
          "f".repeat(64);
      },
    },
    {
      name: "gate not linked to the analysis run",
      mutate(value) {
        value.storyAnalysis.gates[0].beforeRestart.runFingerprint =
          "f".repeat(64);
        value.storyAnalysis.gates[0].afterRestart.runFingerprint =
          "f".repeat(64);
      },
    },
    {
      name: "gate not linked to the analysis snapshot identity",
      mutate(value) {
        value.storyAnalysis.gates[0].beforeRestart.snapshotId =
          "other-snapshot";
        value.storyAnalysis.gates[0].afterRestart.snapshotId =
          "other-snapshot";
      },
    },
    {
      name: "gate not linked to the analysis snapshot revision",
      mutate(value) {
        value.storyAnalysis.gates[0].beforeRestart.snapshotRevision = 5;
        value.storyAnalysis.gates[0].afterRestart.snapshotRevision = 5;
      },
    },
    {
      name: "gate not linked to the analysis snapshot fingerprint",
      mutate(value) {
        value.storyAnalysis.gates[0].beforeRestart.snapshotFingerprint =
          "f".repeat(64);
        value.storyAnalysis.gates[0].afterRestart.snapshotFingerprint =
          "f".repeat(64);
      },
    },
    {
      name: "gate decision record changed across restart",
      mutate(value) {
        value.storyAnalysis.gates[0].afterRestart
          .decisionRecordFingerprint = "f".repeat(64);
      },
    },
  ];
  for (const malformed of malformedResults) {
    const value = structuredClone(validResult);
    malformed.mutate(value);
    await writeFile(
      fixture.resultPath,
      `${JSON.stringify(value)}\n`,
      "utf8",
    );
    await assert.rejects(
      generateBuildEvidence(generationOptions(fixture, "success")),
      /without complete, valid machine evidence/u,
      malformed.name,
    );
    const rejectedManifest = JSON.parse(
      await readFile(fixture.manifestPath, "utf8"),
    );
    assert.equal(
      rejectedManifest.packagedE2e.machineResult.contractValid,
      false,
      malformed.name,
    );
    assert.equal(
      rejectedManifest.assertions.packagedE2eOwnershipExitProven,
      false,
      malformed.name,
    );
  }
});

test("accepts structured prelaunch failure while keeping the packaged gate incomplete", async (t) => {
  const fixture = await createFixture(t);
  await rm(fixture.screenshotPath);
  await writeFile(
    fixture.resultPath,
    `${JSON.stringify({
      schemaVersion: "4.0.0",
      completedAt: COMPLETED_AT,
      status: "failed",
      failureStage: "prelaunch_inventory_1",
      failureCode: "PROCESS_INVENTORY_TIMEOUT",
      packagedVersion: APP_VERSION,
      executable:
        "release/0.1.0/win-unpacked/Cinematic Story Studio.exe",
      fixture: PACKAGED_FIXTURE,
      isolationEnvironment: ["APPDATA", "LOCALAPPDATA", "TEMP", "TMP"],
      completedLaunches: [],
      applicationLaunchBegan: false,
      ownershipEstablished: false,
      cleanupCompleted: true,
      preexistingRelevantProcesses: null,
      flow: [...PACKAGED_FLOW],
      screenshot: {
        artifactId: "packaged-ui-screenshot",
        captured: false,
      },
      importReview: null,
      storyAnalysis: null,
      launches: [],
    })}\n`,
    "utf8",
  );

  const { manifest } = await generateBuildEvidence(
    generationOptions(fixture, "failure"),
  );

  assert.equal(manifest.packagedE2e.result, "failed");
  assert.equal(manifest.packagedE2e.machineResult.exists, true);
  assert.equal(
    manifest.packagedE2e.machineResult.contractValid,
    true,
  );
  assert.equal(
    manifest.packagedE2e.machineResult.reportedStatus,
    "failed",
  );
  assert.equal(
    manifest.packagedE2e.machineResult.failureStage,
    "prelaunch_inventory_1",
  );
  assert.equal(
    manifest.packagedE2e.machineResult.failureCode,
    "PROCESS_INVENTORY_TIMEOUT",
  );
  assert.equal(
    manifest.packagedE2e.machineResult.applicationLaunchBegan,
    false,
  );
  assert.equal(
    manifest.packagedE2e.machineResult.ownershipEstablished,
    false,
  );
  assert.equal(
    manifest.packagedE2e.machineResult.cleanupCompleted,
    true,
  );
  assert.deepEqual(
    manifest.packagedE2e.machineResult.completedLaunches,
    [],
  );
  assert.equal(
    manifest.assertions.packagedE2eHarnessResultMatchesStepOutcome,
    true,
  );
  assert.equal(
    manifest.assertions.packagedE2eOwnershipExitProven,
    false,
  );
  assert.equal(
    manifest.assertions.packagedE2eEvidenceComplete,
    false,
  );
  assert.equal(manifest.testTimestamp, COMPLETED_AT);
});

test("validates exact failed progress and rejects contradictory v4 evidence", async (t) => {
  const fixture = await createFixture(t);
  const firstLaunch = launchEvidence(1, 4100, 4101);
  const secondLaunch = launchEvidence(2, 4200, 4201);
  const validFailures = [
    {
      name: "first application launch failed before ownership",
      value: failedMachineResult({
        failureStage: "launch_1",
        failureCode: "APPLICATION_LAUNCH_FAILED",
        applicationLaunchBegan: true,
        cleanupCompleted: false,
      }),
      ownershipExitProven: false,
    },
    {
      name: "first application readiness failed before service ownership",
      value: failedMachineResult({
        failureStage: "readiness_1",
        failureCode: "APPLICATION_READINESS_FAILED",
        applicationLaunchBegan: true,
      }),
      ownershipExitProven: false,
    },
    {
      name: "inventory helper exit was unconfirmed before launch",
      value: failedMachineResult({
        failureCode: "PROCESS_INVENTORY_HELPER_EXIT_UNCONFIRMED",
        cleanupCompleted: false,
      }),
      ownershipExitProven: false,
    },
    {
      name: "second prelaunch inventory failed after launch one completed",
      value: failedMachineResult({
        failureStage: "prelaunch_inventory_2",
        failureCode: "PROCESS_INVENTORY_TIMEOUT",
        applicationLaunchBegan: true,
        ownershipEstablished: true,
        completedLaunches: [1],
        launches: [firstLaunch],
      }),
      ownershipExitProven: false,
    },
    {
      name: "cleanup failed after both ownership exits were proven",
      value: failedMachineResult({
        failureStage: "cleanup",
        failureCode: "CLEANUP_FAILED",
        applicationLaunchBegan: true,
        ownershipEstablished: true,
        cleanupCompleted: false,
        completedLaunches: [1, 2],
        screenshotCaptured: true,
        launches: [firstLaunch, secondLaunch],
      }),
      ownershipExitProven: true,
    },
    {
      name: "evidence generation failed after cleanup and both ownership exits",
      value: failedMachineResult({
        failureStage: "evidence_generation",
        failureCode: "EVIDENCE_GENERATION_FAILED",
        applicationLaunchBegan: true,
        ownershipEstablished: true,
        completedLaunches: [1, 2],
        screenshotCaptured: true,
        launches: [firstLaunch, secondLaunch],
      }),
      ownershipExitProven: true,
    },
  ];

  for (const candidate of validFailures) {
    await writeFile(
      fixture.resultPath,
      `${JSON.stringify(candidate.value)}\n`,
      "utf8",
    );
    const { manifest } = await generateBuildEvidence(
      generationOptions(fixture, "failure"),
    );
    assert.equal(
      manifest.packagedE2e.machineResult.contractValid,
      true,
      candidate.name,
    );
    assert.equal(
      manifest.assertions.packagedE2eHarnessResultMatchesStepOutcome,
      true,
      candidate.name,
    );
    assert.equal(
      manifest.assertions.packagedE2eOwnershipExitProven,
      candidate.ownershipExitProven,
      candidate.name,
    );
    assert.equal(
      manifest.assertions.packagedE2eEvidenceComplete,
      false,
      candidate.name,
    );
  }

  const malformedBaseline = failedMachineResult();
  malformedBaseline.preexistingRelevantProcesses = [
    {
      pid: 3000,
      name: "Cinematic Story Studio.exe",
      creationDate: "2026-07-29T18:15:19.0000000Z",
      executablePath: "C:\\private\\unrelated.exe",
    },
  ];
  const invalidFailures = [
    {
      name: "malformed non-null baseline presented as unavailable",
      value: malformedBaseline,
    },
    {
      name: "failure code does not match its stage",
      value: failedMachineResult({
        failureCode: "SCREENSHOT_CAPTURE_FAILED",
      }),
    },
    {
      name: "readiness stage reports an inventory-only code",
      value: failedMachineResult({
        failureStage: "readiness_1",
        failureCode: "PROCESS_INVENTORY_TIMEOUT",
        applicationLaunchBegan: true,
      }),
    },
    {
      name: "launch one claims ownership before ownership was established",
      value: failedMachineResult({
        failureStage: "launch_1",
        failureCode: "APPLICATION_LAUNCH_FAILED",
        applicationLaunchBegan: true,
        ownershipEstablished: true,
      }),
    },
    {
      name: "second inventory has no completed first launch",
      value: failedMachineResult({
        failureStage: "prelaunch_inventory_2",
        failureCode: "PROCESS_INVENTORY_TIMEOUT",
        applicationLaunchBegan: true,
        ownershipEstablished: true,
      }),
    },
    {
      name: "second shutdown claims progress without launch evidence",
      value: failedMachineResult({
        failureStage: "shutdown_2",
        failureCode: "SHUTDOWN_VERIFICATION_FAILED",
        applicationLaunchBegan: true,
        ownershipEstablished: true,
        screenshotCaptured: true,
      }),
    },
    {
      name: "cleanup failure claims cleanup completed",
      value: failedMachineResult({
        failureStage: "cleanup",
        failureCode: "CLEANUP_FAILED",
        applicationLaunchBegan: true,
        ownershipEstablished: true,
        completedLaunches: [1, 2],
        screenshotCaptured: true,
        launches: [firstLaunch, secondLaunch],
      }),
    },
    {
      name: "evidence generation claims incomplete cleanup",
      value: failedMachineResult({
        failureStage: "evidence_generation",
        failureCode: "EVIDENCE_GENERATION_FAILED",
        applicationLaunchBegan: true,
        ownershipEstablished: true,
        cleanupCompleted: false,
        completedLaunches: [1, 2],
        screenshotCaptured: true,
        launches: [firstLaunch, secondLaunch],
      }),
    },
  ];

  for (const candidate of invalidFailures) {
    await writeFile(
      fixture.resultPath,
      `${JSON.stringify(candidate.value)}\n`,
      "utf8",
    );
    const { manifest } = await generateBuildEvidence(
      generationOptions(fixture, "failure"),
    );
    assert.equal(
      manifest.packagedE2e.machineResult.contractValid,
      false,
      candidate.name,
    );
    assert.equal(
      manifest.assertions.packagedE2eHarnessResultMatchesStepOutcome,
      false,
      candidate.name,
    );
    assert.equal(
      manifest.assertions.packagedE2eOwnershipExitProven,
      false,
      candidate.name,
    );
  }
});

test("preserves failed-gate evidence when screenshot and result files are absent", async (t) => {
  const fixture = await createFixture(t);
  await Promise.all([
    rm(fixture.screenshotPath),
    rm(fixture.resultPath),
  ]);

  const { manifest } = await generateBuildEvidence(
    generationOptions(fixture, "failure"),
  );

  assert.equal(manifest.packagedE2e.result, "failed");
  assert.equal(manifest.packagedE2e.screenshot.exists, false);
  assert.equal(manifest.packagedE2e.machineResult.exists, false);
  assert.equal(
    manifest.assertions.packagedE2eHarnessResultMatchesStepOutcome,
    false,
  );
  assert.equal(
    manifest.assertions.packagedE2eEvidenceComplete,
    false,
  );
});

async function createFixture(t) {
  const root = await mkdtemp(path.join(tmpdir(), "css-build-evidence-"));
  t.after(async () => {
    await rm(root, { recursive: true, force: true });
  });

  const releaseRoot = path.join(
    root,
    "apps",
    "desktop",
    "release",
    APP_VERSION,
  );
  const executablePath = path.join(
    releaseRoot,
    "win-unpacked",
    "Cinematic Story Studio.exe",
  );
  const stagedServicePath = path.join(
    root,
    "apps",
    "desktop",
    "build-resources",
    "service",
    "cinematic-story-service.exe",
  );
  const embeddedServicePath = path.join(
    releaseRoot,
    "win-unpacked",
    "resources",
    "service",
    "cinematic-story-service.exe",
  );
  const screenshotPath = path.join(releaseRoot, "packaged-e2e.png");
  const resultPath = path.join(releaseRoot, "packaged-e2e-result.json");
  const phase3ResultPath = path.join(
    releaseRoot,
    "phase-3-packaged-e2e-result.json",
  );
  const phase3VoiceCastingEvidencePath = path.join(
    releaseRoot,
    "phase-3-voice-casting-evidence.json",
  );
  const phase3bResultPath = path.join(
    releaseRoot,
    "phase-3b-packaged-e2e-result.json",
  );
  const phase3b1AbsentPackageEvidencePath = path.join(
    releaseRoot,
    "phase-3b1-absent-package-evidence.json",
  );
  const manifestPath = path.join(releaseRoot, "build-evidence.json");
  const requirementsDirectory = path.join(
    root,
    "apps",
    "local-service",
  );
  const syntheticFixtureDirectory = path.join(
    root,
    "fixtures",
    "synthetic-story",
  );
  const syntheticVoiceCatalogDirectory = path.join(
    root,
    "apps",
    "local-service",
    "src",
    "cinematic_story_service",
    "catalogs",
  );
  const appBytes = Buffer.from("desktop-application", "utf8");
  const serviceBytes = Buffer.from("packaged-service", "utf8");
  const screenshotBytes = Buffer.from("png-evidence", "utf8");
  const phase3Result = phase3PackagedResult(screenshotBytes);
  const launches = [
    launchEvidence(1, 4100, 4101),
    launchEvidence(2, 4200, 4201),
  ];
  const phase3bResult = phase3bPackagedResult(launches);
  const phase3b1AbsentPackageEvidenceBytes = Buffer.from(
    `${JSON.stringify(phase3b1AbsentPackageProof(), null, 2)}\n`,
    "utf8",
  );

  await Promise.all([
    mkdir(path.dirname(executablePath), { recursive: true }),
    mkdir(path.dirname(stagedServicePath), { recursive: true }),
    mkdir(path.dirname(embeddedServicePath), { recursive: true }),
    mkdir(path.dirname(screenshotPath), { recursive: true }),
    mkdir(requirementsDirectory, { recursive: true }),
    mkdir(syntheticFixtureDirectory, { recursive: true }),
    mkdir(syntheticVoiceCatalogDirectory, { recursive: true }),
  ]);
  await Promise.all([
    writeFile(
      path.join(root, "apps", "desktop", "package.json"),
      `${JSON.stringify({ version: APP_VERSION })}\n`,
      "utf8",
    ),
    writeFile(executablePath, appBytes),
    writeFile(stagedServicePath, serviceBytes),
    writeFile(embeddedServicePath, serviceBytes),
    writeFile(screenshotPath, screenshotBytes),
    writeFile(
      path.join(requirementsDirectory, "requirements.in"),
      "lxml==6.1.1\npypdf==6.14.2\n",
      "utf8",
    ),
    writeFile(
      path.join(requirementsDirectory, "requirements.lock"),
      [
        "lxml==6.1.1 \\",
        `    --hash=sha256:${"1".repeat(64)}`,
        "pypdf==6.14.2 \\",
        `    --hash=sha256:${"2".repeat(64)}`,
        "",
      ].join("\n"),
      "utf8",
    ),
    writeFile(
      path.join(syntheticFixtureDirectory, "generate-fixtures.mjs"),
      "export const deterministicFixtureGenerator = true;\n",
      "utf8",
    ),
    writeFile(
      path.join(
        syntheticFixtureDirectory,
        "sample-story.docx.base64",
      ),
      SYNTHETIC_DOCX_BYTES.toString("base64"),
      "ascii",
    ),
    writeFile(
      path.join(syntheticFixtureDirectory, "sample-story.txt"),
      SYNTHETIC_FIXTURES.txt,
    ),
    writeFile(
      path.join(syntheticFixtureDirectory, "sample-story.md"),
      SYNTHETIC_MARKDOWN_BYTES,
    ),
    writeFile(
      path.join(
        syntheticFixtureDirectory,
        "sample-story.expected.json",
      ),
      `${JSON.stringify(SYNTHETIC_EXPECTATIONS, null, 2)}\n`,
      "utf8",
    ),
    writeFile(
      resultPath,
      `${JSON.stringify({
        schemaVersion: "4.0.0",
        completedAt: COMPLETED_AT,
        status: "passed",
        failureStage: null,
        failureCode: null,
        packagedVersion: APP_VERSION,
        executable:
          "release/0.1.0/win-unpacked/Cinematic Story Studio.exe",
        fixture: PACKAGED_FIXTURE,
        isolationEnvironment: ["APPDATA", "LOCALAPPDATA", "TEMP", "TMP"],
        completedLaunches: [1, 2],
        applicationLaunchBegan: true,
        ownershipEstablished: true,
        cleanupCompleted: true,
        preexistingRelevantProcesses: [],
        flow: [...PACKAGED_FLOW],
        screenshot: {
          artifactId: "packaged-ui-screenshot",
          captured: true,
        },
        importReview: IMPORT_REVIEW,
        storyAnalysis: storyAnalysisEvidence(),
        launches,
      })}\n`,
      "utf8",
    ),
    writeFile(
      phase3ResultPath,
      `${JSON.stringify(phase3Result)}\n`,
      "utf8",
    ),
    writeFile(
      phase3VoiceCastingEvidencePath,
      `${JSON.stringify(
        phase3VoiceCastingEvidence(phase3Result),
      )}\n`,
      "utf8",
    ),
    writeFile(
      phase3bResultPath,
      `${JSON.stringify(phase3bResult)}\n`,
      "utf8",
    ),
    writeFile(
      phase3b1AbsentPackageEvidencePath,
      phase3b1AbsentPackageEvidenceBytes,
    ),
    writeFile(
      path.join(
        syntheticVoiceCatalogDirectory,
        "synthetic_voice_catalog.v1.json",
      ),
      SYNTHETIC_VOICE_CATALOG_BYTES,
    ),
  ]);

  return {
    root,
    appBytes,
    serviceBytes,
    executablePath,
    stagedServicePath,
    embeddedServicePath,
    screenshotPath,
    resultPath,
    phase3ResultPath,
    phase3VoiceCastingEvidencePath,
    phase3bResultPath,
    phase3b1AbsentPackageEvidencePath,
    phase3b1AbsentPackageEvidenceBytes,
    manifestPath,
  };
}

function generationOptions(fixture, stepOutcome) {
  return {
    repositoryRoot: fixture.root,
    workflowHeadSha: WORKFLOW_HEAD_SHA,
    testedCheckoutSha: WORKFLOW_HEAD_SHA,
    pullRequestHeadSha: PR_HEAD_SHA,
    timestamp: FALLBACK_TIMESTAMP,
    runner: RUNNER,
    packagedE2e: {
      executablePath: fixture.executablePath,
      screenshotPath: fixture.screenshotPath,
      resultPath: fixture.resultPath,
      phase3ResultPath: fixture.phase3ResultPath,
      phase3VoiceCastingEvidencePath:
        fixture.phase3VoiceCastingEvidencePath,
      phase3bResultPath: fixture.phase3bResultPath,
      phase3b1AbsentPackageEvidencePath:
        fixture.phase3b1AbsentPackageEvidencePath,
      phase3b1AbsentPackageStepOutcome: "success",
      stepOutcome,
    },
    manifestPath: fixture.manifestPath,
  };
}

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function fingerprint(value) {
  return sha256(Buffer.from(value, "utf8"));
}

function syntheticCatalogAssignmentEvidence(voiceProfileId) {
  const voice = SYNTHETIC_VOICE_CATALOG.voices.find(
    (item) => item.voiceProfileId === voiceProfileId,
  );
  const rights = SYNTHETIC_VOICE_CATALOG.rights.find(
    (item) => item.voiceProfileId === voiceProfileId,
  );
  assert.notEqual(voice, undefined);
  assert.notEqual(rights, undefined);
  const governedVoice = {
    ...structuredClone(voice),
    catalogRevisionId: GOVERNED_CATALOG_REVISION_ID,
  };
  return {
    voiceProfileId,
    voiceProfileVersion: voice.version,
    voiceEvidenceFingerprint: sha256(
      Buffer.from(
        JSON.stringify(canonicalizeTestJson(governedVoice)),
        "utf8",
      ),
    ),
    rightsRecordId: rights.rightsRecordId,
    rightsRecordRevision: rights.revision,
    rightsEvidenceFingerprint: sha256(
      Buffer.from(
        JSON.stringify(canonicalizeTestJson(rights)),
        "utf8",
      ),
    ),
    catalogRevisionId: GOVERNED_CATALOG_REVISION_ID,
    rightsState: rights.state,
  };
}

function canonicalizeTestJson(value) {
  if (Array.isArray(value)) {
    return value.map(canonicalizeTestJson);
  }
  if (
    value !== null &&
    typeof value === "object"
  ) {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, canonicalizeTestJson(value[key])]),
    );
  }
  return value;
}

function collectExactStoryText(value, excerpts = new Set()) {
  if (Array.isArray(value)) {
    for (const item of value) {
      collectExactStoryText(item, excerpts);
    }
    return excerpts;
  }
  if (value === null || typeof value !== "object") {
    return excerpts;
  }
  if (
    Object.hasOwn(value, "exactText") &&
    typeof value.exactText === "string"
  ) {
    excerpts.add(value.exactText);
  }
  for (const item of Object.values(value)) {
    collectExactStoryText(item, excerpts);
  }
  return excerpts;
}

function objectContainsString(value, expected) {
  if (typeof value === "string") {
    return value.includes(expected);
  }
  if (Array.isArray(value)) {
    return value.some((item) => objectContainsString(item, expected));
  }
  if (value !== null && typeof value === "object") {
    return Object.values(value).some((item) =>
      objectContainsString(item, expected),
    );
  }
  return false;
}

function launchEvidence(launch, appPid, servicePid) {
  const launcherPid = appPid + 1000;
  const providerWorkerPid = servicePid + 1;
  const rootCreationDate =
    launch === 1
      ? "2026-07-29T18:15:20.0000000Z"
      : "2026-07-29T18:15:22.0000000Z";
  const serviceCreationDate =
    launch === 1
      ? "2026-07-29T18:15:21.0000000Z"
      : "2026-07-29T18:15:23.0000000Z";
  const providerWorkerCreationDate =
    launch === 1
      ? "2026-07-29T18:15:21.5000000Z"
      : "2026-07-29T18:15:23.5000000Z";
  return {
    launch,
    preexistingRelevantProcesses: [],
    ownership: {
      launcherPid,
      rootPid: appPid,
      processes: [
        {
          pid: appPid,
          parentPid: launcherPid,
          kind: "app",
          executableName: "Cinematic Story Studio.exe",
          creationDate: rootCreationDate,
        },
        {
          pid: servicePid,
          parentPid: appPid,
          kind: "service",
          executableName: "cinematic-story-service.exe",
          creationDate: serviceCreationDate,
        },
        {
          pid: providerWorkerPid,
          parentPid: servicePid,
          kind: "provider_worker",
          executableName: "cinematic-story-service.exe",
          creationDate: providerWorkerCreationDate,
        },
      ],
    },
    exitProof: {
      ownedPids: [appPid, servicePid, providerWorkerPid],
      graceful: true,
      forcedPids: [],
      remainingPids: [],
    },
  };
}

function failedMachineResult({
  failureStage = "prelaunch_inventory_1",
  failureCode = "PROCESS_INVENTORY_TIMEOUT",
  applicationLaunchBegan = false,
  ownershipEstablished = false,
  cleanupCompleted = true,
  completedLaunches = [],
  screenshotCaptured = false,
  launches = [],
} = {}) {
  return {
    schemaVersion: "4.0.0",
    completedAt: COMPLETED_AT,
    status: "failed",
    failureStage,
    failureCode,
    packagedVersion: APP_VERSION,
    executable:
      "release/0.1.0/win-unpacked/Cinematic Story Studio.exe",
    fixture: PACKAGED_FIXTURE,
    isolationEnvironment: ["APPDATA", "LOCALAPPDATA", "TEMP", "TMP"],
    completedLaunches,
    applicationLaunchBegan,
    ownershipEstablished,
    cleanupCompleted,
    preexistingRelevantProcesses:
      failureStage === "prelaunch_inventory_1" ? null : [],
    flow: [...PACKAGED_FLOW],
    screenshot: {
      artifactId: "packaged-ui-screenshot",
      captured: screenshotCaptured,
    },
    importReview: null,
    storyAnalysis: null,
    launches,
  };
}
