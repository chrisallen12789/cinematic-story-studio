import path from "node:path";

import {
  ProcessInventoryError,
  remainingOwnedProcesses,
  runBoundedProcess,
  type OwnedProcess,
  type ProcessCommandRunner,
  type ProcessIdentity
} from "./packaged-process-inventory";

const maximumObservedProcesses = 32;
const maximumOutputBytes = 16 * 1024;
const observationTimeoutMs = 15_000;
const observationInputEnvironmentVariable =
  "CSS_OWNED_PROCESS_OBSERVATION_INPUT";
const maximumObservationAttempts = 3;
const observationRetryBackoffMs = Object.freeze([250, 750]);
const maximumStableLedgerAttempts = 3;

export interface OwnedProcessNetworkObservation {
  readonly method: "owned_pid_tcp_endpoint_inventory";
  readonly ownedPidsOnly: true;
  readonly observedNonLoopbackEndpointCount: number;
}

export interface OwnedProcessNetworkObservationDependencies {
  readonly run?: ProcessCommandRunner;
  readonly delay?: (milliseconds: number) => Promise<void>;
  readonly platform?: NodeJS.Platform;
}

export interface StableOwnedProcessNetworkObservation {
  readonly ownedProcesses: readonly OwnedProcess[];
  readonly observation: OwnedProcessNetworkObservation;
}

export interface StableOwnedProcessNetworkObservationDependencies {
  readonly observe?: typeof observeLiveOwnedProcessNetworkEndpoints;
}

export async function queryExactProcessIdentities(
  pids: readonly number[],
  dependencies: OwnedProcessNetworkObservationDependencies = {}
): Promise<readonly ProcessIdentity[]> {
  const expectedPids = validatePids(pids);
  if ((dependencies.platform ?? process.platform) !== "win32") {
    throw new Error("Exact-PID process observation requires Windows.");
  }
  const output = await runObservationCommand({
    command: "powershell.exe",
    arguments: [
      "-NoLogo",
      "-NoProfile",
      "-NonInteractive",
      "-Command",
      buildExactPidQueryScript(expectedPids)
    ],
    timeoutMs: observationTimeoutMs,
    maximumOutputBytes
  }, dependencies);
  const identities = parseExactProcessIdentityOutput(output);
  if (identities.some((item) => !expectedPids.includes(item.pid))) {
    throw new Error("The exact-PID process observation returned an unrelated PID.");
  }
  return identities;
}

function parseExactProcessIdentityOutput(
  output: string
): readonly ProcessIdentity[] {
  let raw: unknown;
  try {
    raw = JSON.parse(output);
  } catch {
    throw new Error("The exact-PID process observation was malformed.");
  }
  if (!Array.isArray(raw) || raw.length > maximumObservedProcesses) {
    throw new Error("The exact-PID process observation was malformed.");
  }
  const pids = new Set<number>();
  const result = raw.map((item): ProcessIdentity => {
    if (item === null || typeof item !== "object" || Array.isArray(item)) {
      throw new Error("The exact-PID process observation was malformed.");
    }
    const value = item as Record<string, unknown>;
    if (
      Object.keys(value).length !== 5 ||
      !Number.isSafeInteger(value.pid) ||
      (value.pid as number) <= 0 ||
      !Number.isSafeInteger(value.parentPid) ||
      (value.parentPid as number) < 0 ||
      typeof value.name !== "string" ||
      !/^[A-Za-z0-9][A-Za-z0-9 ._()@+-]{0,259}$/u.test(value.name) ||
      (value.executablePath !== null &&
        (typeof value.executablePath !== "string" ||
          value.executablePath.length > 2_048 ||
          !path.win32.isAbsolute(value.executablePath))) ||
      typeof value.creationDate !== "string" ||
      !isInvariantUtcTimestamp(value.creationDate) ||
      pids.has(value.pid as number)
    ) {
      throw new Error("The exact-PID process observation was malformed.");
    }
    pids.add(value.pid as number);
    return {
      pid: value.pid as number,
      parentPid: value.parentPid as number,
      name: value.name,
      executablePath: value.executablePath,
      creationDate: value.creationDate
    };
  });
  return result.sort((left, right) => left.pid - right.pid);
}

