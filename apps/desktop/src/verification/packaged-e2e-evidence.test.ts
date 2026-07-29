import {
  mkdtemp,
  readFile,
  rm
} from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import {
  isolatedEnvironmentNames,
  packagedE2eSchemaVersion,
  packagedFailureCode,
  packagedFixture,
  packagedFlow,
  runPackagedE2eEvidenceStep,
  writePackagedE2eMachineResult,
  type PackagedE2eMachineResult,
  type PackagedFailureCode
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
    launches: []
  };
}
