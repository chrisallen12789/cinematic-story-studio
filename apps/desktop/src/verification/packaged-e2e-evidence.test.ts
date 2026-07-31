import {
  mkdir,
  mkdtemp,
  readFile,
  rm,
  writeFile
} from "node:fs/promises";
import { createHash } from "node:crypto";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import {
  ANALYSIS_GATE_IDS,
  ANALYSIS_JOB_STAGES,
  PHASE_2_RUNTIME_AGENTS,
  WHOLE_BOOK_ANALYSIS_PROFILE_FINGERPRINT
} from "@cinematic-story-studio/contracts";

import {
  createPackagedFlowRecorder,
  isolatedEnvironmentNames,
  packagedCorrectionReasonFingerprints,
  packagedE2eSchemaVersion,
  packagedFailureCode,
  packagedFixture,
  packagedFlow,
  packagedPhase2GovernanceFlow,
  materializeStrictBase64Docx,
  runPackagedE2eEvidenceStep,
  writePackagedE2eMachineResult,
  type PackagedE2eMachineResult,
  type PackagedFailureCode,
  type PackagedImportReviewEvidence,
  type PackagedStoryAnalysisEvidence
} from "./packaged-e2e-evidence";
import { ProcessInventoryError } from "./packaged-process-inventory";

const temporaryRoots: string[] = [];

afterEach(async () => {
  await Promise.all(
    temporaryRoots.splice(0).map((root) =>
      rm(root, { recursive: true, force: true })
    )
  );
});

