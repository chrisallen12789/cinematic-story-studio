import {
  mkdtemp,
  readFile,
  rm
} from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import {
  createPhase3PackagedFlowRecorder,
  governedVoiceCastingProfileFingerprint,
  governedVoiceCastingProfileId,
  phase3PackagedE2eSchemaVersion,
  phase3PackagedFixture,
  phase3PackagedFlow,
  voiceCastingContractVersion,
  writePhase3PackagedE2eResult,
  writePhase3VoiceCastingEvidence,
  type Phase3Assertions,
  type Phase3PackagedE2eResult,
  type Phase3VoiceCastingEvidence
} from "./phase3-packaged-e2e-evidence";

const temporaryRoots: string[] = [];

afterEach(async () => {
  await Promise.all(
    temporaryRoots.splice(0).map((root) =>
      rm(root, { recursive: true, force: true })
    )
  );
});

describe("Phase 3A packaged E2E evidence", () => {
  it("pins the complete governed casting workflow", () => {
    expect(phase3PackagedE2eSchemaVersion).toBe("5.0.0");
    expect(voiceCastingContractVersion).toBe("3.0.0");
    expect(phase3PackagedFlow).toHaveLength(32);
    expect(phase3PackagedFlow.at(-1)).toBe(
      "verify_final_owned_process_exit"
    );

    const recorder = createPhase3PackagedFlowRecorder();
    for (const operation of phase3PackagedFlow) {
      recorder.record(operation);
    }
    expect(() => recorder.complete()).not.toThrow();
  });

  it("fails closed when workflow evidence is incomplete", () => {
    const recorder = createPhase3PackagedFlowRecorder();
    recorder.record("create_project");
    expect(() => recorder.record("wait_for_extraction")).toThrow(
      "operation order is invalid"
    );
    expect(() => recorder.complete()).toThrow(
      "operation sequence is incomplete"
    );
  });

  it("writes bounded result and contract evidence atomically", async () => {
    const root = await mkdtemp(
      path.join(tmpdir(), "css-phase3-evidence-test-")
    );
    temporaryRoots.push(root);
    const result = phase3Result();
    const evidence = phase3VoiceCastingEvidence(result);
    const resultPath = path.join(root, "phase-3-result.json");
    const evidencePath = path.join(root, "phase-3-evidence.json");

    await writePhase3PackagedE2eResult(resultPath, result);
    await writePhase3VoiceCastingEvidence(evidencePath, evidence);

    expect(JSON.parse(await readFile(resultPath, "utf8"))).toEqual(
      result
    );
    expect(JSON.parse(await readFile(evidencePath, "utf8"))).toEqual(
      evidence
    );
  });
});

function phase3Assertions(): Phase3Assertions {
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
    processOwnershipExitProven: true
  };
}

