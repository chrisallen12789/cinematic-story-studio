import {
  spawn,
  spawnSync,
  type ChildProcess,
  type ChildProcessWithoutNullStreams
} from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { once } from "node:events";
import { createReadStream } from "node:fs";
import { lstat, readFile, readdir, realpath } from "node:fs/promises";
import { createServer } from "node:net";
import path from "node:path";
import { performance } from "node:perf_hooks";
import { fileURLToPath } from "node:url";

import {
  chromium,
  expect,
  test,
  type Browser,
  type Page
} from "@playwright/test";
import type {
  AuditionClip,
  AuditionReviewDecision,
  HumanListeningDisposition
} from "@cinematic-story-studio/contracts";

import {
  buildPhase3b1PrivateReplayLauncher,
  phase3b1PrivateReplayContractFileName,
  phase3b1PrivateReplaySentinelFileName,
  requirePhase3b1PrivateReplayPathBudget,
  resolvePhase3b1PrivateReplayStateDirectory,
  validatePhase3b1PrivateReplayContract,
  phase3b1PrivateReplaySanitizedEnvironmentNames,
  type Phase3b1PrivateReplayContract
} from "../../src/verification/phase3b1-private-replay";
import {
  adoptVerifiedProcessTreeWithPathConfirmation,
  appExecutableName,
  containsProcessIdentity,
  createPackagedProcessInventory,
  defaultProcessInventoryPolicy,
  ownedServiceRootProcesses,
  remainingOwnedProcesses,
  serviceExecutableName,
  type ConfirmedExitedTransientProcess,
  type OwnedProcess,
  type PackagedProcessPaths,
  type ProcessIdentity
} from "../../src/verification/packaged-process-inventory";

const desktopRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../.."
);
const repositoryRoot = path.resolve(desktopRoot, "../..");
const privateRoot = path.join(
  repositoryRoot,
  "local-renders",
  "phase3b1-real-product-path"
);
const packageEnvironment = "CSS_PHASE3B1_PRIVATE_REPLAY_PACKAGE";
const recordEnvironment = "CSS_PHASE3B1_RECORD_PRIVATE_DECISIONS";
const runnerEnvironment = "CSS_PHASE3B1_PRIVATE_REPLAY_RUNNER";
const replayE2eEnvironment = "CSS_PHASE3B1_PRIVATE_REPLAY_E2E";
const replayDebugPortEnvironment =
  "CSS_PHASE3B1_REPLAY_E2E_REMOTE_DEBUGGING_PORT";
const decisionFileName = "listening-decisions.json";
const replayLauncherFileName = "replay-private-listening.ps1";
const maximumJsonBytes = 512 * 1024;
const maximumChildOutputBytes = 64 * 1024;
const launchTimeoutMs = 90_000;
const backendTimeoutMs = 120_000;
const shutdownTimeoutMs = 45_000;
const inventory = createPackagedProcessInventory();
const now = () => performance.now();

interface ListeningIndexClip {
  readonly opaqueFileName: string;
  readonly auditionClipId: string;
  readonly auditionClipFingerprint: string;
  readonly audioArtifactId: string;
  readonly audioSha256: string;
  readonly byteSize: number;
}

interface ListeningIndex {
  readonly projectId: string;
  readonly clips: readonly ListeningIndexClip[];
}

interface ExpectedListeningDecision {
  readonly opaqueFileName: string;
  readonly auditionClipId: string;
  readonly audioSha256: string;
  readonly disposition: HumanListeningDisposition;
  readonly rationale: string;
}

interface VerifiedPrivateReplay {
  readonly packageDirectory: string;
  readonly launcherPath: string;
  readonly stateDirectory: string;
  readonly paths: PackagedProcessPaths;
  readonly contract: Phase3b1PrivateReplayContract;
  readonly contractSha256: string;
  readonly indexSha256: string;
  readonly decisionExpectationsSha256: string;
  readonly projectId: string;
  readonly clips: readonly ListeningIndexClip[];
  readonly decisions: readonly ExpectedListeningDecision[];
  readonly environment: Record<string, string>;
  readonly observedMaximumPathLength: number;
}

interface LaunchOwnership {
  readonly launcherPid: number;
  readonly rootPid: number;
  readonly baseline: readonly ProcessIdentity[];
  readonly packaged: PackagedProcessPaths;
  processes: readonly OwnedProcess[];
  confirmedExitedTransientProcesses: readonly ConfirmedExitedTransientProcess[];
}

interface ReplayLauncher {
  readonly child: ChildProcessWithoutNullStreams;
  readonly reportedPid: Promise<number>;
  readonly exited: Promise<{ readonly code: number | null; readonly signal: NodeJS.Signals | null }>;
  readonly output: () => { readonly stdout: string; readonly stderr: string; readonly overflowed: boolean };
}

interface RestoredSessionProof {
  readonly projectId: string;
  readonly allClipIds: readonly string[];
  readonly decisions: readonly AuditionReviewDecision[];
  readonly backendState: "ready";
  readonly voiceReadinessBlocked: true;
  readonly authenticatedClipIds: readonly string[];
}

interface ActualReplayProof {
  readonly process: Record<string, unknown>;
  readonly session: RestoredSessionProof;
}

interface OwnershipSampler {
  readonly stop: () => Promise<void>;
}

test.describe("Phase 3B.1 private packaged replay", () => {
  test.skip(process.platform !== "win32", "The private replay is a Windows-only product path.");
  test.skip(
    process.env[packageEnvironment] === undefined ||
      process.env[packageEnvironment]?.trim().length === 0 ||
      process.env[runnerEnvironment] !== "1",
    `Set ${packageEnvironment} only through the dedicated private replay runner.`
  );

  test("restores the exact private package from clean, duplicate, and stale baselines", async () => {
    test.setTimeout(900_000);
    const replay = await verifyPrivateReplayPackage(requiredPackageDirectory());
    const recordDecisions = process.env[recordEnvironment] === "1";
    const evidence: Record<string, unknown> = {
      schemaVersion: 1,
      result: "passed",
      contractSha256: replay.contractSha256,
      listeningIndexSha256: replay.indexSha256,
      decisionExpectationsSha256: replay.decisionExpectationsSha256,
      stateId: replay.contract.stateId,
      generationMaximumRetainedPathLength:
        replay.contract.maximumRetainedPathLength,
      observedMaximumRetainedPathLength: replay.observedMaximumPathLength,
      executable: { ...replay.contract.executable },
      applicationArchive: { ...replay.contract.applicationArchive },
      service: { ...replay.contract.service },
      recordModeRequested: recordDecisions,
      noAuditionGenerationRequested: true
    };

    const initialBaseline = await requireEmptyReplayBaseline();
    expect(initialBaseline).toEqual([]);
    evidence.preexistingRelevantProcesses = [];

    evidence.staleOwnedService = await proveStaleOwnedServiceRefusal(replay);
    await requireTwoEmptyRelevantInventories();

    const first = await exerciseActualReplayLauncher(
      replay,
      recordDecisions,
      true
    );
    await requireTwoEmptyRelevantInventories();
    evidence.staleLockMarker = await requirePersistentStaleLockMarker(replay);
    const second = await exerciseActualReplayLauncher(replay, false, false);
    await requireTwoEmptyRelevantInventories();

    expect(second.session.projectId).toBe(first.session.projectId);
    expect(second.session.allClipIds).toEqual(first.session.allClipIds);
    if (
      JSON.stringify(second.session.decisions) !==
      JSON.stringify(first.session.decisions)
    ) {
      throw new Error("The six exact human listening decision records changed across restart.");
    }
    expect(second.session.authenticatedClipIds).toEqual(
      first.session.authenticatedClipIds
    );
    if (first.session.decisions.length !== 6) {
      throw new Error("The replay did not restore all six exact human listening decisions.");
    }
    evidence.actualReplayLaunches = [first.process, second.process];
    evidence.restoration = {
      backendReady:
        first.session.backendState === "ready" &&
        second.session.backendState === "ready",
      sameSoleProjectId: first.session.projectId,
      indexedClipIds: first.session.authenticatedClipIds,
      noNewAuditionClipIdsAfterRestart: first.session.allClipIds,
      authenticatedPlayback: true,
      decisionRestartIdentity: true,
      voiceReadinessBlocked:
        first.session.voiceReadinessBlocked &&
        second.session.voiceReadinessBlocked,
      decisions: first.session.decisions.map(privateDecisionEvidence)
    };
    evidence.finalRelevantProcessInventories = [[], []];

    console.log(
      `CSS_PHASE3B1_PRIVATE_REPLAY_E2E_RESULT=${JSON.stringify(evidence)}`
    );
  });
});