describe("packaged E2E machine evidence", () => {
  it("locks the schema 4 Phase 2 flow and exact evidence keys", () => {
    const importReview = {
      format: "docx",
      sourceSha256: "a".repeat(64),
      extractedTextSha256: "b".repeat(64),
      extractionRevision: 1,
      warningCount: 0,
      approvalDecision: "approved",
      approvalPersistedAfterRestart: true,
      extractionPersistedAfterRestart: true,
      analysisPersistedAfterRestart: true
    } satisfies PackagedImportReviewEvidence;
    const storyAnalysis = storyAnalysisEvidence();

    expect(packagedE2eSchemaVersion).toBe("4.0.0");
    expect(packagedFixture).toBe(
      "fixtures/synthetic-story/sample-story.docx.base64"
    );
    expect(packagedFlow).toEqual([
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
      "close"
    ]);
    expect(packagedFlow.slice(7, 20)).toEqual(
      packagedPhase2GovernanceFlow
    );
    expect(Object.keys(importReview)).toEqual([
      "format",
      "sourceSha256",
      "extractedTextSha256",
      "extractionRevision",
      "warningCount",
      "approvalDecision",
      "approvalPersistedAfterRestart",
      "extractionPersistedAfterRestart",
      "analysisPersistedAfterRestart"
    ]);
    expect(Object.keys(storyAnalysis)).toEqual([
      "profile",
      "agents",
      "approvedInput",
      "run",
      "observedStages",
      "counts",
      "assertions",
      "corrections",
      "gates",
      "restart"
    ]);
    expect(storyAnalysis.agents.map((agent) => agent.agentId)).toEqual(
      PHASE_2_RUNTIME_AGENTS.map((agent) => agent.agentId)
    );
    expect(storyAnalysis.observedStages).toEqual(ANALYSIS_JOB_STAGES);
    expect(Object.keys(storyAnalysis.corrections)).toEqual([
      "characterIdentity",
      "dialogueSpeaker",
      "continuityDisposition"
    ]);
    expect(
      storyAnalysis.corrections.characterIdentity.reasonFingerprint
    ).toBe(packagedCorrectionReasonFingerprints.characterIdentity);
    expect(storyAnalysis.gates.map((gate) => gate.gateId)).toEqual(
      ANALYSIS_GATE_IDS
    );
    expect(
      storyAnalysis.gates.every(
        (gate) =>
          gate.beforeRestart.profileFingerprint ===
            storyAnalysis.profile.profileFingerprint &&
          gate.beforeRestart.runFingerprint ===
            storyAnalysis.run.runFingerprint &&
          gate.beforeRestart.snapshotId === storyAnalysis.run.snapshotId &&
          gate.beforeRestart.snapshotRevision ===
            storyAnalysis.run.snapshotRevision &&
          gate.beforeRestart.snapshotFingerprint ===
            storyAnalysis.run.snapshotFingerprint
      )
    ).toBe(true);
  });

  it("fails closed when the executed Phase 2 operation order drifts", () => {
    const recorder = createPackagedFlowRecorder(
      packagedPhase2GovernanceFlow
    );
    recorder.record("start_whole_book_analysis");
    expect(() => recorder.record("inspect_structure")).toThrow(
      "The packaged workflow operation order is invalid."
    );
    expect(() => recorder.complete()).toThrow(
      "The packaged workflow operation sequence is incomplete."
    );
  });

  it("materializes canonical ASCII base64 only inside the isolated root", async () => {
    const root = await mkdtemp(
      path.join(tmpdir(), "css-packaged-fixture-test-")
    );
    temporaryRoots.push(root);
    const fixtureDirectory = path.join(root, "fixture");
    await mkdir(fixtureDirectory);
    const encodedPath = path.join(root, "sample-story.docx.base64");
    const decoded = Buffer.from([
      0x50,
      0x4b,
      0x03,
      0x04,
      0x43,
      0x53,
      0x53
    ]);
    await writeFile(
      encodedPath,
      `${decoded.toString("base64")}\n`,
      "ascii"
    );
    const destination = path.join(fixtureDirectory, "sample-story.docx");

    await expect(
      materializeStrictBase64Docx(encodedPath, root, destination)
    ).resolves.toBe(createHash("sha256").update(decoded).digest("hex"));
    await expect(readFile(destination)).resolves.toEqual(decoded);
    await expect(
      materializeStrictBase64Docx(
        encodedPath,
        root,
        path.join(path.dirname(root), "escaped.docx")
      )
    ).rejects.toThrow("outside its isolated root");
  });

  it("writes a bounded redacted result for prelaunch inventory failure", async () => {
    const root = await mkdtemp(
      path.join(tmpdir(), "css-packaged-evidence-test-")
    );
    temporaryRoots.push(root);
    const resultPath = path.join(root, "packaged-e2e-result.json");
    const privateValue = "unapproved-private-diagnostic";
    const error = new ProcessInventoryError(
      "PROCESS_INVENTORY_TIMEOUT",
      true
    );
    Object.defineProperty(error, "privateValue", {
      value: privateValue
    });
    const result = failedPrelaunchResult(
      packagedFailureCode("prelaunch_inventory_1", error)
    );

    await expect(
      runPackagedE2eEvidenceStep(
        "prelaunch_inventory_1",
        async () => {
          throw error;
        },
        async (stage, code) => {
          expect(stage).toBe("prelaunch_inventory_1");
          await writePackagedE2eMachineResult(
            resultPath,
            failedPrelaunchResult(code)
          );
        }
      )
    ).rejects.toBe(error);

    const bytes = await readFile(resultPath, "utf8");
    const parsed = JSON.parse(bytes) as PackagedE2eMachineResult;
    expect(parsed).toEqual(result);
    expect(parsed.status).toBe("failed");
    expect(parsed.failureStage).toBe("prelaunch_inventory_1");
    expect(parsed.failureCode).toBe("PROCESS_INVENTORY_TIMEOUT");
    expect(parsed.applicationLaunchBegan).toBe(false);
    expect(parsed.ownershipEstablished).toBe(false);
    expect(parsed.cleanupCompleted).toBe(true);
    expect(parsed.completedLaunches).toEqual([]);
    expect(bytes).not.toContain(privateValue);
    expect(Buffer.byteLength(bytes, "utf8")).toBeLessThan(
      1024 * 1024
    );
  });

  it("reports evidence-generation failures after verified shutdown", () => {
    expect(
      packagedFailureCode("evidence_generation", undefined)
    ).toBe("EVIDENCE_GENERATION_FAILED");
  });
});

