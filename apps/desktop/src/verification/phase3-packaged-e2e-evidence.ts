import {
  rename,
  rm,
  writeFile
} from "node:fs/promises";

import {
  GOVERNED_VOICE_CASTING_PROFILE_FINGERPRINT,
  GOVERNED_VOICE_CASTING_PROFILE_ID,
  VOICE_CASTING_CONTRACT_VERSION,
  VOICE_CASTING_PRODUCER_ID,
  VOICE_RIGHTS_POLICY_ID,
  type ProductionRoleType
} from "@cinematic-story-studio/contracts";

export const phase3PackagedE2eSchemaVersion = "5.0.0" as const;
export const voiceCastingContractVersion =
  VOICE_CASTING_CONTRACT_VERSION;
export const phase3PackagedFixture =
  "fixtures/synthetic-story/sample-story.docx.base64" as const;
export const governedVoiceCastingProfileId =
  GOVERNED_VOICE_CASTING_PROFILE_ID;
export const governedVoiceCastingProfileFingerprint =
  GOVERNED_VOICE_CASTING_PROFILE_FINGERPRINT;
export const voiceCastingProducerId =
  VOICE_CASTING_PRODUCER_ID;
export const voiceRightsPolicyId = VOICE_RIGHTS_POLICY_ID;
export const phase3PackagedE2eResultEnvironment =
  "CSS_PHASE3_PACKAGED_E2E_RESULT_PATH" as const;
export const phase3VoiceCastingEvidenceEnvironment =
  "CSS_PHASE3_VOICE_CASTING_EVIDENCE_PATH" as const;

export const phase3PackagedFlow = Object.freeze([
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
  "verify_final_owned_process_exit"
] as const);

export type Phase3PackagedFlowOperation =
  (typeof phase3PackagedFlow)[number];

export interface Phase3PackagedFlowRecorder {
  readonly record: (operation: Phase3PackagedFlowOperation) => void;
  readonly complete: () => void;
}

export function createPhase3PackagedFlowRecorder(): Phase3PackagedFlowRecorder {
  let nextIndex = 0;
  return {
    record(operation) {
      if (phase3PackagedFlow[nextIndex] !== operation) {
        throw new Error(
          "The Phase 3A packaged workflow operation order is invalid."
        );
      }
      nextIndex += 1;
    },
    complete() {
      if (nextIndex !== phase3PackagedFlow.length) {
        throw new Error(
          "The Phase 3A packaged workflow operation sequence is incomplete."
        );
      }
    }
  };
}

export interface Phase3RoleVoiceAssignmentEvidence {
  readonly assignmentId: string;
  readonly roleId: string;
  readonly roleType: ProductionRoleType;
  readonly voiceProfileId: string;
  readonly voiceProfileVersion: string;
  readonly voiceEvidenceFingerprint: string;
  readonly rightsRecordId: string;
  readonly rightsRecordRevision: number;
  readonly rightsEvidenceFingerprint: string;
  readonly catalogRevisionId: string;
  readonly castingProfileFingerprint: string;
  readonly phase2SnapshotFingerprint: string;
  readonly effectiveCorrectionSetFingerprint: string;
  readonly authority: "human_locked";
  readonly rightsState: "verified" | "restricted";
  readonly revision: number;
  readonly supersedesAssignmentId: string;
}

export interface Phase3CastingProof {
  readonly profileId: typeof governedVoiceCastingProfileId;
  readonly profileFingerprint:
    typeof governedVoiceCastingProfileFingerprint;
  readonly catalogRevisionId: string;
  readonly catalogFingerprint: string;
  readonly castingRunId: string;
  readonly approvedCastSnapshotId: string;
  readonly approvedCastSnapshotRevision: number;
  readonly narratorAssignmentId: string;
  readonly characterAssignmentIds: readonly string[];
  readonly narratorAssignment: Phase3RoleVoiceAssignmentEvidence;
  readonly characterAssignments:
    readonly Phase3RoleVoiceAssignmentEvidence[];
  readonly conflictId: string;
  readonly conflictDispositionCorrectionId: string;
  readonly restrictedRightsWarningId: string;
  readonly ineligibleApprovalRejected: true;
  readonly gateDecisionIds: {
    readonly narratorCastingReview: string;
    readonly characterCastingReview: string;
    readonly completeCastReview: string;
  };
  readonly restartRestored: true;
}

export interface Phase3ProcessOwnershipProof {
  readonly ownershipEstablished: true;
  readonly electronOwnedPids: readonly number[];
  readonly serviceOwnedPids: readonly number[];
  readonly launchShutdowns: readonly Phase3LaunchShutdownProof[];
  readonly forcedPids: readonly [];
  readonly remainingOwnedPids: readonly [];
  readonly unrelatedProcessesTerminated: false;
}