async function verifyPrivateReplayPackage(
  packageDirectoryValue: string
): Promise<VerifiedPrivateReplay> {
  const packageDirectory = path.resolve(packageDirectoryValue);
  const [canonicalPackage, canonicalPrivateRoot, packageMetadata] = await Promise.all([
    realpath(packageDirectory),
    realpath(privateRoot),
    lstat(packageDirectory)
  ]);
  if (
    !packageMetadata.isDirectory() ||
    packageMetadata.isSymbolicLink() ||
    !samePath(canonicalPackage, packageDirectory) ||
    !isStrictChild(canonicalPrivateRoot, canonicalPackage)
  ) {
    throw new Error("The explicit private replay package was not a canonical ignored package directory.");
  }
  const ignored = spawnSync(
    "git",
    ["check-ignore", "--quiet", "--", canonicalPackage],
    { cwd: repositoryRoot, shell: false, windowsHide: true }
  );
  if (ignored.error !== undefined || ignored.status !== 0) {
    throw new Error("The explicit private replay package was not ignored by Git.");
  }

  const contractPath = path.join(
    canonicalPackage,
    phase3b1PrivateReplayContractFileName
  );
  const contractBytes = await readBoundedFile(contractPath, maximumJsonBytes);
  const contract = validatePhase3b1PrivateReplayContract(
    JSON.parse(contractBytes.toString("utf8")) as Phase3b1PrivateReplayContract
  );
  if (path.basename(canonicalPackage) !== contract.packageDirectoryName) {
    throw new Error("The private replay package directory identity changed.");
  }
  const contractSha256 = sha256(contractBytes);
  const indexPath = path.join(canonicalPackage, "listening-index.json");
  const indexBytes = await readBoundedFile(indexPath, maximumJsonBytes);
  const indexSha256 = sha256(indexBytes);
  if (indexSha256 !== contract.listeningIndexSha256) {
    throw new Error("The private listening index fingerprint changed.");
  }
  const index = parseListeningIndex(JSON.parse(indexBytes.toString("utf8")) as unknown);
  const clips = index.clips;
  await verifyPrivateWavs(canonicalPackage, clips);

  const decisionsBytes = await readBoundedFile(
    path.join(canonicalPackage, decisionFileName),
    maximumJsonBytes
  );
  const decisions = parseExpectedDecisions(
    JSON.parse(decisionsBytes.toString("utf8")) as unknown,
    clips
  );
  const decisionExpectationsSha256 = sha256(decisionsBytes);

  const hostLocalAppData = requiredAbsoluteEnvironment("LOCALAPPDATA");
  const stateDirectory = resolvePhase3b1PrivateReplayStateDirectory(
    hostLocalAppData,
    contract.stateId
  );
  const canonicalState = await realpath(stateDirectory);
  if (!samePath(canonicalState, stateDirectory)) {
    throw new Error("The retained private replay state was not canonical.");
  }
  const sentinelBytes = await readBoundedFile(
    path.join(canonicalState, phase3b1PrivateReplaySentinelFileName),
    maximumJsonBytes
  );
  if (sha256(sentinelBytes) !== contract.stateSentinelSha256) {
    throw new Error("The retained replay-state binding fingerprint changed.");
  }
  validateStateSentinel(
    JSON.parse(sentinelBytes.toString("utf8")) as unknown,
    contract,
    index
  );
  for (const name of ["AppData", "LocalAppData", "Temp"] as const) {
    await requireCanonicalDirectory(path.join(canonicalState, name));
  }
  const retainedPaths = await listCanonicalTree(canonicalState);
  const observedMaximumPathLength = requirePhase3b1PrivateReplayPathBudget(
    canonicalState,
    retainedPaths
  );
  const paths = await resolveBoundExecutables(contract);
  const launcherPath = path.join(canonicalPackage, replayLauncherFileName);
  const launcherBytes = await readBoundedFile(launcherPath, maximumJsonBytes);
  const expectedLauncher = buildPhase3b1PrivateReplayLauncher(
    contract,
    contractSha256
  );
  if (launcherBytes.toString("utf8") !== expectedLauncher) {
    throw new Error("The private replay launcher did not match its exact generated contract.");
  }

  return {
    packageDirectory: canonicalPackage,
    launcherPath,
    stateDirectory: canonicalState,
    paths,
    contract,
    contractSha256,
    indexSha256,
    decisionExpectationsSha256,
    projectId: index.projectId,
    clips,
    decisions,
    environment: retainedEnvironment(canonicalState),
    observedMaximumPathLength
  };
}

async function proveStaleOwnedServiceRefusal(
  replay: VerifiedPrivateReplay
): Promise<Record<string, unknown>> {
  const baseline = await requireEmptyReplayBaseline();
  const dataDirectory = path.join(
    replay.stateDirectory,
    "LocalAppData",
    "Cinematic Story Studio"
  );
  await requireCanonicalDirectory(dataDirectory);
  const child = spawn(
    replay.paths.serviceExecutablePath,
    ["--data-dir", dataDirectory],
    {
      cwd: path.dirname(replay.paths.serviceExecutablePath),
      env: replay.environment,
      shell: false,
      windowsHide: true,
      stdio: ["pipe", "pipe", "pipe"]
    }
  );
  const output = captureBoundedOutput(child);
  const startedAt = Date.now();
  let identity: ProcessIdentity | null = null;
  let gracefulExitRequested = false;
  try {
    const token = randomBytes(32).toString("hex");
    const nonce = randomBytes(24).toString("base64url");
    child.stdin.write(
      `${JSON.stringify({ token, nonce, protocolVersion: "1.0.0" })}\n`,
      "utf8"
    );
    await waitForFixedOutput(child, output, /^CSS_READY /mu, launchTimeoutMs);
    identity = await waitForExactProcess({
      pid: requiredPid(child.pid, "owned stale service"),
      parentPid: process.pid,
      name: serviceExecutableName,
      executablePath: replay.paths.serviceExecutablePath,
      baseline,
      startedAt
    });

    let refusal: Error | null = null;
    try {
      await requireEmptyReplayBaseline();
    } catch (error) {
      refusal = error instanceof Error ? error : new Error("Replay baseline refusal failed.");
    }
    if (refusal === null || !refusal.message.includes("preexisting relevant process")) {
      throw new Error("The replay baseline did not safely refuse a live stale owned service.");
    }
    const afterRefusal = await queryRelevantProcesses();
    if (
      !afterRefusal.some((item) => sameProcessIdentity(item, identity!)) ||
      child.exitCode !== null ||
      child.signalCode !== null
    ) {
      throw new Error("The replay baseline refusal changed the test-owned stale service.");
    }
    const actualReplayRefusal = await runRejectedReplayLauncher(replay, afterRefusal);
    const afterActualReplayRefusal = await queryRelevantProcesses();
    if (
      !afterActualReplayRefusal.some((item) => sameProcessIdentity(item, identity!)) ||
      child.exitCode !== null ||
      child.signalCode !== null
    ) {
      throw new Error("The actual replay refusal changed the test-owned stale service.");
    }

    gracefulExitRequested = true;
    child.stdin.end();
    const exit = await waitForChildExit(child, shutdownTimeoutMs);
    if (exit.code !== 0 || exit.signal !== null || output().overflowed) {
      throw new Error("The exact stale service did not accept graceful stdin-EOF shutdown.");
    }
    await requireIdentityGoneTwice(identity);
    return {
      pid: identity.pid,
      parentPid: identity.parentPid,
      creationIdentity: identity.creationDate,
      exactExecutablePathConfirmed: true,
      replayRefusedBeforeLaunch: true,
      refusalTerminatedProcess: false,
      actualReplayEntryPoint: actualReplayRefusal,
      shutdownMethod: "stdin_eof",
      exitCode: 0,
      signalCode: null,
      twoAbsenceInventories: true
    };
  } finally {
    if (
      !gracefulExitRequested &&
      child.exitCode === null &&
      child.signalCode === null &&
      identity !== null
    ) {
      child.stdin.end();
      await waitForChildExit(child, shutdownTimeoutMs);
      await requireIdentityGoneTwice(identity);
    }
  }
}