function storyAnalysisEvidence(): PackagedStoryAnalysisEvidence {
  const fingerprint = (character: string) => character.repeat(64);
  const correction = <
    TCategory extends
      | "character_identity"
      | "dialogue_speaker"
      | "continuity_disposition"
  >(
    name: string,
    category: TCategory
  ) => {
    const effectiveFingerprint = fingerprint("e");
    const reasonFingerprint =
      category === "character_identity"
        ? packagedCorrectionReasonFingerprints.characterIdentity
        : category === "dialogue_speaker"
          ? packagedCorrectionReasonFingerprints.dialogueSpeaker
          : packagedCorrectionReasonFingerprints.continuityDisposition;
    return {
      correctionId: `correction-${name}`,
      category,
      targetEntityId: `entity-${name}`,
      reasonFingerprint,
      previousValueFingerprint: fingerprint("d"),
      correctedValueFingerprint: effectiveFingerprint,
      effectiveValueFingerprintBeforeRestart: effectiveFingerprint,
      effectiveValueFingerprintAfterRestart: effectiveFingerprint,
      effectiveAuthorityBeforeRestart: "human",
      effectiveAuthorityAfterRestart: "human",
      immutable: true,
      lockedAgainstAutomation: true,
      persistedAfterRestart: true
    } as const;
  };
  return {
    profile: {
      profileId: "whole-book-intelligence-v1",
      semanticVersion: "1.0.0",
      profileFingerprint: WHOLE_BOOK_ANALYSIS_PROFILE_FINGERPRINT,
      producerId: "whole-book-analysis-orchestrator",
      producerVersion: "1.0.0"
    },
    agents: PHASE_2_RUNTIME_AGENTS.map((agent, index) => ({
      agentId: agent.agentId,
      agentVersion: agent.version,
      executionId: `execution-${index + 1}`,
      status: "succeeded",
      outputFingerprint: fingerprint("c")
    })),
    approvedInput: {
      sourceDocumentId: "source-1",
      sourceRevision: 1,
      sourceSha256: fingerprint("a"),
      extractionId: "extraction-1",
      extractionRevision: 1,
      extractedTextSha256: fingerprint("b"),
      importReviewId: "import-review-1",
      importReviewRevision: 2,
      importReviewDecisionId: "import-decision-1",
      approvedEvidenceFingerprint: fingerprint("c"),
      storyId: "story-1",
      storyRevision: 1,
      storyFingerprint: fingerprint("d")
    },
    run: {
      runId: "analysis-run-1",
      inputFingerprint: fingerprint("1"),
      runFingerprint: fingerprint("2"),
      jobId: "analysis-job-1",
      status: "succeeded",
      snapshotId: "analysis-snapshot-1",
      snapshotRevision: 1,
      snapshotFingerprint: fingerprint("3"),
      correctionSetFingerprint: fingerprint("4")
    },
    observedStages: ANALYSIS_JOB_STAGES,
    counts: {
      agentExecutions: 11,
      chapters: 1,
      scenes: 2,
      beats: 3,
      characters: 2,
      mentions: 2,
      dialogueLines: 1,
      narrationSpans: 1,
      povSegments: 1,
      locations: 1,
      timelineEvents: 1,
      temporalConstraints: 1,
      relationships: 1,
      emotionalStates: 1,
      dramaticIntents: 1,
      continuityFindings: 1,
      corrections: 3
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
      continuityAnomalyDetected: true
    },
    corrections: {
      characterIdentity: correction(
        "character-identity",
        "character_identity"
      ),
      dialogueSpeaker: correction(
        "dialogue-speaker",
        "dialogue_speaker"
      ),
      continuityDisposition: correction(
        "continuity-disposition",
        "continuity_disposition"
      )
    },
    gates: ANALYSIS_GATE_IDS.map((gateId, index) => {
      const state = {
        reviewId: `gate-review-${index + 1}`,
        decisionId: `gate-decision-${index + 1}`,
        state: "approved" as const,
        profileFingerprint: WHOLE_BOOK_ANALYSIS_PROFILE_FINGERPRINT,
        runFingerprint: fingerprint("2"),
        snapshotId: "analysis-snapshot-1",
        snapshotRevision: 1,
        snapshotFingerprint: fingerprint("3"),
        decisionRecordFingerprint: fingerprint(
          `gate-decision-record-${index + 1}`
        ),
        artifactFingerprint: fingerprint("6"),
        evidenceFingerprint: fingerprint("7")
      };
      return {
        gateId,
        beforeRestart: state,
        afterRestart: { ...state },
        immutable: true as const
      };
    }),
    restart: {
      runPersisted: true,
      snapshotPersisted: true,
      correctionSetPersisted: true,
      gateDecisionsPersisted: true,
      agentExecutionsPersisted: true
    }
  };
}

function failedPrelaunchResult(
  failureCode: PackagedFailureCode
): PackagedE2eMachineResult {
  return {
    schemaVersion: packagedE2eSchemaVersion,
    completedAt: "2026-07-29T20:09:24.620Z",
    status: "failed",
    failureStage: "prelaunch_inventory_1",
    failureCode,
    packagedVersion: "0.1.0",
    executable:
      "release/0.1.0/win-unpacked/Cinematic Story Studio.exe",
    fixture: packagedFixture,
    isolationEnvironment: isolatedEnvironmentNames,
    completedLaunches: [],
    applicationLaunchBegan: false,
    ownershipEstablished: false,
    cleanupCompleted: true,
    preexistingRelevantProcesses: null,
    flow: packagedFlow,
    screenshot: {
      artifactId: "packaged-ui-screenshot",
      captured: false
    },
    importReview: null,
    storyAnalysis: null,
    launches: []
  };
}
