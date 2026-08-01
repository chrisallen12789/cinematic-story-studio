import path from "node:path";

import {
  runBoundedProcess,
  type OwnedProcess,
  type ProcessCommandRunner,
  type ProcessIdentity
} from "./packaged-process-inventory";

const maximumObservedProcesses = 32;
const maximumOutputBytes = 16 * 1024;
const observationTimeoutMs = 15_000;

export interface OwnedProcessNetworkObservation {
  readonly method: "owned_pid_tcp_endpoint_inventory";
  readonly ownedPidsOnly: true;
  readonly observedNonLoopbackEndpointCount: number;
}

export interface OwnedProcessNetworkObservationDependencies {
  readonly run?: ProcessCommandRunner;
  readonly platform?: NodeJS.Platform;
}

export async function queryExactProcessIdentities(
  pids: readonly number[],
  dependencies: OwnedProcessNetworkObservationDependencies = {}
): Promise<readonly ProcessIdentity[]> {
  const expectedPids = validatePids(pids);
  if ((dependencies.platform ?? process.platform) !== "win32") {
    throw new Error("Exact-PID process observation requires Windows.");
  }
  const output = await (dependencies.run ?? runBoundedProcess)({
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
  });
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
  const output = await (dependencies.run ?? runBoundedProcess)({
    command: "powershell.exe",
    arguments: [
      "-NoLogo",
      "-NoProfile",
      "-NonInteractive",
      "-Command",
      buildObservationScript(encodedExpected)
    ],
    timeoutMs: observationTimeoutMs,
    maximumOutputBytes
  });
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

function buildObservationScript(encodedExpected: string): string {
  return [
    "$ErrorActionPreference = 'Stop'",
    "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)",
    `$encodedExpected = '${encodedExpected}'`,
    "$expectedJson = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($encodedExpected))",
    "$expected = @(ConvertFrom-Json -InputObject $expectedJson)",
    "$observedPids = [Collections.Generic.List[int]]::new()",
    "$nonLoopbackCount = 0",
    "if ($null -eq (Get-Command -Name Get-NetTCPConnection -CommandType Cmdlet -ErrorAction Stop)) { throw 'Get-NetTCPConnection is unavailable.' }",
    "foreach ($identity in $expected) {",
    "  $ownedPid = [int]$identity.pid",
    "  $process = @(Get-CimInstance -ClassName Win32_Process -Filter (\"ProcessId = {0}\" -f $ownedPid) -Property ProcessId,Name,ExecutablePath,CreationDate)",
    "  if ($process.Count -ne 1) { throw 'An exact owned process identity was unavailable.' }",
    "  $actual = $process[0]",
    "  $actualCreation = $actual.CreationDate.ToUniversalTime().ToString('O', [Globalization.CultureInfo]::InvariantCulture)",
    "  if ([int]$actual.ProcessId -ne $ownedPid -or -not [String]::Equals([string]$actual.Name, [string]$identity.name, [StringComparison]::OrdinalIgnoreCase) -or $null -eq $actual.ExecutablePath -or -not [String]::Equals([IO.Path]::GetFullPath([string]$actual.ExecutablePath), [IO.Path]::GetFullPath([string]$identity.executablePath), [StringComparison]::OrdinalIgnoreCase) -or -not [String]::Equals($actualCreation, [string]$identity.creationDate, [StringComparison]::Ordinal)) { throw 'An exact owned process identity changed before endpoint observation.' }",
    "  foreach ($connection in @(Get-NetTCPConnection -OwningProcess $ownedPid -ErrorAction SilentlyContinue)) {",
    "    $state = [string]$connection.State",
    "    $localAddress = [string]$connection.LocalAddress",
    "    $remoteAddress = [string]$connection.RemoteAddress",
    "    $isExternal = if ($state -eq 'Listen') { $localAddress -notin @('127.0.0.1', '::1') } else { $remoteAddress -notin @('', '0.0.0.0', '::', '127.0.0.1', '::1') }",
    "    if ($isExternal) { $nonLoopbackCount += 1 }",
    "  }",
    "  $observedPids.Add($ownedPid)",
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