async function exerciseActualReplayLauncher(
  replay: VerifiedPrivateReplay,
  recordDecisions: boolean,
  exerciseDuplicate: boolean
): Promise<ActualReplayProof> {
  const baseline = await requireEmptyReplayBaseline();
  const debuggingPort = await reserveHighLoopbackPort();
  const startedAt = Date.now();
  const launcher = startReplayLauncher(replay.launcherPath, {
    ...process.env,
    ELECTRON_RUN_AS_NODE: "1",
    NODE_OPTIONS: "--require=C:\\phase3b1-replay-must-not-load.cjs",
    NODE_PATH: "C:\\phase3b1-replay-must-not-resolve",
    CSS_DESKTOP_DEV_URL: "http://127.0.0.1:9",
    CSS_E2E_DATA_DIR: "C:\\phase3b1-replay-must-not-use",
    CSS_PACKAGED_E2E_SHUTDOWN_EVIDENCE_PATH:
      "C:\\phase3b1-replay-must-not-write-packaged-evidence.json",
    CSS_PHASE3B_RUNTIME_SHUTDOWN_EVIDENCE:
      "C:\\phase3b1-replay-must-not-write-runtime-evidence.json",
    [replayE2eEnvironment]: "1",
    [replayDebugPortEnvironment]: String(debuggingPort)
  });
  let ownership: LaunchOwnership | null = null;
  let browser: Browser | null = null;
  let sampler: OwnershipSampler | null = null;
  let shutdownCompleted = false;
  try {
    const rootPid = await withDeadline(
      launcher.reportedPid,
      launchTimeoutMs,
      "The actual replay launcher did not report its application PID."
    );
    const root = await waitForExactProcess({
      pid: rootPid,
      parentPid: requiredPid(launcher.child.pid, "PowerShell replay launcher"),
      name: appExecutableName,
      executablePath: replay.paths.executablePath,
      baseline,
      startedAt
    });
    ownership = {
      launcherPid: requiredPid(launcher.child.pid, "PowerShell replay launcher"),
      rootPid,
      baseline,
      packaged: replay.paths,
      processes: [{ ...root, kind: "app" }],
      confirmedExitedTransientProcesses: []
    };
    ownership = await waitForOwnedService(ownership);
    sampler = startOwnershipSampler(ownership);
    browser = await connectActualReplayBrowser(debuggingPort);
    const page = await requireActualReplayPage(browser);
    const session = await inspectRestoredSession(
      page,
      replay,
      recordDecisions
    );
    const completedSampler = sampler;
    sampler = null;
    await completedSampler.stop();
    ownership = await expandOwnership(ownership, true);
    const serviceBeforeDuplicate = requireOneOwnedService(ownership);
    const duplicate = exerciseDuplicate
      ? await exerciseLiveDuplicateLauncher(replay, ownership)
      : null;
    ownership = await expandOwnership(ownership, true);
    const serviceAfterDuplicate = requireOneOwnedService(ownership);
    if (
      !sameProcessIdentity(serviceBeforeDuplicate, serviceAfterDuplicate)
    ) {
      throw new Error(
        "The replay session changed its original owned service identity."
      );
    }

    await requestExactMainWindowClose(root, replay.paths.executablePath);
    const exit = await withDeadline(
      launcher.exited,
      shutdownTimeoutMs,
      "The actual replay launcher did not exit after graceful window close."
    );
    if (exit.code !== 0 || exit.signal !== null || launcher.output().overflowed) {
      throw new Error("The actual replay launcher did not exit cleanly.");
    }
    await waitForOwnedProcessesGone(ownership);
    await waitForBrowserDisconnected(browser);
    shutdownCompleted = true;
    return {
      session,
      process: {
        reportedRootPid: rootPid,
        rootParentPid: root.parentPid,
        rootCreationIdentity: root.creationDate,
        exactRootPathConfirmed: true,
        actualReplayEntryPointInspectedOverLoopbackCdp: true,
        poisonedParentExecutionEnvironmentSanitized: true,
        ownedDebuggingPort: debuggingPort,
        ownedProcesses: allOwnedProcessEvidence(ownership),
        duplicate,
        gracefulWindowClose: true,
        launcherExitCode: 0,
        remainingOwnedPids: [],
        twoAbsenceInventories: true,
        unrelatedProcessesTerminated: false
      }
    };
  } finally {
    if (sampler !== null) {
      try {
        await sampler.stop();
      } catch {
        // This path is reached only while another launch assertion is already
        // unwinding. Preserve that primary failure and continue exact-owned
        // graceful cleanup; a successful path stops and checks the sampler
        // before entering this finally block.
      }
    }
    if (!shutdownCompleted && ownership !== null) {
      const root = ownership.processes.find(
        (item) => item.pid === ownership!.rootPid && item.kind === "app"
      );
      if (root !== undefined) {
        const current = await queryRelevantProcesses();
        if (current.some((item) => sameProcessIdentity(item, root))) {
          await requestExactMainWindowClose(root, replay.paths.executablePath);
          await withDeadline(
            launcher.exited,
            shutdownTimeoutMs,
            "The owned replay launcher did not exit during graceful recovery."
          );
          await waitForOwnedProcessesGone(ownership);
        }
      }
    }
    if (browser !== null && browser.isConnected()) {
      await browser.close();
    }
  }
}

async function reserveHighLoopbackPort(): Promise<number> {
  for (let attempt = 0; attempt < 20; attempt += 1) {
    const port = 49_152 + randomBytes(2).readUInt16BE(0) % 16_384;
    const server = createServer();
    try {
      await new Promise<void>((resolve, reject) => {
        server.once("error", reject);
        server.listen({ host: "127.0.0.1", port, exclusive: true }, resolve);
      });
      await new Promise<void>((resolve, reject) => {
        server.close((error) => {
          if (error === undefined) resolve();
          else reject(error);
        });
      });
      return port;
    } catch {
      if (server.listening) {
        await new Promise<void>((resolve) => server.close(() => resolve()));
      }
    }
  }
  throw new Error("A bounded high loopback debugging port was unavailable.");
}

async function connectActualReplayBrowser(port: number): Promise<Browser> {
  const deadline = now() + launchTimeoutMs;
  while (now() < deadline) {
    try {
      return await chromium.connectOverCDP(`http://127.0.0.1:${String(port)}`, {
        timeout: 3_000
      });
    } catch {
      await delay(150);
    }
  }
  throw new Error("Playwright could not connect to the loopback-only actual replay session.");
}

async function requireActualReplayPage(browser: Browser): Promise<Page> {
  const deadline = now() + launchTimeoutMs;
  while (now() < deadline) {
    const pages = browser.contexts().flatMap((context) => context.pages());
    for (const page of pages) {
      try {
        const hasApi = await page.evaluate(
          () => typeof window.cinematicStory === "object"
        );
        if (hasApi) return page;
      } catch {
        // Navigation can replace the execution context while the packaged
        // renderer hydrates. Retry only within the bounded launch window.
      }
    }
    await delay(100);
  }
  throw new Error("The actual replay renderer page was unavailable over loopback CDP.");
}

async function waitForBrowserDisconnected(browser: Browser): Promise<void> {
  if (!browser.isConnected()) return;
  await withDeadline(
    new Promise<void>((resolve) => browser.once("disconnected", () => resolve())),
    5_000,
    "The actual replay CDP connection remained after application shutdown."
  );
}

function startOwnershipSampler(ownership: LaunchOwnership): OwnershipSampler {
  let stopped = false;
  let failure: Error | null = null;
  const completed = (async () => {
    while (!stopped) {
      try {
        await expandOwnership(
          ownership,
          true,
          now() + defaultProcessInventoryPolicy.totalDeadlineMs
        );
      } catch (error) {
        failure =
          error instanceof Error
            ? error
            : new Error("The replay ownership sampler failed.");
        return;
      }
      if (!stopped) await delay(100);
    }
  })();
  return {
    async stop() {
      stopped = true;
      await completed;
      if (failure !== null) throw failure;
    }
  };
}

async function runRejectedReplayLauncher(
  replay: VerifiedPrivateReplay,
  baseline: readonly ProcessIdentity[]
): Promise<Record<string, unknown>> {
  if (baseline.length === 0) {
    throw new Error("A rejected replay launch requires a non-empty relevant baseline.");
  }
  const launcher = startReplayLauncher(
    replay.launcherPath,
    replay.environment
  );
  const reportedPidOutcome = launcher.reportedPid.then(
    (pid) => ({ status: "reported" as const, pid }),
    () => ({ status: "absent" as const, pid: null })
  );
  const exit = await withDeadline(
    launcher.exited,
    launchTimeoutMs,
    "The safely rejected replay launcher did not exit within its deadline."
  );
  const reported = await withDeadline(
    reportedPidOutcome,
    5_000,
    "The safely rejected replay launcher did not close its PID channel."
  );
  const output = launcher.output();
  if (
    exit.code === null ||
    exit.code === 0 ||
    exit.signal !== null ||
    reported.status !== "absent" ||
    output.overflowed ||
    output.stdout.includes("CSS_REPLAY_LAUNCHER_PID=") ||
    !output.stderr.includes(
      "An exact replay process is already running; no process was changed."
    )
  ) {
    throw new Error("The actual replay entry point did not fail closed on a relevant process baseline.");
  }
  for (let observation = 0; observation < 2; observation += 1) {
    const current = await queryRelevantProcesses();
    if (current.some((item) => !containsProcessIdentity(baseline, item))) {
      throw new Error("A refused replay entry point created a relevant application or service process.");
    }
    if (observation === 0) await delay(200);
  }
  return {
    relevantBaselineCount: baseline.length,
    launcherPidReported: false,
    fixedRefusalReasonConfirmed: true,
    exitCode: exit.code,
    signalCode: null,
    relevantProcessCreated: false,
    processTerminationAttempted: false,
    twoNoDeltaInventories: true
  };
}