function isInvariantUtcTimestamp(value: string): boolean {
  const match =
    /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\.(\d{7})Z$/u.exec(value);
  if (match === null) return false;
  const milliseconds = match[2]?.slice(0, 3);
  const prefix = match[1];
  if (milliseconds === undefined || prefix === undefined) return false;
  const parsed = new Date(`${prefix}.${milliseconds}Z`);
  return (
    !Number.isNaN(parsed.valueOf()) &&
    parsed.toISOString() === `${prefix}.${milliseconds}Z`
  );
}

interface ObservationOutput {
  readonly observedPids: readonly number[];
  readonly observedNonLoopbackEndpointCount: number;
}

const observationFailureCodes = new Set([
  "OBSERVATION_CMDLET_UNAVAILABLE",
  "OBSERVATION_HELPER_FAILED",
  "OBSERVATION_IDENTITY_CHANGED",
  "OBSERVATION_IDENTITY_UNAVAILABLE",
  "OBSERVATION_INPUT_INVALID"
]);
const observationFailureStages = new Set([
  "bootstrap",
  "endpoint_query",
  "identity_compare",
  "identity_iterate",
  "identity_query",
  "identity_requery",
  "network_command_presence",
  "network_command_type",
  "network_definition_resolve",
  "network_definition_validate",
  "network_export_lookup",
  "network_manifest_resolve",
  "network_module_import"
]);
const observationFailureTypes = new Set([
  "InvalidOperationException",
  "MethodInvocationException",
  "PropertyNotFoundException",
  "RuntimeException",
  "UnknownException"
]);
const observationObjectTypes = new Set([
  "FunctionInfo",
  "Null",
  "ObjectArray",
  "String",
  "UnknownObject"
]);

/**
 * Observe TCP endpoints for exact, already-owned process identities only.
 *
 * The PowerShell helper receives no process name wildcard and never enumerates
 * the machine process table. Each process identity is revalidated by exact PID,
 * executable path, and creation time immediately before its exact-PID TCP
 * query. A missing, reused, or changed identity fails closed.
 */
export async function observeOwnedProcessNetworkEndpoints(
  ownedProcesses: readonly OwnedProcess[],
  dependencies: OwnedProcessNetworkObservationDependencies = {}
): Promise<OwnedProcessNetworkObservation> {
  const expected = validateExpectedProcesses(ownedProcesses);
  if ((dependencies.platform ?? process.platform) !== "win32") {
    throw new Error("Owned-process network observation requires Windows.");
  }
  const encodedExpected = Buffer.from(
    JSON.stringify(
      expected.map((item) => ({
        pid: item.pid,
        name: item.name,
        executablePath: item.executablePath,
        creationDate: item.creationDate
      }))
    ),
    "utf8"
  ).toString("base64");
  const output = await runObservationCommand({
    command: "powershell.exe",
    arguments: [
      "-NoLogo",
      "-NoProfile",
      "-NonInteractive",
      "-Command",
      buildObservationScript()
    ],
    environment: {
      ...process.env,
      [observationInputEnvironmentVariable]: encodedExpected
    },
    timeoutMs: observationTimeoutMs,
    maximumOutputBytes
  }, dependencies);
  const parsed = parseObservationOutput(output);
  const expectedPids = expected.map((item) => item.pid);
  if (
    parsed.observedPids.length !== expectedPids.length ||
    parsed.observedPids.some((pid, index) => pid !== expectedPids[index])
  ) {
    throw new Error(
      "The owned-process endpoint observation did not cover the exact requested PIDs."
    );
  }
  return {
    method: "owned_pid_tcp_endpoint_inventory",
    ownedPidsOnly: true,
    observedNonLoopbackEndpointCount:
      parsed.observedNonLoopbackEndpointCount
  };
}