function phase3Result(): Phase3PackagedE2eResult {
  return {
    schemaVersion: phase3PackagedE2eSchemaVersion,
    contractVersion: voiceCastingContractVersion,
    result: "passed",
    fixture: phase3PackagedFixture,
    flow: phase3PackagedFlow,
    casting: {
      profileId: governedVoiceCastingProfileId,
      profileFingerprint: governedVoiceCastingProfileFingerprint,
      catalogRevisionId: "synthetic-voice-catalog-v1@1.0.0",
      catalogFingerprint: "a".repeat(64),
      castingRunId: "casting-run-1",
      approvedCastSnapshotId: "cast-snapshot-1",
      approvedCastSnapshotRevision: 1,
      narratorAssignmentId: "assignment-narrator",
      characterAssignmentIds: [
        "assignment-character-1",
        "assignment-character-2"
      ],
      narratorAssignment: {
        assignmentId: "assignment-narrator",
        roleId: "role-primary-narrator",
        roleType: "primary_narrator",
        voiceProfileId: "synthetic-narrator-02",
        voiceProfileVersion: "1.0.0",
        voiceEvidenceFingerprint: "1".repeat(64),
        rightsRecordId: "rights-synthetic-narrator-02",
        rightsRecordRevision: 1,
        rightsEvidenceFingerprint: "2".repeat(64),
        catalogRevisionId: "synthetic-voice-catalog-v1@1.0.0",
        castingProfileFingerprint:
          governedVoiceCastingProfileFingerprint,
        phase2SnapshotFingerprint: "3".repeat(64),
        effectiveCorrectionSetFingerprint: "4".repeat(64),
        authority: "human_locked",
        rightsState: "restricted",
        revision: 3,
        supersedesAssignmentId: "assignment-narrator-selection"
      },
      characterAssignments: [
        {
          assignmentId: "assignment-character-1",
          roleId: "role-character-1",
          roleType: "named_character",
          voiceProfileId: "synthetic-character-01",
          voiceProfileVersion: "1.0.0",
          voiceEvidenceFingerprint: "5".repeat(64),
          rightsRecordId: "rights-synthetic-character-01",
          rightsRecordRevision: 1,
          rightsEvidenceFingerprint: "6".repeat(64),
          catalogRevisionId: "synthetic-voice-catalog-v1@1.0.0",
          castingProfileFingerprint:
            governedVoiceCastingProfileFingerprint,
          phase2SnapshotFingerprint: "3".repeat(64),
          effectiveCorrectionSetFingerprint: "7".repeat(64),
          authority: "human_locked",
          rightsState: "verified",
          revision: 3,
          supersedesAssignmentId: "assignment-character-1-selection"
        },
        {
          assignmentId: "assignment-character-2",
          roleId: "role-character-2",
          roleType: "named_character",
          voiceProfileId: "synthetic-character-02",
          voiceProfileVersion: "1.0.0",
          voiceEvidenceFingerprint: "8".repeat(64),
          rightsRecordId: "rights-synthetic-character-02",
          rightsRecordRevision: 1,
          rightsEvidenceFingerprint: "9".repeat(64),
          catalogRevisionId: "synthetic-voice-catalog-v1@1.0.0",
          castingProfileFingerprint:
            governedVoiceCastingProfileFingerprint,
          phase2SnapshotFingerprint: "3".repeat(64),
          effectiveCorrectionSetFingerprint: "a".repeat(64),
          authority: "human_locked",
          rightsState: "verified",
          revision: 3,
          supersedesAssignmentId: "assignment-character-2-selection"
        }
      ],
      conflictId: "casting-conflict-1",
      conflictDispositionCorrectionId: "casting-correction-7",
      restrictedRightsWarningId: "rights-warning-1",
      ineligibleApprovalRejected: true,
      gateDecisionIds: {
        narratorCastingReview: "casting-decision-1",
        characterCastingReview: "casting-decision-2",
        completeCastReview: "casting-decision-3"
      },
      restartRestored: true
    },
    screenshot: {
      relativePath:
        "apps/desktop/release/0.1.0/packaged-e2e.png",
      byteSize: 100,
      sha256: "b".repeat(64),
      captureStatus: "success"
    },
    processOwnership: {
      ownershipEstablished: true,
      electronOwnedPids: [101, 201],
      serviceOwnedPids: [102, 202],
      launchShutdowns: [
        {
          launch: 1,
          electron: {
            launcherPid: 100,
            rootPid: 101,
            exitCode: 0,
            forceKillUsed: false
          },
          service: {
            pid: 102,
            method: "stdin_eof",
            exitCode: 0,
            signalCode: null,
            forceKillUsed: false
          }
        },
        {
          launch: 2,
          electron: {
            launcherPid: 200,
            rootPid: 201,
            exitCode: 0,
            forceKillUsed: false
          },
          service: {
            pid: 202,
            method: "stdin_eof",
            exitCode: 0,
            signalCode: null,
            forceKillUsed: false
          }
        }
      ],
      forcedPids: [],
      remainingOwnedPids: [],
      unrelatedProcessesTerminated: false
    },
    assertions: phase3Assertions(),
    completedAt: "2026-07-30T12:00:00.000Z"
  };
}

function phase3VoiceCastingEvidence(
  result: Phase3PackagedE2eResult
): Phase3VoiceCastingEvidence {
  return {
    castingProfile: {
      profileId: governedVoiceCastingProfileId,
      fingerprint: governedVoiceCastingProfileFingerprint,
      producerId: "voice-casting-orchestrator@1.0.0"
    },
    providers: [
      { descriptorId: "synthetic-local-fixture", version: "1.0.0" }
    ],
    models: [
      { descriptorId: "synthetic-character-model", version: "1.0.0" }
    ],
    catalogRevision: {
      catalogRevisionId: "synthetic-voice-catalog-v1@1.0.0",
      revision: 1,
      fingerprint: "a".repeat(64)
    },
    rightsPolicyId: "voice-rights-policy-v1",
    phase2Evidence: {
      analysisRunId: "analysis-run-1",
      snapshotId: "analysis-snapshot-1",
      snapshotRevision: 1,
      snapshotFingerprint: "c".repeat(64),
      correctionSetFingerprint: "d".repeat(64),
      gateDecisionIds: [
        "phase2-decision-1",
        "phase2-decision-2",
        "phase2-decision-3",
        "phase2-decision-4"
      ]
    },
    counts: {
      productionRoles: 3,
      narratorRoles: 1,
      characterRoles: 2,
      preReductionCandidates: 42,
      finalCandidates: 30,
      conflicts: 1,
      assignments: 3,
      corrections: 7
    },
    castingRunId: result.casting.castingRunId,
    approvedCastSnapshot: {
      snapshotId: result.casting.approvedCastSnapshotId,
      revision: result.casting.approvedCastSnapshotRevision,
      fingerprint: "e".repeat(64)
    },
    assignments: {
      narratorAssignmentId: result.casting.narratorAssignmentId,
      characterAssignmentIds:
        result.casting.characterAssignmentIds,
      narratorAssignment: result.casting.narratorAssignment,
      characterAssignments: result.casting.characterAssignments
    },
    rightsEligibility: "eligible_after_required_acknowledgements",
    correctionPersistence: true,
    conflictDispositionPersistence: true,
    gateDecisions: Object.values(result.casting.gateDecisionIds),
    restartPersistence: true,
    packagedE2e: result,
    assertions: result.assertions
  };
}