async function exerciseLiveDuplicateLauncher(
  replay: VerifiedPrivateReplay,
  original: LaunchOwnership
): Promise<Record<string, unknown>> {
  const before = await queryRelevantProcesses();
  const originalService = requireOneOwnedService(original);
  const launcherRefusal = await runRejectedReplayLauncher(replay, before);
  const afterLauncherRefusal = await queryRelevantProcesses();
  if (
    !afterLauncherRefusal.some((item) => sameProcessIdentity(item, originalService)) ||
    afterLauncherRefusal.some((item) => !containsProcessIdentity(before, item))
  ) {
    throw new Error("The duplicate replay-launcher refusal changed the original process tree.");
  }
  const startedAt = Date.now();
  const duplicate = spawn(replay.paths.executablePath, [], {
    cwd: path.dirname(replay.paths.executablePath),
    env: replay.environment,
    shell: false,
    windowsHide: true,
    stdio: "ignore"
  });
  const duplicatePid = requiredPid(duplicate.pid, "direct duplicate application");
  const observedNew = new Map<number, ProcessIdentity>();
  const exitPromise = childExit(duplicate);
  let exited = false;
  let completed = false;
  const deadline = now() + launchTimeoutMs;
  void exitPromise.then(
    () => {
      exited = true;
    },
    () => {
      exited = true;
    }
  );
  try {
    let postExitObservations = 0;
    while (!exited || postExitObservations < 2) {
      if (now() >= deadline) {
        throw new Error("The live duplicate launcher did not exit within its bounded deadline.");
      }
      const current = await queryRelevantProcesses();
      if (!current.some((item) => sameProcessIdentity(item, originalService))) {
        throw new Error("The original service did not remain healthy during duplicate launch.");
      }
      for (const item of current) {
        if (containsProcessIdentity(before, item)) continue;
        if (item.name === serviceExecutableName) {
          throw new Error("A live duplicate launcher created a second service process.");
        }
        if (
          item.name !== appExecutableName ||
          item.executablePath === null ||
          !samePath(item.executablePath, replay.paths.executablePath) ||
          item.pid !== duplicatePid ||
          item.parentPid !== process.pid ||
          Date.parse(item.creationDate) + 1_000 < startedAt
        ) {
          throw new Error("A live duplicate launcher created an ambiguous relevant process.");
        }
        observedNew.set(item.pid, item);
      }
      if (exited) postExitObservations += 1;
      await delay(50);
    }
    const exit = await exitPromise;
    if (
      duplicatePid === original.rootPid ||
      exit.code !== 0 ||
      exit.signal !== null ||
      observedNew.size > 1 ||
      (observedNew.size === 1 && !observedNew.has(duplicatePid))
    ) {
      throw new Error("The live duplicate did not exit as one bounded single-instance launch.");
    }
    const after = await queryRelevantProcesses();
    const delta = after.filter((item) => !containsProcessIdentity(before, item));
    if (delta.length !== 0) {
      throw new Error("The live duplicate left a relevant process behind.");
    }
    completed = true;
    return {
      launcherRefusal,
      directExecutablePid: duplicatePid,
      transientIdentityObserved: observedNew.size === 1,
      secondServiceObserved: false,
      originalServicePid: originalService.pid,
      originalServiceCreationIdentity: originalService.creationDate,
      originalServiceRemainedHealthy: true,
      duplicateExitCode: 0,
      remainingDuplicateRelevantPids: [],
      unrelatedProcessesTerminated: false
    };
  } finally {
    if (!completed && observedNew.size === 1) {
      const transient = [...observedNew.values()][0];
      if (transient !== undefined) {
        const current = await queryRelevantProcesses();
        if (current.some((item) => sameProcessIdentity(item, transient))) {
          await requestExactMainWindowClose(transient, replay.paths.executablePath);
          await withDeadline(
            exitPromise,
            shutdownTimeoutMs,
            "The exact duplicate did not exit during graceful recovery."
          );
          await requireIdentityGoneTwice(transient);
        }
      }
    }
  }
}

async function inspectRestoredSession(
  page: Page,
  replay: VerifiedPrivateReplay,
  recordDecisions: boolean
): Promise<RestoredSessionProof> {
  await waitForBackendReady(page);
  const projectId = await restoreSoleProject(page, replay.projectId);
  const beforeClips = await listClips(page, projectId);
  const indexedClips = bindIndexedClips(beforeClips, replay.clips);
  await proveIndexedClipUiAndPlayback(page, projectId, indexedClips);
  const decisions = await verifyOrRecordDecisions(
    page,
    projectId,
    indexedClips,
    replay.decisions,
    recordDecisions
  );
  const afterClips = await listClips(page, projectId);
  expect(sortedIds(afterClips)).toEqual(sortedIds(beforeClips));
  const workspace = await readWorkspace(page, projectId);
  if (workspace.voiceReadinessSnapshot !== null) {
    throw new Error("Voice Readiness became approved despite the mixed human listening outcome.");
  }
  return {
    projectId,
    allClipIds: sortedIds(afterClips),
    decisions,
    backendState: "ready",
    voiceReadinessBlocked: true,
    authenticatedClipIds: replay.clips.map((item) => item.auditionClipId)
  };
}

async function proveIndexedClipUiAndPlayback(
  page: Page,
  projectId: string,
  clips: readonly AuditionClip[]
): Promise<void> {
  await page.getByRole("button", { name: "Auditions", exact: true }).click();
  const workspace = page.locator(".auditions-workspace");
  await expect(
    page.getByRole("heading", { name: "Auditions & Pronunciation", exact: true })
  ).toBeVisible({ timeout: backendTimeoutMs });
  await expect(workspace).toHaveAttribute("aria-busy", "false", {
    timeout: backendTimeoutMs
  });
  for (const clip of clips) {
    await expect(
      page.getByTestId(`listened-exact-clip-${clip.auditionClipId}`)
    ).toBeVisible({ timeout: 30_000 });
    await loadAuthenticatedAudio(page, projectId, clip);
  }

  const strongestClip = clips.at(-1);
  if (strongestClip === undefined) {
    throw new Error("The indexed private playback set was empty.");
  }
  const card = page
    .getByTestId(`listened-exact-clip-${strongestClip.auditionClipId}`)
    .locator("xpath=ancestor::article");
  await card.getByRole("button", { name: "Load audio", exact: true }).click();
  await expect(
    page.getByText("The authenticated audition audio is ready.", { exact: true })
  ).toBeVisible({ timeout: 30_000 });
  const player = page.getByLabel("Audition clip player", { exact: true });
  await expect(player).toHaveAttribute("src", /^blob:/u);
  await page.getByRole("button", { name: "Replay", exact: true }).click();
  await expect.poll(() => player.evaluate((element: HTMLAudioElement) => element.paused), {
    timeout: 10_000
  }).toBe(false);
  await page.getByRole("button", { name: "Stop", exact: true }).click();
  await expect.poll(() => player.evaluate((element: HTMLAudioElement) => element.paused)).toBe(true);
}

async function verifyOrRecordDecisions(
  page: Page,
  projectId: string,
  clips: readonly AuditionClip[],
  expected: readonly ExpectedListeningDecision[],
  recordDecisions: boolean
): Promise<readonly AuditionReviewDecision[]> {
  const clipById = new Map(clips.map((clip) => [clip.auditionClipId, clip]));
  const roleOrder = [...new Set(clips.map((clip) => clip.roleId))];
  const histories = new Map<string, readonly AuditionReviewDecision[]>();
  for (const roleId of roleOrder) {
    histories.set(roleId, await readReviewHistory(page, projectId, roleId));
  }

  const existingByClip = new Map<string, AuditionReviewDecision>();
  for (const [roleId, history] of histories) {
    for (const decision of history) {
      const attestation = decision.listeningAttestation;
      if (
        decision.actor.classification === "human" &&
        attestation !== null &&
        attestation !== undefined
      ) {
        if (!expected.some((item) => item.auditionClipId === attestation.auditionClipId)) {
          throw new Error(`A conflicting real human listening decision exists for role ${roleId}.`);
        }
        if (existingByClip.has(attestation.auditionClipId)) {
          throw new Error("An exact private clip had more than one human listening decision.");
        }
        existingByClip.set(attestation.auditionClipId, decision);
      }
    }
  }

  for (const roleId of roleOrder) {
    let missingObserved = false;
    for (const item of expected) {
      const clip = clipById.get(item.auditionClipId);
      if (clip?.roleId !== roleId) continue;
      const exists = existingByClip.has(item.auditionClipId);
      if (!exists) missingObserved = true;
      if (exists && missingObserved) {
        throw new Error("Private human listening decisions were not persisted in chronological role order.");
      }
    }
  }

  for (const item of expected) {
    const clip = clipById.get(item.auditionClipId);
    if (clip === undefined) {
      throw new Error("An expected private human decision lost its exact clip.");
    }
    const prior = existingByClip.get(item.auditionClipId);
    if (prior !== undefined) {
      verifyStoredDecision(prior, clip, item);
      continue;
    }
    if (!recordDecisions) {
      throw new Error(
        `The exact human listening decision for ${item.opaqueFileName} is absent; rerun once with ${recordEnvironment}=1 only after authorized listening.`
      );
    }
    const history = await readReviewHistory(page, projectId, clip.roleId);
    const latest = history[0] ?? null;
    const recorded = await decideExactReview(
      page,
      projectId,
      clip,
      item,
      latest?.decisionId ?? null
    );
    verifyStoredDecision(recorded, clip, item);
    existingByClip.set(item.auditionClipId, recorded);
  }

  const finalClips = bindIndexedClips(await listClips(page, projectId), expected);
  return finalClips.map((clip, index) => {
    const decision = clip.review.latestDecision;
    const expectation = expected[index];
    if (decision === null || expectation === undefined) {
      throw new Error("An exact private human decision did not project onto its clip.");
    }
    verifyStoredDecision(decision, clip, expectation);
    return decision;
  });
}

async function decideExactReview(
  page: Page,
  projectId: string,
  clip: AuditionClip,
  expected: ExpectedListeningDecision,
  supersedesDecisionId: string | null
): Promise<AuditionReviewDecision> {
  const response = await page.evaluate(
    async ({ id, clipValue, expectation, supersedes }) => {
      const disposition = expectation.disposition;
      const decision =
        disposition === "acceptable"
          ? "approve"
          : disposition === "unacceptable"
            ? "reject"
            : "request_changes";
      const result = await window.cinematicStory.auditions.decideReview({
        projectId: id,
        gateId: "per_role_audition_review",
        roleId: clipValue.roleId,
        reviewId: clipValue.review.reviewId,
        expectedReviewRevision: clipValue.review.revision,
        expectedEvidenceFingerprint: clipValue.review.evidence.evidenceFingerprint,
        decision,
        rationale: expectation.rationale,
        supersedesDecisionId: supersedes,
        listeningAttestation: {
          auditionClipId: clipValue.auditionClipId,
          auditionClipRevision: clipValue.revision,
          auditionClipFingerprint: clipValue.clipFingerprint,
          audioArtifactId: clipValue.audioArtifact.audioArtifactId,
          audioArtifactSha256: clipValue.audioArtifact.sha256,
          listened: true,
          disposition
        },
        idempotencyKey: `phase3b1-human-${expectation.opaqueFileName.replace(".wav", "")}-${clipValue.auditionClipId.slice(0, 12)}`
      });
      return result.ok
        ? ({ ok: true, decision: result.value.decision } as const)
        : ({ ok: false, code: result.error.code, message: result.error.message } as const);
    },
    { id: projectId, clipValue: clip, expectation: expected, supersedes: supersedesDecisionId }
  );
  if (!response.ok) {
    throw new Error(`Exact human listening decision failed: ${response.code}: ${response.message}`);
  }
  return response.decision;
}

