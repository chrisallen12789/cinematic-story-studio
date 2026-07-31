// @vitest-environment node

import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import {
  createPackagedServiceShutdownEvidence,
  packagedShutdownEvidenceSchemaVersion,
  readPackagedServiceShutdownEvidence,
  resolvePackagedShutdownEvidencePath,
  writePackagedServiceShutdownEvidence
} from "./shutdown-evidence";

const temporaryRoots: string[] = [];

afterEach(async () => {
  await Promise.all(
    temporaryRoots.splice(0).map((root) =>
      rm(root, { recursive: true, force: true })
    )
  );
});

describe("packaged service shutdown evidence", () => {
  it("round-trips bounded graceful process evidence atomically", async () => {
    const root = await createTemporaryRoot();
    const target = path.join(
      root,
      "packaged-e2e-service-shutdown-1.json"
    );
    const evidence = createPackagedServiceShutdownEvidence({
      processes: [
        {
          pid: 42_424,
          method: "stdin_eof",
          exitCode: 0,
          signalCode: null
        }
      ],
      forceKillUsed: false,
      allProcessesExitedGracefully: true
    });

    await writePackagedServiceShutdownEvidence(target, evidence);

    expect(await readPackagedServiceShutdownEvidence(target)).toEqual({
      schemaVersion: packagedShutdownEvidenceSchemaVersion,
      status: "succeeded",
      processes: [
        {
          pid: 42_424,
          method: "stdin_eof",
          exitCode: 0,
          signalCode: null
        }
      ],
      forceKillUsed: false,
      allProcessesExitedGracefully: true
    });
  });

  it("rejects evidence targets outside isolated user data", async () => {
    const root = await createTemporaryRoot();
    expect(
      () =>
        resolvePackagedShutdownEvidencePath(
          path.join(
            root,
            "..",
            "packaged-e2e-service-shutdown-1.json"
          ),
          root
        )
    ).toThrow("escaped");
  });

  it("fails closed on a contradictory evidence file", async () => {
    const root = await createTemporaryRoot();
    const target = path.join(
      root,
      "packaged-e2e-service-shutdown-2.json"
    );
    await writeFile(
      target,
      `${JSON.stringify({
        schemaVersion: packagedShutdownEvidenceSchemaVersion,
        status: "succeeded",
        processes: [
          {
            pid: 42_425,
            method: "force_kill",
            exitCode: 0,
            signalCode: null
          }
        ],
        forceKillUsed: false,
        allProcessesExitedGracefully: true
      })}\n`,
      "utf8"
    );

    await expect(
      readPackagedServiceShutdownEvidence(target)
    ).rejects.toThrow("summary is invalid");
  });
});

async function createTemporaryRoot(): Promise<string> {
  const root = await mkdtemp(
    path.join(tmpdir(), "css-shutdown-evidence-")
  );
  temporaryRoots.push(root);
  return root;
}