/**
 * Narrow an append-only ownership ledger to the exact identities that are
 * still live before performing endpoint observation.
 *
 * Historical helpers that have already exited remain in the caller's ledger
 * for shutdown proof. Their absence is established by exact PID only; a reused
 * or changed identity still fails closed. Every live owned identity is passed
 * to the strict observer, and the authenticated runtime anchors must remain
 * live throughout the observation.
 */
export async function observeLiveOwnedProcessNetworkEndpoints(
  ownedProcesses: readonly OwnedProcess[],
  requiredLivePids: readonly number[],
  dependencies: OwnedProcessNetworkObservationDependencies = {}
): Promise<OwnedProcessNetworkObservation> {
  const historical = validateExpectedProcesses(ownedProcesses);
  const required = validatePids(requiredLivePids);
  if (
    required.some(
      (pid) => !historical.some((processIdentity) => processIdentity.pid === pid)
    )
  ) {
    throw new Error(
      "A required runtime identity was outside the owned-process ledger."
    );
  }
  const current = await queryExactProcessIdentities(
    historical.map((item) => item.pid),
    dependencies
  );
  const live = remainingOwnedProcesses(current, historical);
  if (required.some((pid) => !live.some((item) => item.pid === pid))) {
    throw new Error(
      "An authenticated service or provider-worker identity was not live."
    );
  }
  return observeOwnedProcessNetworkEndpoints(live, dependencies);
}

/**
 * Require the append-only ownership ledger to remain stable across endpoint
 * observation. A newly adopted descendant invalidates the prior observation,
 * expands the ledger, and causes every still-live owned Python PID to be
 * observed again. Bounded churn fails closed.
 */
export async function observeStableOwnedProcessNetworkEndpoints(
  ownedProcesses: readonly OwnedProcess[],
  requiredLivePids: readonly number[],
  refreshOwnedProcesses: (
    current: readonly OwnedProcess[]
  ) => Promise<readonly OwnedProcess[]>,
  dependencies: StableOwnedProcessNetworkObservationDependencies = {}
): Promise<StableOwnedProcessNetworkObservation> {
  let ledger = validateOwnedLedger(ownedProcesses);
  const required = validatePids(requiredLivePids);
  const pendingRequiredPids = new Set<number>();
  const observe =
    dependencies.observe ?? observeLiveOwnedProcessNetworkEndpoints;
  let maximumNonLoopbackEndpointCount = 0;
  for (
    let attempt = 0;
    attempt < maximumStableLedgerAttempts;
    attempt += 1
  ) {
    const before = validateRefreshedOwnedLedger(
      ledger,
      await refreshOwnedProcesses(ledger)
    );
    for (const item of newlyAddedOwnedProcesses(ledger, before)) {
      if (item.kind === "service" || item.kind === "provider_worker") {
        pendingRequiredPids.add(item.pid);
      }
    }
    const ownedPythonProcesses = before.filter(
      (item) => item.kind === "service" || item.kind === "provider_worker"
    );
    const requiredForAttempt = [
      ...new Set([...required, ...pendingRequiredPids])
    ].sort((left, right) => left - right);
    const observation = await observe(
      ownedPythonProcesses,
      requiredForAttempt
    );
    pendingRequiredPids.clear();
    maximumNonLoopbackEndpointCount = Math.max(
      maximumNonLoopbackEndpointCount,
      observation.observedNonLoopbackEndpointCount
    );
    const after = validateRefreshedOwnedLedger(
      before,
      await refreshOwnedProcesses(before)
    );
    for (const item of newlyAddedOwnedProcesses(before, after)) {
      if (item.kind === "service" || item.kind === "provider_worker") {
        pendingRequiredPids.add(item.pid);
      }
    }
    if (sameOwnedLedger(before, after)) {
      return {
        ownedProcesses: after,
        observation: {
          ...observation,
          observedNonLoopbackEndpointCount:
            maximumNonLoopbackEndpointCount
        }
      };
    }
    ledger = after;
  }
  throw new Error(
    "The owned-process ledger did not stabilize across endpoint observation."
  );
}