function verifyStoredDecision(
  decision: AuditionReviewDecision,
  clip: AuditionClip,
  expected: ExpectedListeningDecision
): void {
  const expectedDecision =
    expected.disposition === "acceptable"
      ? "approved"
      : expected.disposition === "unacceptable"
        ? "rejected"
        : "changes_requested";
  const attestation = decision.listeningAttestation;
  if (
    decision.reviewId !== clip.review.reviewId ||
    decision.projectId !== clip.projectId ||
    decision.gateId !== "per_role_audition_review" ||
    decision.roleId !== clip.roleId ||
    decision.decision !== expectedDecision ||
    decision.actor.classification !== "human" ||
    decision.actor.actorId !== "local_user" ||
    decision.rationale !== expected.rationale ||
    decision.expectedReviewRevision !== clip.review.revision ||
    decision.evidenceFingerprint !== clip.review.evidence.evidenceFingerprint ||
    decision.immutable !== true ||
    !isTimestamp(decision.decidedAt) ||
    attestation === null ||
    attestation === undefined ||
    attestation.auditionClipId !== clip.auditionClipId ||
    attestation.auditionClipRevision !== clip.revision ||
    attestation.auditionClipFingerprint !== clip.clipFingerprint ||
    attestation.audioArtifactId !== clip.audioArtifact.audioArtifactId ||
    attestation.audioArtifactSha256 !== expected.audioSha256 ||
    attestation.audioArtifactSha256 !== clip.audioArtifact.sha256 ||
    attestation.listened !== true ||
    attestation.disposition !== expected.disposition ||
    attestation.actor.classification !== "human" ||
    attestation.actor.actorId !== "local_user" ||
    attestation.recordedAt !== decision.decidedAt ||
    attestation.rationale !== expected.rationale ||
    !/^[a-f0-9]{64}$/u.test(attestation.attestationFingerprint) ||
    attestation.immutable !== true
  ) {
    throw new Error(`The stored private decision for ${expected.opaqueFileName} did not match its exact service-derived evidence.`);
  }
}

async function readReviewHistory(
  page: Page,
  projectId: string,
  roleId: string
): Promise<readonly AuditionReviewDecision[]> {
  const response = await page.evaluate(async ({ id, role }) => {
    const result = await window.cinematicStory.auditions.listReviewDecisions({
      projectId: id,
      gateId: "per_role_audition_review",
      roleId: role,
      limit: 200
    });
    return result.ok
      ? ({ ok: true, value: result.value } as const)
      : ({ ok: false, code: result.error.code, message: result.error.message } as const);
  }, { id: projectId, role: roleId });
  if (!response.ok) {
    throw new Error(`Private review history failed: ${response.code}: ${response.message}`);
  }
  if (response.value.nextCursor !== undefined || response.value.items.length !== response.value.total) {
    throw new Error("The bounded private review history was incomplete.");
  }
  return response.value.items;
}

async function waitForBackendReady(page: Page): Promise<void> {
  const deadline = now() + backendTimeoutMs;
  let last = "unavailable";
  while (now() < deadline) {
    const result = await page.evaluate(async () => {
      const status = await window.cinematicStory.backend.getStatus();
      return status.ok
        ? ({ ok: true, state: status.value.state, message: status.value.message } as const)
        : ({ ok: false, code: status.error.code, message: status.error.message } as const);
    });
    if (result.ok && result.state === "ready") return;
    last = result.ok ? `${result.state}: ${result.message}` : `${result.code}: ${result.message}`;
    await delay(250);
  }
  throw new Error(`The retained private backend did not reach ready (${last}).`);
}

async function restoreSoleProject(page: Page, expectedProjectId: string): Promise<string> {
  const only = await page.evaluate(async (expectedId) => {
    const listed = await window.cinematicStory.projects.list();
    if (!listed.ok) {
      throw new Error(`Private project list failed: ${listed.error.code}: ${listed.error.message}`);
    }
    const only = listed.value.items[0];
    if (
      only === undefined ||
      listed.value.items.length !== 1 ||
      listed.value.nextCursor !== undefined ||
      only.projectId !== expectedId
    ) {
      throw new Error("The retained replay state did not contain exactly one project.");
    }
    return { projectId: only.projectId, name: only.name };
  }, expectedProjectId);
  const activeProject = page.locator(".project-link.active");
  await expect(activeProject).toHaveCount(1, { timeout: backendTimeoutMs });
  await expect(activeProject).toContainText(only.name);
  await expect(
    page.getByRole("heading", { name: only.name, exact: true, level: 1 })
  ).toBeVisible({ timeout: backendTimeoutMs });
  return only.projectId;
}

async function listClips(page: Page, projectId: string): Promise<readonly AuditionClip[]> {
  const response = await page.evaluate(async (id) => {
    const result = await window.cinematicStory.auditions.listClips({
      projectId: id,
      limit: 200
    });
    return result.ok
      ? ({ ok: true, value: result.value } as const)
      : ({ ok: false, code: result.error.code, message: result.error.message } as const);
  }, projectId);
  if (!response.ok) {
    throw new Error(`Private clip list failed: ${response.code}: ${response.message}`);
  }
  if (response.value.nextCursor !== undefined || response.value.items.length !== response.value.total) {
    throw new Error("The bounded private clip list was incomplete.");
  }
  return response.value.items;
}

async function readWorkspace(page: Page, projectId: string) {
  const response = await page.evaluate(async (id) => {
    const result = await window.cinematicStory.auditions.getWorkspace({
      projectId: id,
      roleLimit: 50
    });
    return result.ok
      ? ({ ok: true, workspace: result.value.workspace } as const)
      : ({ ok: false, code: result.error.code, message: result.error.message } as const);
  }, projectId);
  if (!response.ok) {
    throw new Error(`Private audition workspace failed: ${response.code}: ${response.message}`);
  }
  return response.workspace;
}

async function loadAuthenticatedAudio(
  page: Page,
  projectId: string,
  clip: AuditionClip
): Promise<void> {
  const loaded = await page.evaluate(async ({ id, value }) => {
    const result = await window.cinematicStory.auditions.loadAudio({
      projectId: id,
      auditionClipId: value.auditionClipId,
      auditionSessionId: value.auditionSessionId,
      audioArtifactId: value.audioArtifact.audioArtifactId,
      expectedClipRevision: value.revision,
      expectedClipFingerprint: value.clipFingerprint,
      expectedArtifactSha256: value.audioArtifact.sha256,
      mediaType: "audio/wav",
      byteSize: value.audioArtifact.byteSize
    });
    return result.ok
      ? ({
          ok: true,
          bytes: [...new Uint8Array(result.value.bytes)],
          mediaType: result.value.mediaType,
          byteSize: result.value.byteSize,
          sha256: result.value.sha256
        } as const)
      : ({ ok: false, code: result.error.code, message: result.error.message } as const);
  }, { id: projectId, value: clip });
  if (!loaded.ok) {
    throw new Error(`Authenticated private audio load failed: ${loaded.code}: ${loaded.message}`);
  }
  const bytes = Uint8Array.from(loaded.bytes);
  if (
    loaded.mediaType !== "audio/wav" ||
    loaded.byteSize !== bytes.byteLength ||
    loaded.sha256 !== clip.audioArtifact.sha256 ||
    sha256(bytes) !== clip.audioArtifact.sha256 ||
    Buffer.from(bytes.subarray(0, 4)).toString("ascii") !== "RIFF" ||
    Buffer.from(bytes.subarray(8, 12)).toString("ascii") !== "WAVE"
  ) {
    throw new Error("Authenticated private playback bytes failed exact WAV integrity checks.");
  }
}

function bindIndexedClips(
  clips: readonly AuditionClip[],
  index: readonly (
    Pick<ListeningIndexClip, "auditionClipId" | "audioSha256"> &
      Partial<Pick<ListeningIndexClip, "auditionClipFingerprint" | "audioArtifactId">>
  )[]
): readonly AuditionClip[] {
  return index.map((expected) => {
    const matches = clips.filter((clip) => clip.auditionClipId === expected.auditionClipId);
    const clip = matches[0];
    if (
      matches.length !== 1 ||
      clip === undefined ||
      clip.providerClass !== "real_local" ||
      clip.audioArtifact.sha256 !== expected.audioSha256 ||
      (expected.auditionClipFingerprint !== undefined &&
        clip.clipFingerprint !== expected.auditionClipFingerprint) ||
      (expected.audioArtifactId !== undefined &&
        clip.audioArtifact.audioArtifactId !== expected.audioArtifactId) ||
      clip.review.evidence.auditionClipId !== clip.auditionClipId ||
      clip.review.evidence.auditionClipRevision !== clip.revision
    ) {
      throw new Error("An indexed real-provider clip did not restore with its exact review and audio binding.");
    }
    return clip;
  });
}

