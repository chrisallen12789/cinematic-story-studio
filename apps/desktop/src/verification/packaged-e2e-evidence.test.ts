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
  isolatedEnvironmentNames,
  packagedE2eSchemaVersion,
  packagedFailureCode,
  packagedFixture,
  packagedFlow,
  materializeStrictBase64Docx,
  runPackagedE2eEvidenceStep,
  writePackagedE2eMachineResult,
  type PackagedE2eMachineResult,
  type PackagedFailureCode,
  type PackagedImportReviewEvidence
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
  it("locks the schema 3 DOCX review flow and exact evidence keys", () => {
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

    expect(packagedE2eSchemaVersion).toBe("3.0.0");
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
      "close",
      "restart",
      "restore",
      "verify_import_review_persistence",
      "close"
    ]);
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
});

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
    launches: []
  };
}