function validateOwnedLedger(
  values: readonly OwnedProcess[]
): readonly OwnedProcess[] {
  if (values.length === 0 || values.length > 256) {
    throw new Error("The owned-process ledger was out of bounds.");
  }
  const sorted = [...values].sort((left, right) => left.pid - right.pid);
  if (new Set(sorted.map((item) => item.pid)).size !== sorted.length) {
    throw new Error("The owned-process ledger contained duplicate PIDs.");
  }
  return sorted;
}

function validateRefreshedOwnedLedger(
  prior: readonly OwnedProcess[],
  refreshed: readonly OwnedProcess[]
): readonly OwnedProcess[] {
  const validated = validateOwnedLedger(refreshed);
  if (
    prior.some(
      (expected) =>
        !validated.some((candidate) => sameOwnedLedgerIdentity(candidate, expected))
    )
  ) {
    throw new Error(
      "The append-only owned-process ledger lost or changed an established identity."
    );
  }
  return validated;
}

function sameOwnedLedger(
  left: readonly OwnedProcess[],
  right: readonly OwnedProcess[]
): boolean {
  return (
    left.length === right.length &&
    left.every((item, index) =>
      sameOwnedLedgerIdentity(item, right[index])
    )
  );
}

function newlyAddedOwnedProcesses(
  prior: readonly OwnedProcess[],
  refreshed: readonly OwnedProcess[]
): readonly OwnedProcess[] {
  return refreshed.filter(
    (candidate) =>
      !prior.some((expected) => sameOwnedLedgerIdentity(candidate, expected))
  );
}

function sameOwnedLedgerIdentity(
  left: OwnedProcess,
  right: OwnedProcess | undefined
): boolean {
  return (
    right !== undefined &&
    left.pid === right.pid &&
    left.parentPid === right.parentPid &&
    left.name === right.name &&
    left.executablePath === right.executablePath &&
    left.creationDate === right.creationDate &&
    left.kind === right.kind
  );
}

function validateExpectedProcesses(
  values: readonly OwnedProcess[]
): readonly OwnedProcess[] {
  if (values.length === 0 || values.length > maximumObservedProcesses) {
    throw new Error("The owned-process endpoint observation was out of bounds.");
  }
  const sorted = [...values].sort((left, right) => left.pid - right.pid);
  const pids = new Set<number>();
  for (const value of sorted) {
    if (
      !Number.isSafeInteger(value.pid) ||
      value.pid <= 0 ||
      !Number.isSafeInteger(value.parentPid) ||
      value.parentPid <= 0 ||
      value.executablePath === null ||
      value.executablePath.length === 0 ||
      value.executablePath.length > 2_048 ||
      value.executablePath.includes("\0") ||
      value.name.length === 0 ||
      value.name.length > 260 ||
      value.name.includes("\0") ||
      !Number.isFinite(Date.parse(value.creationDate)) ||
      pids.has(value.pid)
    ) {
      throw new Error(
        "An owned-process identity was invalid for endpoint observation."
      );
    }
    pids.add(value.pid);
  }
  return sorted;
}

function validatePids(values: readonly number[]): readonly number[] {
  if (
    values.length === 0 ||
    values.length > maximumObservedProcesses ||
    values.some((value) => !Number.isSafeInteger(value) || value <= 0) ||
    new Set(values).size !== values.length
  ) {
    throw new Error("The exact-PID process observation was invalid.");
  }
  return [...values].sort((left, right) => left - right);
}

async function runObservationCommand(
  request: Parameters<ProcessCommandRunner>[0],
  dependencies: OwnedProcessNetworkObservationDependencies
): Promise<string> {
  const run = dependencies.run ?? runBoundedProcess;
  const delay = dependencies.delay ?? boundedDelay;
  for (let attempt = 0; attempt < maximumObservationAttempts; attempt += 1) {
    try {
      return await run(request);
    } catch (error) {
      if (
        !(error instanceof ProcessInventoryError) ||
        !error.retryable ||
        attempt + 1 >= maximumObservationAttempts
      ) {
        throw error;
      }
      await delay(observationRetryBackoffMs[attempt] ?? 0);
    }
  }
  throw new ProcessInventoryError(
    "PROCESS_INVENTORY_COMMAND_FAILED",
    false
  );
}