async function waitForOwnedService(ownership: LaunchOwnership): Promise<LaunchOwnership> {
  const deadline = now() + backendTimeoutMs;
  let current = ownership;
  while (now() < deadline) {
    current = await expandOwnership(current, false, deadline);
    const serviceRoots = ownedServiceRoots(current);
    if (serviceRoots.length === 1) return current;
    if (serviceRoots.length > 1) {
      throw new Error("The replay launch created more than one owned service root.");
    }
    await delay(100);
  }
  throw new Error("The replay launch did not establish one exact owned service.");
}

async function expandOwnership(
  ownership: LaunchOwnership,
  requireService: boolean,
  deadlineAt = now() + defaultProcessInventoryPolicy.totalDeadlineMs
): Promise<LaunchOwnership> {
  const current = await queryRelevantProcesses(deadlineAt);
  const adopted = await adoptVerifiedProcessTreeWithPathConfirmation({
    current,
    baseline: ownership.baseline,
    owned: ownership.processes,
    confirmedExitedTransientProcesses: ownership.confirmedExitedTransientProcesses,
    rootPid: ownership.rootPid,
    packaged: ownership.packaged,
    deadlineAt,
    queryCurrent: queryRelevantProcesses
  });
  ownership.processes = adopted.ownedProcesses;
  ownership.confirmedExitedTransientProcesses = adopted.confirmedExitedTransientProcesses;
  if (ownership.processes.length === 0) {
    throw new Error("The exact replay root identity was lost.");
  }
  if (requireService) requireOneOwnedService(ownership);
  return ownership;
}

function requireOneOwnedService(ownership: LaunchOwnership): OwnedProcess {
  const serviceRoots = ownedServiceRoots(ownership);
  if (serviceRoots.length !== 1 || serviceRoots[0] === undefined) {
    throw new Error("The replay did not own exactly one embedded service root.");
  }
  return serviceRoots[0];
}

function ownedServiceRoots(ownership: LaunchOwnership): readonly OwnedProcess[] {
  return ownedServiceRootProcesses({
    owned: ownership.processes,
    rootPid: ownership.rootPid,
    packaged: ownership.packaged
  });
}

async function waitForOwnedProcessesGone(
  ownership: LaunchOwnership
): Promise<readonly OwnedProcess[]> {
  const deadline = now() + shutdownTimeoutMs;
  let owned = ownership.processes;
  let transient = ownership.confirmedExitedTransientProcesses;
  let absenceCount = 0;
  while (now() < deadline) {
    const current = await queryRelevantProcesses(deadline);
    const adopted = await adoptVerifiedProcessTreeWithPathConfirmation({
      current,
      baseline: ownership.baseline,
      owned,
      confirmedExitedTransientProcesses: transient,
      rootPid: ownership.rootPid,
      packaged: ownership.packaged,
      deadlineAt: deadline,
      queryCurrent: queryRelevantProcesses
    });
    owned = adopted.ownedProcesses;
    transient = adopted.confirmedExitedTransientProcesses;
    const remaining = remainingOwnedProcesses(adopted.observedProcesses, owned);
    if (remaining.length === 0) {
      absenceCount += 1;
      if (absenceCount >= 2) {
        ownership.processes = owned;
        ownership.confirmedExitedTransientProcesses = transient;
        return owned;
      }
    } else {
      absenceCount = 0;
    }
    await delay(200);
  }
  throw new Error("Exact replay-owned processes did not produce two absent inventories.");
}