export interface Phase3LaunchShutdownProof {
  readonly launch: 1 | 2;
  readonly electron: {
    readonly launcherPid: number;
    readonly rootPid: number;
    readonly exitCode: 0;
    readonly forceKillUsed: false;
  };
  readonly service: {
    readonly pid: number;
    readonly method: "stdin_eof";
    readonly exitCode: 0;
    readonly signalCode: null;
    readonly forceKillUsed: false;
  };
}

export interface Phase3Assertions {
  readonly phase2PrerequisitesCurrent: true;
  readonly castingProfilePinned: true;
  readonly catalogFingerprintVerified: true;
  readonly rolesCreated: true;
  readonly boundedCandidatesCreated: true;
  readonly metadataConflictProven: true;
  readonly rightsGovernanceProven: true;
  readonly humanAssignmentsLocked: true;
  readonly threeGateDecisionsPersisted: true;
  readonly restartPersistenceProven: true;
  readonly processOwnershipExitProven: true;
}

export interface Phase3PackagedE2eResult {
  readonly schemaVersion: typeof phase3PackagedE2eSchemaVersion;
  readonly contractVersion: typeof voiceCastingContractVersion;
  readonly result: "passed";
  readonly fixture: typeof phase3PackagedFixture;
  readonly flow: typeof phase3PackagedFlow;
  readonly casting: Phase3CastingProof;
  readonly screenshot: {
    readonly relativePath: string;
    readonly byteSize: number;
    readonly sha256: string;
    readonly captureStatus: "success";
  };
  readonly processOwnership: Phase3ProcessOwnershipProof;
  readonly assertions: Phase3Assertions;
  readonly completedAt: string;
}

export interface Phase3VoiceCastingEvidence {
  readonly castingProfile: {
    readonly profileId: typeof governedVoiceCastingProfileId;
    readonly fingerprint:
      typeof governedVoiceCastingProfileFingerprint;
    readonly producerId: typeof voiceCastingProducerId;
  };
  readonly providers: readonly {
    readonly descriptorId: string;
    readonly version: string;
  }[];
  readonly models: readonly {
    readonly descriptorId: string;
    readonly version: string;
  }[];
  readonly catalogRevision: {
    readonly catalogRevisionId: string;
    readonly revision: number;
    readonly fingerprint: string;
  };
  readonly rightsPolicyId: typeof voiceRightsPolicyId;
  readonly phase2Evidence: {
    readonly analysisRunId: string;
    readonly snapshotId: string;
    readonly snapshotRevision: number;
    readonly snapshotFingerprint: string;
    readonly correctionSetFingerprint: string;
    readonly gateDecisionIds: readonly string[];
  };
  readonly counts: {
    readonly productionRoles: number;
    readonly narratorRoles: number;
    readonly characterRoles: number;
    readonly preReductionCandidates: number;
    readonly finalCandidates: number;
    readonly conflicts: number;
    readonly assignments: number;
    readonly corrections: number;
  };
  readonly castingRunId: string;
  readonly approvedCastSnapshot: {
    readonly snapshotId: string;
    readonly revision: number;
    readonly fingerprint: string;
  };
  readonly assignments: {
    readonly narratorAssignmentId: string;
    readonly characterAssignmentIds: readonly string[];
    readonly narratorAssignment: Phase3RoleVoiceAssignmentEvidence;
    readonly characterAssignments:
      readonly Phase3RoleVoiceAssignmentEvidence[];
  };
  readonly rightsEligibility:
    "eligible_after_required_acknowledgements";
  readonly correctionPersistence: true;
  readonly conflictDispositionPersistence: true;
  readonly gateDecisions: readonly string[];
  readonly restartPersistence: true;
  readonly packagedE2e: Phase3PackagedE2eResult;
  readonly assertions: Phase3Assertions;
}

const maximumPhase3EvidenceBytes = 1024 * 1024;

export async function writePhase3PackagedE2eResult(
  resultPath: string,
  value: Phase3PackagedE2eResult
): Promise<void> {
  await writeBoundedPhase3Evidence(resultPath, value);
}

export async function writePhase3VoiceCastingEvidence(
  evidencePath: string,
  value: Phase3VoiceCastingEvidence
): Promise<void> {
  await writeBoundedPhase3Evidence(evidencePath, value);
}

async function writeBoundedPhase3Evidence(
  targetPath: string,
  value: Phase3PackagedE2eResult | Phase3VoiceCastingEvidence
): Promise<void> {
  const serialized = `${JSON.stringify(value, null, 2)}\n`;
  if (
    Buffer.byteLength(serialized, "utf8") >
    maximumPhase3EvidenceBytes
  ) {
    throw new Error("The Phase 3A packaged evidence exceeded its limit.");
  }
  const temporaryPath = `${targetPath}.${process.pid}.tmp`;
  try {
    await writeFile(temporaryPath, serialized, {
      encoding: "utf8",
      mode: 0o600
    });
    await rename(temporaryPath, targetPath);
  } finally {
    await rm(temporaryPath, { force: true });
  }
}