function boundedDelay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, milliseconds);
  });
}

function buildExactPidQueryScript(pids: readonly number[]): string {
  return [
    "$ErrorActionPreference = 'Stop'",
    "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)",
    `$requestedPids = @(${pids.join(",")})`,
    "$records = foreach ($requestedPid in $requestedPids) {",
    "  foreach ($process in @(Get-CimInstance -ClassName Win32_Process -Filter (\"ProcessId = {0}\" -f $requestedPid) -Property ProcessId,ParentProcessId,Name,ExecutablePath,CreationDate)) {",
    "    [PSCustomObject]@{",
    "      pid = [int]$process.ProcessId",
    "      parentPid = [int]$process.ParentProcessId",
    "      name = [string]$process.Name",
    "      executablePath = if ($null -eq $process.ExecutablePath) { $null } else { [string]$process.ExecutablePath }",
    "      creationDate = $process.CreationDate.ToUniversalTime().ToString('O', [Globalization.CultureInfo]::InvariantCulture)",
    "    }",
    "  }",
    "}",
    "[Console]::Out.Write((ConvertTo-Json -InputObject @($records) -Compress))"
  ].join("\n");
}

function buildObservationScript(): string {
  return [
    "$ErrorActionPreference = 'Stop'",
    "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)",
    "function Test-OwnedIdentity {",
    "  param($Actual, $Expected, [int]$OwnedPid)",
    "  if ($null -eq $Actual -or $null -eq $Actual.ExecutablePath) { return $false }",
    "  $creation = $Actual.CreationDate.ToUniversalTime().ToString('O', [Globalization.CultureInfo]::InvariantCulture)",
    "  return [int]$Actual.ProcessId -eq $OwnedPid -and [String]::Equals([string]$Actual.Name, [string]$Expected.name, [StringComparison]::OrdinalIgnoreCase) -and [String]::Equals([IO.Path]::GetFullPath([string]$Actual.ExecutablePath), [IO.Path]::GetFullPath([string]$Expected.executablePath), [StringComparison]::OrdinalIgnoreCase) -and [String]::Equals($creation, [string]$Expected.creationDate, [StringComparison]::Ordinal)",
    "}",
    "$failureStage = 'bootstrap'",
    "$currentOwnedPid = 0",
    "$commandObjectType = 'Null'",
    "try {",
    `$encodedExpected = [Environment]::GetEnvironmentVariable('${observationInputEnvironmentVariable}', 'Process')`,
    `if ([String]::IsNullOrEmpty($encodedExpected) -or $encodedExpected.Length -gt ${maximumOutputBytes * 4} -or $encodedExpected -notmatch '^[A-Za-z0-9+/]+={0,2}$') { throw 'OBSERVATION_INPUT_INVALID' }`,
    `[Environment]::SetEnvironmentVariable('${observationInputEnvironmentVariable}', $null, 'Process')`,
    "$expectedJson = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($encodedExpected))",
    "$decodedExpected = ConvertFrom-Json -InputObject $expectedJson",
    "$expected = @()",
    "foreach ($decodedIdentity in $decodedExpected) { $expected += $decodedIdentity }",
    `if ($expected.Count -lt 1 -or $expected.Count -gt ${maximumObservedProcesses}) { throw 'OBSERVATION_INPUT_INVALID' }`,
    "$observedPids = [Collections.Generic.List[int]]::new()",
    "$nonLoopbackCount = 0",
    "$failureStage = 'network_manifest_resolve'",
    "$netTcpIpManifest = [IO.Path]::GetFullPath((Join-Path $PSHOME 'Modules\\NetTCPIP\\NetTCPIP.psd1'))",
    "$manifestItem = Get-Item -LiteralPath $netTcpIpManifest -Force -ErrorAction Stop",
    "if ($manifestItem.PSIsContainer -or ($manifestItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'OBSERVATION_CMDLET_UNAVAILABLE' }",
    "$failureStage = 'network_module_import'",
    "$networkModules = @(Import-Module -Name $netTcpIpManifest -Force -PassThru -ErrorAction Stop)",
    "$failureStage = 'network_definition_resolve'",
    "$netTcpIpCommandDefinition = [IO.Path]::GetFullPath((Join-Path $manifestItem.DirectoryName 'MSFT_NetTCPConnection.cdxml'))",
    "$commandDefinitionItem = Get-Item -LiteralPath $netTcpIpCommandDefinition -Force -ErrorAction Stop",
    "$failureStage = 'network_export_lookup'",
    "$networkCommand = if ($networkModules.Count -eq 1) { $networkModules[0].ExportedCommands['Get-NetTCPConnection'] } else { $null }",
    "$commandObjectTypeCandidate = if ($null -eq $networkCommand) { 'Null' } else { [string]$networkCommand.GetType().Name }",
    "$commandObjectType = if ($commandObjectTypeCandidate -in @('FunctionInfo', 'ObjectArray', 'String')) { $commandObjectTypeCandidate } else { 'UnknownObject' }",
    "$failureStage = 'network_definition_validate'",
    "if ($commandDefinitionItem.PSIsContainer -or ($commandDefinitionItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'OBSERVATION_CMDLET_UNAVAILABLE' }",
    "$failureStage = 'network_command_presence'",
    "if ($null -eq $networkCommand) { throw 'OBSERVATION_CMDLET_UNAVAILABLE' }",
    "$failureStage = 'network_command_type'",
    "if ($commandObjectType -cne 'FunctionInfo') { throw 'OBSERVATION_CMDLET_UNAVAILABLE' }",
    "$failureStage = 'identity_iterate'",
    "foreach ($identity in $expected) {",
    "  $ownedPid = [int]$identity.pid",
    "  $currentOwnedPid = $ownedPid",
    "  $failureStage = 'identity_query'",
    "  $process = @(Get-CimInstance -ClassName Win32_Process -Filter (\"ProcessId = {0}\" -f $ownedPid) -Property ProcessId,Name,ExecutablePath,CreationDate)",
    "  if ($process.Count -ne 1) { throw 'OBSERVATION_IDENTITY_UNAVAILABLE' }",
    "  $actual = $process[0]",
    "  $failureStage = 'identity_compare'",
    "  if (-not (Test-OwnedIdentity $actual $identity $ownedPid)) { throw 'OBSERVATION_IDENTITY_CHANGED' }",
    "  $failureStage = 'endpoint_query'",
    "  $connections = @()",
    "  try {",
    "    $connections = @(& $networkCommand -OwningProcess $ownedPid -ErrorAction Stop)",
    "  } catch {",
    "    $noEndpointError = 'CmdletizationQuery_NotFound_OwningProcess,Get-NetTCPConnection'",
    "    if (-not [String]::Equals([string]$_.FullyQualifiedErrorId, $noEndpointError, [StringComparison]::Ordinal)) { throw }",
    "  }",
    "  foreach ($connection in $connections) {",
    "    $state = [string]$connection.State",
    "    $localAddress = [string]$connection.LocalAddress",
    "    $remoteAddress = [string]$connection.RemoteAddress",
    "    $isExternal = if ($state -eq 'Listen') { $localAddress -notin @('127.0.0.1', '::1') } else { $remoteAddress -notin @('', '0.0.0.0', '::', '127.0.0.1', '::1') }",
    "    if ($isExternal) { $nonLoopbackCount += 1 }",
    "  }",
    "  $failureStage = 'identity_requery'",
    "  $processAfter = @(Get-CimInstance -ClassName Win32_Process -Filter (\"ProcessId = {0}\" -f $ownedPid) -Property ProcessId,Name,ExecutablePath,CreationDate)",
    "  if ($processAfter.Count -ne 1) { throw 'OBSERVATION_IDENTITY_UNAVAILABLE' }",
    "  $actualAfter = $processAfter[0]",
    "  if (-not (Test-OwnedIdentity $actualAfter $identity $ownedPid)) { throw 'OBSERVATION_IDENTITY_CHANGED' }",
    "  $observedPids.Add($ownedPid)",
    "}",
    "} catch {",
    "  $candidate = [string]$_.Exception.Message",
    "  $candidateType = [string]$_.Exception.GetType().Name",
    "  $allowed = @('OBSERVATION_CMDLET_UNAVAILABLE', 'OBSERVATION_IDENTITY_CHANGED', 'OBSERVATION_IDENTITY_UNAVAILABLE', 'OBSERVATION_INPUT_INVALID')",
    "  $allowedTypes = @('InvalidOperationException', 'MethodInvocationException', 'PropertyNotFoundException', 'RuntimeException')",
    "  $failureCode = if ($candidate -in $allowed) { $candidate } else { 'OBSERVATION_HELPER_FAILED' }",
    "  $failureType = if ($candidateType -in $allowedTypes) { $candidateType } else { 'UnknownException' }",
    "  $failure = [PSCustomObject]@{ commandObjectType = $commandObjectType; failureCode = $failureCode; failureStage = $failureStage; failureType = $failureType; ownedPid = $currentOwnedPid }",
    "  [Console]::Out.Write((ConvertTo-Json -InputObject $failure -Compress))",
    "  exit 0",
    "}",
    "$result = [PSCustomObject]@{ observedPids = @($observedPids | Sort-Object); observedNonLoopbackEndpointCount = $nonLoopbackCount }",
    "[Console]::Out.Write((ConvertTo-Json -InputObject $result -Compress))"
  ].join("\n");
}