async function requestExactMainWindowClose(
  identity: ProcessIdentity,
  executablePath: string
): Promise<void> {
  const current = await queryRelevantProcesses();
  const exact = current.filter(
    (item) =>
      sameProcessIdentity(item, identity) &&
      item.executablePath !== null &&
      samePath(item.executablePath, executablePath)
  );
  if (exact.length !== 1) {
    throw new Error("The exact owned replay window identity was unavailable for graceful close.");
  }
  const escapedPath = executablePath.replaceAll("'", "''");
  const script = [
    "$ErrorActionPreference = 'Stop'",
    `$processId = ${String(identity.pid)}`,
    `$expectedPath = [IO.Path]::GetFullPath('${escapedPath}')`,
    "$owned = Get-Process -Id $processId -ErrorAction Stop",
    "$actualPath = [IO.Path]::GetFullPath([string]$owned.Path)",
    "if (-not [string]::Equals($actualPath, $expectedPath, [StringComparison]::OrdinalIgnoreCase)) { exit 3 }",
    "if (-not $owned.CloseMainWindow()) { exit 4 }",
    "[Console]::Out.Write('CLOSE_REQUESTED')"
  ].join("\n");
  const result = spawnSync(
    "powershell.exe",
    ["-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
    {
      encoding: "utf8",
      shell: false,
      windowsHide: true,
      timeout: 15_000,
      maxBuffer: maximumChildOutputBytes
    }
  );
  if (
    result.error !== undefined ||
    result.status !== 0 ||
    String(result.stdout).trim() !== "CLOSE_REQUESTED"
  ) {
    throw new Error("The exact owned replay window rejected graceful close.");
  }
}

function startReplayLauncher(
  launcherPath: string,
  environment: NodeJS.ProcessEnv = process.env
): ReplayLauncher {
  const child = spawn(
    "powershell.exe",
    ["-NoLogo", "-NoProfile", "-NonInteractive", "-File", launcherPath],
    {
      cwd: path.dirname(launcherPath),
      env: environment,
      shell: false,
      windowsHide: true,
      stdio: ["pipe", "pipe", "pipe"]
    }
  );
  let stdout = "";
  let stderr = "";
  let overflowed = false;
  let resolvedPid: number | null = null;
  let resolvePid: (pid: number) => void = () => undefined;
  let rejectPid: (error: Error) => void = () => undefined;
  const reportedPid = new Promise<number>((resolve, reject) => {
    resolvePid = resolve;
    rejectPid = reject;
  });
  const capture = (target: "stdout" | "stderr", chunk: Buffer) => {
    const value = chunk.toString("utf8");
    if (Buffer.byteLength(stdout + stderr + value, "utf8") > maximumChildOutputBytes) {
      overflowed = true;
      return;
    }
    if (target === "stdout") {
      stdout += value;
      const matches = [...stdout.matchAll(/CSS_REPLAY_LAUNCHER_PID=(\d+)/gu)];
      for (const match of matches) {
        const pid = Number(match[1]);
        if (!Number.isSafeInteger(pid) || pid <= 0 || (resolvedPid !== null && resolvedPid !== pid)) {
          rejectPid(new Error("The replay launcher reported an ambiguous PID."));
          continue;
        }
        if (resolvedPid === null) {
          resolvedPid = pid;
          resolvePid(pid);
        }
      }
    } else {
      stderr += value;
    }
  };
  child.stdout.on("data", (chunk: Buffer) => capture("stdout", chunk));
  child.stderr.on("data", (chunk: Buffer) => capture("stderr", chunk));
  child.once("error", rejectPid);
  child.once("close", () => {
    if (resolvedPid === null) {
      rejectPid(new Error("The replay launcher closed before reporting its PID."));
    }
  });
  const exited = new Promise<{ readonly code: number | null; readonly signal: NodeJS.Signals | null }>(
    (resolve, reject) => {
      child.once("error", reject);
      child.once("exit", (code, signal) => {
        resolve({ code, signal });
      });
    }
  );
  return {
    child,
    reportedPid,
    exited,
    output: () => ({ stdout, stderr, overflowed })
  };
}

function captureBoundedOutput(child: ChildProcessWithoutNullStreams) {
  let stdout = "";
  let stderr = "";
  let overflowed = false;
  const capture = (target: "stdout" | "stderr", chunk: Buffer) => {
    const value = chunk.toString("utf8");
    if (Buffer.byteLength(stdout + stderr + value, "utf8") > maximumChildOutputBytes) {
      overflowed = true;
      return;
    }
    if (target === "stdout") stdout += value;
    else stderr += value;
  };
  child.stdout.on("data", (chunk: Buffer) => capture("stdout", chunk));
  child.stderr.on("data", (chunk: Buffer) => capture("stderr", chunk));
  return () => ({ stdout, stderr, overflowed });
}

async function waitForFixedOutput(
  child: ChildProcessWithoutNullStreams,
  output: () => { readonly stdout: string; readonly stderr: string; readonly overflowed: boolean },
  pattern: RegExp,
  timeoutMs: number
): Promise<void> {
  const deadline = now() + timeoutMs;
  while (now() < deadline) {
    const captured = output();
    if (captured.overflowed) throw new Error("An owned child exceeded its bounded output limit.");
    if (pattern.test(captured.stdout)) return;
    if (child.exitCode !== null || child.signalCode !== null) {
      throw new Error("The exact owned child exited before its fixed readiness record.");
    }
    await delay(50);
  }
  throw new Error("The exact owned child did not emit its fixed readiness record.");
}

async function waitForChildExit(
  child: ChildProcessWithoutNullStreams,
  timeoutMs: number
): Promise<{ readonly code: number | null; readonly signal: NodeJS.Signals | null }> {
  if (child.exitCode !== null || child.signalCode !== null) {
    return { code: child.exitCode, signal: child.signalCode };
  }
  const [code, signal] = await withDeadline(
    once(child, "exit") as Promise<[number | null, NodeJS.Signals | null]>,
    timeoutMs,
    "The exact owned child did not exit after graceful shutdown."
  );
  return { code, signal };
}

function childExit(
  child: ChildProcess
): Promise<{ readonly code: number | null; readonly signal: NodeJS.Signals | null }> {
  if (child.exitCode !== null || child.signalCode !== null) {
    return Promise.resolve({ code: child.exitCode, signal: child.signalCode });
  }
  return new Promise((resolve, reject) => {
    child.once("error", reject);
    child.once("exit", (code, signal) => resolve({ code, signal }));
  });
}

async function waitForExactProcess({
  pid,
  parentPid,
  name,
  executablePath,
  baseline,
  startedAt
}: {
  readonly pid: number;
  readonly parentPid?: number;
  readonly name: string;
  readonly executablePath: string;
  readonly baseline: readonly ProcessIdentity[];
  readonly startedAt: number;
}): Promise<ProcessIdentity> {
  const deadline = now() + launchTimeoutMs;
  while (now() < deadline) {
    const current = await queryRelevantProcesses(deadline);
    const matches = current.filter(
      (item) =>
        item.pid === pid &&
        (parentPid === undefined || item.parentPid === parentPid) &&
        item.name === name &&
        item.executablePath !== null &&
        samePath(item.executablePath, executablePath) &&
        !containsProcessIdentity(baseline, item) &&
        Date.parse(item.creationDate) + 1_000 >= startedAt
    );
    if (matches.length > 1) throw new Error("An exact replay process identity was ambiguous.");
    if (matches[0] !== undefined) return matches[0];
    await delay(100);
  }
  throw new Error("An exact replay process identity could not be established.");
}

async function requireEmptyReplayBaseline(): Promise<readonly ProcessIdentity[]> {
  const baseline = await queryRelevantProcesses();
  if (baseline.length !== 0) {
    throw new Error(
      `The private replay safely refuses a preexisting relevant process baseline (${baseline.length} observed); no termination was attempted.`
    );
  }
  return baseline;
}

async function requireTwoEmptyRelevantInventories(): Promise<void> {
  for (let observation = 0; observation < 2; observation += 1) {
    const current = await queryRelevantProcesses();
    if (current.length !== 0) {
      throw new Error("A relevant application or service process remained after exact-owned shutdown.");
    }
    if (observation === 0) await delay(200);
  }
}

async function requireIdentityGoneTwice(identity: ProcessIdentity): Promise<void> {
  const deadline = now() + shutdownTimeoutMs;
  let absent = 0;
  while (now() < deadline) {
    const current = await queryRelevantProcesses(deadline);
    if (current.some((item) => sameProcessIdentity(item, identity))) absent = 0;
    else absent += 1;
    if (absent >= 2) return;
    await delay(200);
  }
  throw new Error("The exact owned process did not produce two absent inventories.");
}

async function requirePersistentStaleLockMarker(
  replay: VerifiedPrivateReplay
): Promise<Record<string, unknown>> {
  await requireTwoEmptyRelevantInventories();
  const marker = path.join(
    replay.stateDirectory,
    "LocalAppData",
    "Cinematic Story Studio",
    ".cinematic-story-studio.lock"
  );
  const [metadata, canonical] = await Promise.all([lstat(marker), realpath(marker)]);
  if (!metadata.isFile() || metadata.isSymbolicLink() || !samePath(marker, canonical)) {
    throw new Error("The persistent stale service-lock marker was unavailable.");
  }
  return {
    relativePath: "LocalAppData/Cinematic Story Studio/.cinematic-story-studio.lock",
    byteSize: metadata.size,
    presentWithNoLiveRelevantPid: true,
    removedBeforeRelaunch: false
  };
}

async function queryRelevantProcesses(deadlineAt?: number): Promise<readonly ProcessIdentity[]> {
  return inventory.query({ deadlineAt });
}

async function resolveBoundExecutables(
  contract: Phase3b1PrivateReplayContract
): Promise<PackagedProcessPaths> {
  const executablePath = path.join(repositoryRoot, ...contract.executable.relativePath.split("/"));
  const applicationArchivePath = path.join(
    repositoryRoot,
    ...contract.applicationArchive.relativePath.split("/")
  );
  const serviceExecutablePath = path.join(repositoryRoot, ...contract.service.relativePath.split("/"));
  await verifyFileEvidence(executablePath, contract.executable);
  await verifyFileEvidence(applicationArchivePath, contract.applicationArchive);
  await verifyFileEvidence(serviceExecutablePath, contract.service);
  return {
    executablePath: await realpath(executablePath),
    serviceExecutablePath: await realpath(serviceExecutablePath)
  };
}

async function verifyFileEvidence(
  filePath: string,
  evidence: Phase3b1PrivateReplayContract["executable"]
): Promise<void> {
  const [metadata, canonical, digest] = await Promise.all([
    lstat(filePath),
    realpath(filePath),
    sha256File(filePath)
  ]);
  if (
    !metadata.isFile() ||
    metadata.isSymbolicLink() ||
    !samePath(canonical, filePath) ||
    !isStrictChild(repositoryRoot, canonical) ||
    metadata.size !== evidence.byteSize ||
    digest !== evidence.sha256
  ) {
    throw new Error("An exact private replay application component fingerprint changed.");
  }
}

async function verifyPrivateWavs(
  packageDirectory: string,
  clips: readonly ListeningIndexClip[]
): Promise<void> {
  for (const clip of clips) {
    const filePath = path.join(packageDirectory, clip.opaqueFileName);
    const [metadata, canonical, digest] = await Promise.all([
      lstat(filePath),
      realpath(filePath),
      sha256File(filePath)
    ]);
    if (
      !metadata.isFile() ||
      metadata.isSymbolicLink() ||
      !samePath(canonical, filePath) ||
      !isStrictChild(packageDirectory, canonical) ||
      metadata.size !== clip.byteSize ||
      digest !== clip.audioSha256
    ) {
      throw new Error(`The exact private WAV ${clip.opaqueFileName} fingerprint changed.`);
    }
  }
}

function parseListeningIndex(value: unknown): ListeningIndex {
  const record = requireRecord(value, "private listening index");
  if (
    typeof record.projectId !== "string" ||
    !isSafeId(record.projectId) ||
    !Array.isArray(record.clips) ||
    record.clips.length !== 6
  ) {
    throw new Error("The private listening index must contain exactly six clips.");
  }
  const clips = record.clips.map((raw, index) => {
    const clip = requireRecord(raw, "private listening clip");
    const opaqueFileName = `clip-${String(index + 1).padStart(2, "0")}.wav`;
    if (
      clip.opaqueFileName !== opaqueFileName ||
      typeof clip.auditionClipId !== "string" ||
      !isSafeId(clip.auditionClipId) ||
      typeof clip.audioSha256 !== "string" ||
      !/^[a-f0-9]{64}$/u.test(clip.audioSha256) ||
      typeof clip.auditionClipFingerprint !== "string" ||
      !/^[a-f0-9]{64}$/u.test(clip.auditionClipFingerprint) ||
      typeof clip.audioArtifactId !== "string" ||
      !isSafeId(clip.audioArtifactId) ||
      !Number.isSafeInteger(clip.byteSize) ||
      Number(clip.byteSize) <= 44
    ) {
      throw new Error("A private listening-index clip identity was invalid.");
    }
    return {
      opaqueFileName,
      auditionClipId: clip.auditionClipId,
      auditionClipFingerprint: clip.auditionClipFingerprint,
      audioArtifactId: clip.audioArtifactId,
      audioSha256: clip.audioSha256,
      byteSize: Number(clip.byteSize)
    };
  });
  if (new Set(clips.map((item) => item.auditionClipId)).size !== 6) {
    throw new Error("The private listening index repeated a clip identity.");
  }
  return { projectId: record.projectId, clips };
}

function parseExpectedDecisions(
  value: unknown,
  clips: readonly ListeningIndexClip[]
): readonly ExpectedListeningDecision[] {
  const record = requireRecord(value, "private expected decisions");
  requireExactKeys(record, ["schemaVersion", "evidenceClassification", "decisions"]);
  if (
    record.schemaVersion !== 1 ||
    record.evidenceClassification !== "private_human_listening_expectations" ||
    !Array.isArray(record.decisions) ||
    record.decisions.length !== 6
  ) {
    throw new Error("The private expected-decision file was invalid.");
  }
  return record.decisions.map((raw, index) => {
    const item = requireRecord(raw, "private expected decision");
    requireExactKeys(item, [
      "opaqueFileName",
      "auditionClipId",
      "audioSha256",
      "disposition",
      "rationale"
    ]);
    const clip = clips[index];
    if (
      clip === undefined ||
      item.opaqueFileName !== clip.opaqueFileName ||
      item.auditionClipId !== clip.auditionClipId ||
      item.audioSha256 !== clip.audioSha256 ||
      typeof item.disposition !== "string" ||
      !["acceptable", "unacceptable", "needs_changes", "undecided"].includes(item.disposition) ||
      typeof item.rationale !== "string" ||
      item.rationale.trim() !== item.rationale ||
      item.rationale.length < 1 ||
      item.rationale.length > 4_000
    ) {
      throw new Error("A private expected decision did not match its exact indexed clip.");
    }
    return {
      opaqueFileName: clip.opaqueFileName,
      auditionClipId: clip.auditionClipId,
      audioSha256: clip.audioSha256,
      disposition: item.disposition as HumanListeningDisposition,
      rationale: item.rationale
    };
  });
}

function validateStateSentinel(
  value: unknown,
  contract: Phase3b1PrivateReplayContract,
  listeningIndex: ListeningIndex
): void {
  const record = requireRecord(value, "private replay-state sentinel");
  requireExactKeys(record, [
    "schemaVersion",
    "evidenceClassification",
    "stateId",
    "packageDirectoryName",
    "listeningIndexSha256",
    "projectId",
    "clips"
  ]);
  if (
    record.schemaVersion !== 1 ||
    record.evidenceClassification !== "private_local_replay_state_binding" ||
    record.stateId !== contract.stateId ||
    record.packageDirectoryName !== contract.packageDirectoryName ||
    record.listeningIndexSha256 !== contract.listeningIndexSha256 ||
    typeof record.projectId !== "string" ||
    !isSafeId(record.projectId) ||
    record.projectId !== listeningIndex.projectId ||
    !Array.isArray(record.clips) ||
    record.clips.length !== listeningIndex.clips.length
  ) {
    throw new Error("The retained replay-state sentinel did not match its package contract.");
  }
  record.clips.forEach((raw, clipIndex) => {
    const binding = requireRecord(raw, "private replay-state clip binding");
    requireExactKeys(binding, [
      "auditionClipId",
      "auditionClipFingerprint",
      "audioArtifactId",
      "audioSha256"
    ]);
    const clip = listeningIndex.clips[clipIndex];
    if (
      clip === undefined ||
      binding.auditionClipId !== clip.auditionClipId ||
      binding.auditionClipFingerprint !== clip.auditionClipFingerprint ||
      binding.audioArtifactId !== clip.audioArtifactId ||
      binding.audioSha256 !== clip.audioSha256
    ) {
      throw new Error("A retained replay-state clip binding did not match the listening index.");
    }
  });
}

async function listCanonicalTree(root: string): Promise<readonly string[]> {
  const result: string[] = [];
  const pending = await readdir(root);
  while (pending.length > 0) {
    const relative = pending.pop();
    if (relative === undefined) break;
    const candidate = path.join(root, relative);
    const metadata = await lstat(candidate);
    if (metadata.isSymbolicLink()) {
      throw new Error("The retained replay state contained a symbolic link.");
    }
    result.push(relative);
    if (metadata.isDirectory()) {
      for (const child of await readdir(candidate)) {
        pending.push(path.join(relative, child));
      }
    } else if (!metadata.isFile()) {
      throw new Error("The retained replay state contained an unsupported entry.");
    }
  }
  if (result.length === 0) throw new Error("The retained replay state was empty.");
  return result;
}

async function readBoundedFile(filePath: string, maximumBytes: number): Promise<Buffer> {
  const metadata = await lstat(filePath);
  if (
    !metadata.isFile() ||
    metadata.isSymbolicLink() ||
    metadata.size < 1 ||
    metadata.size > maximumBytes
  ) {
    throw new Error("A private replay input file was invalid.");
  }
  return readFile(filePath);
}

async function requireCanonicalDirectory(directory: string): Promise<void> {
  const [metadata, canonical] = await Promise.all([lstat(directory), realpath(directory)]);
  if (!metadata.isDirectory() || metadata.isSymbolicLink() || !samePath(directory, canonical)) {
    throw new Error("A retained replay directory was not canonical.");
  }
}

async function sha256File(filePath: string): Promise<string> {
  const hash = createHash("sha256");
  for await (const chunk of createReadStream(filePath)) hash.update(chunk as Buffer);
  return hash.digest("hex");
}

function sha256(value: Buffer | Uint8Array): string {
  return createHash("sha256").update(value).digest("hex");
}

function retainedEnvironment(stateDirectory: string): Record<string, string> {
  const environment: Record<string, string> = {};
  for (const [name, value] of Object.entries(process.env)) {
    if (value !== undefined) environment[name] = value;
  }
  environment.APPDATA = path.join(stateDirectory, "AppData");
  environment.LOCALAPPDATA = path.join(stateDirectory, "LocalAppData");
  environment.TEMP = path.join(stateDirectory, "Temp");
  environment.TMP = environment.TEMP;
  for (const name of phase3b1PrivateReplaySanitizedEnvironmentNames) {
    delete environment[name];
  }
  return environment;
}

function requiredPackageDirectory(): string {
  const value = process.env[packageEnvironment];
  if (
    value === undefined ||
    value.trim().length === 0 ||
    value.length > 2_048 ||
    value.includes("\0") ||
    !path.isAbsolute(value)
  ) {
    throw new Error(`${packageEnvironment} must be an explicit bounded absolute path.`);
  }
  if (process.env[recordEnvironment] !== undefined && process.env[recordEnvironment] !== "1") {
    throw new Error(`${recordEnvironment}, when supplied, must be exactly 1.`);
  }
  return path.resolve(value);
}

function requiredAbsoluteEnvironment(name: string): string {
  const value = process.env[name];
  if (
    value === undefined ||
    value.trim().length === 0 ||
    value.length > 2_048 ||
    value.includes("\0") ||
    !path.isAbsolute(value)
  ) {
    throw new Error(`${name} must be a bounded absolute host path.`);
  }
  return path.resolve(value);
}

function privateDecisionEvidence(decision: AuditionReviewDecision) {
  const attestation = decision.listeningAttestation;
  if (attestation === null || attestation === undefined) {
    throw new Error("A persisted private decision lost its listening attestation.");
  }
  return {
    decisionId: decision.decisionId,
    reviewId: decision.reviewId,
    auditionClipId: attestation.auditionClipId,
    audioArtifactSha256: attestation.audioArtifactSha256,
    decision: decision.decision,
    disposition: attestation.disposition,
    actor: { ...decision.actor },
    decidedAt: decision.decidedAt,
    attestationId: attestation.attestationId,
    attestationFingerprint: attestation.attestationFingerprint,
    recordedAt: attestation.recordedAt,
    immutable: decision.immutable && attestation.immutable
  };
}

function processEvidence(processValue: OwnedProcess) {
  return {
    pid: processValue.pid,
    parentPid: processValue.parentPid,
    kind: processValue.kind === "app" ? "electron" : processValue.kind,
    executableName: processValue.name,
    creationIdentity: processValue.creationDate,
    exactExecutablePathConfirmed: processValue.executablePath !== null,
    goneAfterShutdown: true
  };
}

function allOwnedProcessEvidence(ownership: LaunchOwnership) {
  return [
    ...ownership.processes.map(processEvidence),
    ...ownership.confirmedExitedTransientProcesses.map((processValue) => ({
      pid: processValue.pid,
      parentPid: processValue.parentPid,
      kind: "electron" as const,
      executableName: processValue.name,
      creationIdentity: processValue.creationDate,
      exactExecutablePathConfirmed: false,
      executablePathStatus: processValue.pathStatus,
      verifiedParentCreationIdentity: processValue.verifiedParentCreationDate,
      absenceObservations: processValue.absenceObservations,
      goneAfterShutdown: true
    }))
  ].sort((left, right) => left.pid - right.pid);
}

function sortedIds(clips: readonly AuditionClip[]): readonly string[] {
  return clips.map((clip) => clip.auditionClipId).sort((left, right) => left.localeCompare(right));
}

function sameProcessIdentity(left: ProcessIdentity, right: ProcessIdentity): boolean {
  return (
    left.pid === right.pid &&
    left.name === right.name &&
    left.creationDate === right.creationDate &&
    (left.executablePath === null ||
      right.executablePath === null ||
      samePath(left.executablePath, right.executablePath))
  );
}

function samePath(left: string, right: string): boolean {
  return path.win32.resolve(left).toLowerCase() === path.win32.resolve(right).toLowerCase();
}

function isStrictChild(parent: string, child: string): boolean {
  const relative = path.relative(path.resolve(parent), path.resolve(child));
  return (
    relative.length > 0 &&
    relative !== ".." &&
    !relative.startsWith(`..${path.sep}`) &&
    !path.isAbsolute(relative)
  );
}

function requireRecord(value: unknown, label: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`The ${label} was invalid.`);
  }
  return value as Record<string, unknown>;
}

