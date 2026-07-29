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
  assertRepositoryChild(root, outputPath, "build-evidence manifest");

  const [
    desktopApplicationEvidence,
    stagedServiceEvidence,
    embeddedServiceEvidence,
    screenshotEvidence,
    resultEvidence,
  ] = await Promise.all([
    requiredFileEvidence(canonicalRoot, root, expectedExecutable, "desktop application"),
    requiredFileEvidence(canonicalRoot, root, stagedService, "staged service"),
    requiredFileEvidence(canonicalRoot, root, embeddedService, "embedded service"),
    optionalFileEvidence(canonicalRoot, root, expectedScreenshot, "packaged E2E screenshot"),
    optionalFileEvidence(canonicalRoot, root, expectedResult, "packaged E2E result"),
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
  const harnessResultMatchesStepOutcome =
    harnessResult.contractValid &&
    harnessResult.reportedStatus === expectedHarnessStatus;
  const packagedE2eOwnershipExitProven =
    harnessResult.ownershipExitProven;
  const packagedE2eEvidenceComplete =
    harnessResultMatchesStepOutcome &&
    (normalizedStepOutcome === "failure" ||
      (screenshotEvidence.exists &&
        harnessResult.screenshotCaptured === true &&
        packagedE2eOwnershipExitProven));
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
    schemaVersion: "1.0.0",
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
      packagedE2eEvidenceComplete,
    },
    packagedE2e: {
      result: harnessResult.reportedStatus ?? expectedHarnessStatus,
      stepOutcome: normalizedStepOutcome,
      screenshot: screenshotEvidence,
      machineResult: {
        ...resultEvidence,
        contractValid: harnessResult.contractValid,
        reportedStatus: harnessResult.reportedStatus,
      },
      launches: harnessResult.launches,
    },
    testTimestamp:
      harnessResult.completedAt ?? normalizeTimestamp(timestamp),
    runner: normalizeRunner(runner),
  };

  await mkdir(path.dirname(outputPath), { recursive: true });
  await writeFile(outputPath, `${JSON.stringify(manifest, null, 2)}\n`, "utf8");

  if (!stagedServiceMatchesEmbeddedService) {
    throw new Error(
      "The staged service does not match the service embedded in the desktop artifact.",
    );
  }
  if (
    normalizedStepOutcome === "success" &&
    (!harnessResultMatchesStepOutcome || !packagedE2eEvidenceComplete)
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
    return {
      contractValid: false,
      reportedStatus: null,
      screenshotCaptured: null,
      completedAt: null,
      launches: [],
      ownershipExitProven: false,
    };
  }
  if (
    typeof resultEvidence.sizeBytes !== "number" ||
    resultEvidence.sizeBytes > MAX_HARNESS_RESULT_BYTES
  ) {
    return {
      contractValid: false,
      reportedStatus: null,
      screenshotCaptured: null,
      completedAt: null,
      launches: [],
      ownershipExitProven: false,
    };
  }

  try {
    const value = JSON.parse(await readFile(resultPath, "utf8"));
    const expectedExecutable = `release/${appVersion}/win-unpacked/${APP_EXECUTABLE_NAME}`;
    const completedAt =
      typeof value?.completedAt === "string"
        ? normalizeUtcTimestamp(value.completedAt)
        : null;
    const launches = sanitizeLaunchProof(value?.launches);
    const ownershipExitProven =
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
    const contractValid =
      isPlainObject(value) &&
      value.schemaVersion === "1.0.0" &&
      (value.status === "passed" || value.status === "failed") &&
      completedAt !== null &&
      value.packagedVersion === appVersion &&
      value.executable === expectedExecutable &&
      value.fixture === "fixtures/synthetic-story/sample-story.md" &&
      equalStringArrays(value.isolationEnvironment, [
        "APPDATA",
        "LOCALAPPDATA",
        "TEMP",
        "TMP",
      ]) &&
      equalStringArrays(value.flow, [
        "create",
        "import_synthetic_fixture",
        "analyze",
        "correct_speaker",
        "close",
        "restart",
        "restore",
        "close",
      ]) &&
      isPlainObject(value.screenshot) &&
      value.screenshot.artifactId === "packaged-ui-screenshot" &&
      typeof value.screenshot.captured === "boolean" &&
      Array.isArray(launches) &&
      (value.status !== "passed" || ownershipExitProven);
    return {
      contractValid,
      reportedStatus:
        value?.status === "passed" || value?.status === "failed"
          ? value.status
          : null,
      screenshotCaptured:
        isPlainObject(value?.screenshot) &&
        typeof value.screenshot.captured === "boolean"
          ? value.screenshot.captured
          : null,
      completedAt,
      launches: launches ?? [],
      ownershipExitProven,
    };
  } catch {
    return {
      contractValid: false,
      reportedStatus: null,
      screenshotCaptured: null,
      completedAt: null,
      launches: [],
      ownershipExitProven: false,
    };
  }
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
      (item.launch !== 1 && item.launch !== 2) ||
      seenLaunches.has(item.launch) ||
      !Array.isArray(item.preexistingRelevantProcesses) ||
      !isPlainObject(item.ownership) ||
      !isPositivePid(item.ownership.launcherPid) ||
      !isPositivePid(item.ownership.rootPid) ||
      !Array.isArray(item.ownership.processes) ||
      item.ownership.processes.length === 0 ||
      item.ownership.processes.length > 256 ||
      !isPlainObject(item.exitProof) ||
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
        !isPositivePid(process.pid) ||
        seenProcessPids.has(process.pid) ||
        !Number.isSafeInteger(process.parentPid) ||
        process.parentPid < 0 ||
        (process.kind !== "app" && process.kind !== "service") ||
        typeof process.executableName !== "string" ||
        (process.kind === "app" &&
          process.executableName !== APP_EXECUTABLE_NAME) ||
        (process.kind === "service" &&
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
  return launches.sort((left, right) => left.launch - right.launch);
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

async function sha256File(target) {
  const digest = createHash("sha256");
  for await (const chunk of createReadStream(target)) {
    digest.update(chunk);
  }
  return digest.digest("hex");
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
  return {
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

function environmentValue(name) {
  const value = process.env[name];
  if (value === undefined) {
    throw new Error(`Missing required build-evidence environment: ${name}.`);
  }
  return value;
}

async function main() {
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
