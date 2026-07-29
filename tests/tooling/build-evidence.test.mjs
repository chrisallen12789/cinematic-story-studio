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

import { generateBuildEvidence } from "../../scripts/ci/build-evidence.mjs";

const APP_VERSION = "0.1.0";
const PR_HEAD_SHA = "0123456789abcdef0123456789abcdef01234567";
const WORKFLOW_HEAD_SHA =
  "89abcdef0123456789abcdef0123456789abcdef";
const FALLBACK_TIMESTAMP = "2026-07-29T18:00:00.000Z";
const COMPLETED_AT = "2026-07-29T18:15:21.123Z";
const RUNNER = Object.freeze({
  name: "GitHub Actions 1000000000",
  os: "Windows",
  architecture: "X64",
  environment: "github-hosted",
  runId: "30478862847",
  runAttempt: "2",
  workflow: "Phase 0 Windows CI",
  job: "verify-and-build",
});

test("writes stable relative-path evidence for a successful packaged gate", async (t) => {
  const fixture = await createFixture(t);
  const options = generationOptions(fixture, "success");

  const first = await generateBuildEvidence(options);
  const firstBytes = await readFile(first.manifestPath, "utf8");
  const second = await generateBuildEvidence(options);
  const secondBytes = await readFile(second.manifestPath, "utf8");

  assert.equal(secondBytes, firstBytes);
  assert.equal(first.manifest.schemaVersion, "1.0.0");
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
    packagedE2eEvidenceComplete: true,
  });
  assert.equal(first.manifest.packagedE2e.result, "passed");
  assert.equal(
    first.manifest.packagedE2e.screenshot.path,
    "apps/desktop/release/0.1.0/packaged-e2e.png",
  );
  assert.equal(
    first.manifest.packagedE2e.machineResult.path,
    "apps/desktop/release/0.1.0/packaged-e2e-result.json",
  );
  assert.deepEqual(first.manifest.packagedE2e.launches, [
    {
      launch: 1,
      preexistingRelevantProcesses: [],
      ownership: {
        launcherPid: 5100,
        rootPid: 4100,
        processes: [
          {
            pid: 4100,
            parentPid: 5100,
            kind: "app",
            executableName: "Cinematic Story Studio.exe",
            creationDate: "2026-07-29T18:15:20.0000000Z",
          },
          {
            pid: 4101,
            parentPid: 4100,
            kind: "service",
            executableName: "cinematic-story-service.exe",
            creationDate: "2026-07-29T18:15:21.0000000Z",
          },
        ],
      },
      exitProof: {
        ownedPids: [4100, 4101],
        graceful: true,
        forcedPids: [],
        remainingPids: [],
      },
    },
    {
      launch: 2,
      preexistingRelevantProcesses: [],
      ownership: {
        launcherPid: 5200,
        rootPid: 4200,
        processes: [
          {
            pid: 4200,
            parentPid: 5200,
            kind: "app",
            executableName: "Cinematic Story Studio.exe",
            creationDate: "2026-07-29T18:15:22.0000000Z",
          },
          {
            pid: 4201,
            parentPid: 4200,
            kind: "service",
            executableName: "cinematic-story-service.exe",
            creationDate: "2026-07-29T18:15:23.0000000Z",
          },
        ],
      },
      exitProof: {
        ownedPids: [4200, 4201],
        graceful: true,
        forcedPids: [],
        remainingPids: [],
      },
    },
  ]);
  assert.equal(first.manifest.testTimestamp, COMPLETED_AT);
  assert.deepEqual(first.manifest.runner, RUNNER);
  assert.equal(firstBytes.endsWith("\n"), true);
  assert.equal(firstBytes.includes(fixture.root), false);
  const workflow = await readFile(
    new URL("../../.github/workflows/ci.yml", import.meta.url),
    "utf8",
  );
  const buildIndex = workflow.indexOf("id: build");
  const packagedGateIndex = workflow.indexOf("id: packaged_e2e");

  assert.notEqual(buildIndex, -1);
  assert.equal(packagedGateIndex > buildIndex, true);
  for (const environmentName of [
    "CSS_PACKAGED_E2E_EXECUTABLE",
    "CSS_PACKAGED_E2E_EVIDENCE_PATH",
    "CSS_PACKAGED_E2E_RESULT_PATH",
  ]) {
    assert.match(workflow, new RegExp(environmentName, "u"));
  }
  assert.match(
    workflow,
    /apps\/desktop\/release\/\$version/u,
  );
  assert.match(
    workflow,
    /win-unpacked\/Cinematic Story Studio\.exe/u,
  );
  assert.match(workflow, /node scripts\/ci\/build-evidence\.mjs/u);
  assert.match(
    workflow,
    /github\.event\.pull_request\.head\.sha \|\| github\.sha/u,
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
  const manifestPath = path.join(releaseRoot, "build-evidence.json");
  const appBytes = Buffer.from("desktop-application", "utf8");
  const serviceBytes = Buffer.from("packaged-service", "utf8");

  await Promise.all([
    mkdir(path.dirname(executablePath), { recursive: true }),
    mkdir(path.dirname(stagedServicePath), { recursive: true }),
    mkdir(path.dirname(embeddedServicePath), { recursive: true }),
    mkdir(path.dirname(screenshotPath), { recursive: true }),
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
    writeFile(screenshotPath, "png-evidence", "utf8"),
    writeFile(
      resultPath,
      `${JSON.stringify({
        schemaVersion: "1.0.0",
        completedAt: COMPLETED_AT,
        status: "passed",
        packagedVersion: APP_VERSION,
        executable:
          "release/0.1.0/win-unpacked/Cinematic Story Studio.exe",
        fixture: "fixtures/synthetic-story/sample-story.md",
        isolationEnvironment: ["APPDATA", "LOCALAPPDATA", "TEMP", "TMP"],
        flow: [
          "create",
          "import_synthetic_fixture",
          "analyze",
          "correct_speaker",
          "close",
          "restart",
          "restore",
          "close",
        ],
        screenshot: {
          artifactId: "packaged-ui-screenshot",
          captured: true,
        },
        launches: [
          launchEvidence(1, 4100, 4101),
          launchEvidence(2, 4200, 4201),
        ],
      })}\n`,
      "utf8",
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
      stepOutcome,
    },
    manifestPath: fixture.manifestPath,
  };
}

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function launchEvidence(launch, appPid, servicePid) {
  const launcherPid = appPid + 1000;
  const rootCreationDate =
    launch === 1
      ? "2026-07-29T18:15:20.0000000Z"
      : "2026-07-29T18:15:22.0000000Z";
  const serviceCreationDate =
    launch === 1
      ? "2026-07-29T18:15:21.0000000Z"
      : "2026-07-29T18:15:23.0000000Z";
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
      ],
    },
    exitProof: {
      ownedPids: [appPid, servicePid],
      graceful: true,
      forcedPids: [],
      remainingPids: [],
    },
  };
}