function requireExactKeys(value: Record<string, unknown>, expected: readonly string[]): void {
  const actual = Object.keys(value).sort();
  const sortedExpected = [...expected].sort();
  if (
    actual.length !== sortedExpected.length ||
    actual.some((key, index) => key !== sortedExpected[index])
  ) {
    throw new Error("A private replay evidence record contained unexpected fields.");
  }
}

function isSafeId(value: string): boolean {
  return /^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,199}$/u.test(value);
}

function isTimestamp(value: string): boolean {
  return /^\d{4}-\d{2}-\d{2}T/u.test(value) && Number.isFinite(Date.parse(value));
}

function requiredPid(value: number | undefined, label: string): number {
  if (value === undefined || !Number.isSafeInteger(value) || value <= 0) {
    throw new Error(`The ${label} PID was invalid.`);
  }
  return value;
}

async function withDeadline<T>(
  operation: Promise<T>,
  timeoutMs: number,
  message: string
): Promise<T> {
  let timeout: NodeJS.Timeout | undefined;
  try {
    return await Promise.race([
      operation,
      new Promise<never>((_resolve, reject) => {
        timeout = setTimeout(() => reject(new Error(message)), timeoutMs);
      })
    ]);
  } finally {
    if (timeout !== undefined) clearTimeout(timeout);
  }
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}