function parseObservationOutput(output: string): ObservationOutput {
  let raw: unknown;
  try {
    raw = JSON.parse(output);
  } catch {
    throw new Error("The owned-process endpoint observation was malformed.");
  }
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) {
    throw new Error("The owned-process endpoint observation was malformed.");
  }
  const value = raw as Record<string, unknown>;
  if (
    Object.keys(value).length === 5 &&
    typeof value.commandObjectType === "string" &&
    observationObjectTypes.has(value.commandObjectType) &&
    typeof value.failureCode === "string" &&
    observationFailureCodes.has(value.failureCode) &&
    typeof value.failureStage === "string" &&
    observationFailureStages.has(value.failureStage) &&
    typeof value.failureType === "string" &&
    observationFailureTypes.has(value.failureType) &&
    Number.isSafeInteger(value.ownedPid) &&
    (value.ownedPid as number) >= 0
  ) {
    throw new Error(
      `The owned-process endpoint observation failed safely (${value.failureCode}, ${value.failureStage}, ${value.failureType}, ${value.commandObjectType}, owned PID ${String(value.ownedPid)}).`
    );
  }
  if (
    Object.keys(value).length !== 2 ||
    !("observedPids" in value) ||
    !("observedNonLoopbackEndpointCount" in value) ||
    !Array.isArray(value.observedPids) ||
    value.observedPids.length === 0 ||
    value.observedPids.length > maximumObservedProcesses ||
    value.observedPids.some(
      (pid) => !Number.isSafeInteger(pid) || (pid as number) <= 0
    ) ||
    new Set(value.observedPids).size !== value.observedPids.length ||
    !Number.isSafeInteger(value.observedNonLoopbackEndpointCount) ||
    (value.observedNonLoopbackEndpointCount as number) < 0 ||
    (value.observedNonLoopbackEndpointCount as number) > 65_536
  ) {
    throw new Error("The owned-process endpoint observation was malformed.");
  }
  return {
    observedPids: value.observedPids as number[],
    observedNonLoopbackEndpointCount:
      value.observedNonLoopbackEndpointCount as number
  };
}
