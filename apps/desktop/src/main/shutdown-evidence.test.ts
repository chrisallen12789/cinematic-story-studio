// @vitest-environment node

import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import {
  createPackagedServiceShutdownEvidence,
  packagedShutdownEvidenceSchemaVersion,
  phase3bRuntimeShutdownEvidenceFileName,
  readPhase3bRuntimeShutdownEvidence,
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
  it.each([
    "packaged-e2e-service-shutdown-1.json",
    "packaged-e2e-service-shutdown-2.json",
    "phase3b1-real-service-shutdown-3.json",
    "phase3b1-real-service-shutdown-4.json"
  ])("allows the exact governed shutdown target %s", async (fileName) => {
    const root = await createTemporaryRoot();
    const target = path.join(root, fileName);

    expect(resolvePackagedShutdownEvidencePath(target, root)).toBe(target);
  });

  it.each([
    "packaged-e2e-service-shutdown-0.json",
    "packaged-e2e-service-shutdown-3.json",
    "phase3b1-real-service-shutdown-2.json",
    "phase3b1-real-service-shutdown-5.json",
    "phase3b1-real-service-shutdown-3.json.tmp",
    "prefix-phase3b1-real-service-shutdown-3.json"
  ])("rejects the near-match shutdown target %s", async (fileName) => {
    const root = await createTemporaryRoot();

    expect(() =>
      resolvePackagedShutdownEvidencePath(path.join(root, fileName), root)
    ).toThrow("escaped");
  });

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

  it("reads only bounded authenticated Phase 3B runtime shutdown proof", async () => {
    const root = await createTemporaryRoot();
    const target = path.join(root, phase3bRuntimeShutdownEvidenceFileName);
    const runtimeExit = {
      runtimeInstanceId: "runtime-instance-1",
      workerPid: 42_426,
      state: "stopped",
      stoppedAt: "2026-07-31T12:00:00.000Z",
      stopReasonCode: "clean",
      exitCode: 0,
      shutdownAcknowledged: true,
      gracefulShutdownConfirmed: true,
      terminatedByParent: false,
      ownershipConfirmed: true,
      ownedProcessesConfirmedExited: true,
      jobObjectAssigned: true,
      deniedNetworkAttemptCount: 0
    };
    await writeFile(
      target,
      `${JSON.stringify({
        contractVersion: "1.0.0",
        serviceInstanceId: "service-instance-1",
        ownedRuntimeCount: 1,
        runtimeExits: [runtimeExit],
        allGracefulShutdownsConfirmed: true,
        writtenAt: "2026-07-31T12:00:01.000Z"
      })}\n`,
      "utf8"
    );

    await expect(readPhase3bRuntimeShutdownEvidence(root)).resolves.toEqual({
      contractVersion: "1.0.0",
      serviceInstanceId: "service-instance-1",
      ownedRuntimeCount: 1,
      runtimeExits: [runtimeExit],
      allGracefulShutdownsConfirmed: true,
      writtenAt: "2026-07-31T12:00:01.000Z"
    });

    await writeFile(
      target,
      `${JSON.stringify({
        contractVersion: "1.0.0",
        serviceInstanceId: "service-instance-1",
        ownedRuntimeCount: 1,
        runtimeExits: [
          { ...runtimeExit, shutdownAcknowledged: false }
        ],
        allGracefulShutdownsConfirmed: true,
        writtenAt: "2026-07-31T12:00:01.000Z"
      })}\n`,
      "utf8"
    );
    await expect(
      readPhase3bRuntimeShutdownEvidence(root)
    ).rejects.toThrow("summary was contradictory");

    await writeFile(
      target,
      `${JSON.stringify({
        contractVersion: "1.0.0",
        serviceInstanceId: "service-instance-1",
        ownedRuntimeCount: 1,
        runtimeExits: [{ ...runtimeExit, jobObjectAssigned: false }],
        allGracefulShutdownsConfirmed: true,
        writtenAt: "2026-07-31T12:00:01.000Z"
      })}\n`,
      "utf8"
    );
    await expect(
      readPhase3bRuntimeShutdownEvidence(root)
    ).rejects.toThrow("summary was contradictory");
  });
});

async function createTemporaryRoot(): Promise<string> {
  const root = await mkdtemp(
    path.join(tmpdir(), "css-shutdown-evidence-")
  );
  temporaryRoots.push(root);
  return root;
}
