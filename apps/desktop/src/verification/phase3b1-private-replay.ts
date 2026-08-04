import path from "node:path";

export const phase3b1PrivateReplayDirectoryName = "CSS-P3B1";
export const phase3b1PrivateReplayContractFileName = "replay-contract.json";
export const phase3b1PrivateReplaySentinelFileName = "replay-state.json";
export const phase3b1PrivateReplaySchemaVersion = 1 as const;
export const phase3b1PrivateReplayMaximumPathLength = 240;
export const phase3b1PrivateReplaySanitizedEnvironmentNames = [
  "ELECTRON_RUN_AS_NODE",
  "NODE_OPTIONS",
  "NODE_PATH",
  "PYTHONHOME",
  "PYTHONPATH",
  "CSS_DESKTOP_DEV_URL",
  "CSS_E2E",
  "CSS_E2E_DATA_DIR",
  "CSS_PACKAGED_E2E_EXECUTABLE",
  "CSS_PACKAGED_E2E_SHUTDOWN_EVIDENCE_PATH",
  "CSS_PHASE3B_RUNTIME_SHUTDOWN_EVIDENCE",
  "CSS_PHASE3B1_REAL_MODEL_PACKAGE_ZIP",
  "CSS_PHASE3B1_PRIVATE_EVIDENCE_ROOT",
  "CSS_PHASE3B1_SOURCE_HEAD_SHA",
  "CSS_PHASE3B1_PRIVATE_REPLAY_PACKAGE",
  "CSS_PHASE3B1_RECORD_PRIVATE_DECISIONS",
  "CSS_PHASE3B1_PRIVATE_REPLAY_RUNNER",
  "CSS_PHASE3B1_PRIVATE_REPLAY_E2E",
  "CSS_PHASE3B1_REPLAY_E2E_REMOTE_DEBUGGING_PORT"
] as const;

const sha256Pattern = /^[a-f0-9]{64}$/u;
const replayStateIdPattern = /^[a-f0-9]{12}$/u;
const versionPattern = /^\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?$/u;

export interface Phase3b1PrivateReplayFileEvidence {
  readonly relativePath: string;
  readonly byteSize: number;
  readonly sha256: string;
}

export interface Phase3b1PrivateReplayContract {
  readonly schemaVersion: typeof phase3b1PrivateReplaySchemaVersion;
  readonly evidenceClassification: "private_local_replay_contract";
  readonly stateStorage: "local_application_data";
  readonly stateDirectoryName: typeof phase3b1PrivateReplayDirectoryName;
  readonly stateId: string;
  readonly packageDirectoryName: string;
  readonly listeningIndexSha256: string;
  readonly stateSentinelSha256: string;
  readonly packagedVersion: string;
  readonly executable: Phase3b1PrivateReplayFileEvidence;
  readonly applicationArchive: Phase3b1PrivateReplayFileEvidence;
  readonly service: Phase3b1PrivateReplayFileEvidence;
  readonly maximumRetainedPathLength: number;
  readonly enforcedMaximumPathLength: typeof phase3b1PrivateReplayMaximumPathLength;
}

export function resolvePhase3b1PrivateReplayStateDirectory(
  localApplicationData: string,
  stateId: string
): string {
  requireReplayStateId(stateId);
  if (!path.isAbsolute(localApplicationData)) {
    throw new Error("The local application-data directory must be absolute.");
  }
  return path.join(
    path.resolve(localApplicationData),
    phase3b1PrivateReplayDirectoryName,
    stateId
  );
}

export function requirePhase3b1PrivateReplayPathBudget(
  stateDirectory: string,
  relativePaths: readonly string[]
): number {
  if (!path.isAbsolute(stateDirectory) || relativePaths.length === 0) {
    throw new Error("The private replay path-budget inputs were invalid.");
  }
  let maximum = stateDirectory.length;
  for (const relativePath of relativePaths) {
    if (
      relativePath.length === 0 ||
      path.isAbsolute(relativePath) ||
      relativePath === ".." ||
      relativePath.startsWith(`..${path.sep}`)
    ) {
      throw new Error("A retained private replay path escaped its state root.");
    }
    const candidate = path.join(stateDirectory, relativePath);
    if (!isStrictChild(stateDirectory, candidate)) {
      throw new Error("A retained private replay path escaped its state root.");
    }
    maximum = Math.max(maximum, candidate.length);
  }
  if (maximum > phase3b1PrivateReplayMaximumPathLength) {
    throw new Error(
      `The retained private replay state requires a ${String(maximum)}-character path, exceeding the enforced ${String(phase3b1PrivateReplayMaximumPathLength)}-character budget.`
    );
  }
  return maximum;
}

export function validatePhase3b1PrivateReplayContract(
  value: unknown
): Phase3b1PrivateReplayContract {
  if (!isRecord(value)) {
    throw new Error("The private replay contract was invalid.");
  }
  const candidate = value as Partial<Phase3b1PrivateReplayContract>;
  if (typeof candidate.stateId !== "string") {
    throw new Error("The private replay contract was invalid.");
  }
  requireReplayStateId(candidate.stateId);
  if (
    candidate.schemaVersion !== phase3b1PrivateReplaySchemaVersion ||
    candidate.evidenceClassification !== "private_local_replay_contract" ||
    candidate.stateStorage !== "local_application_data" ||
    candidate.stateDirectoryName !== phase3b1PrivateReplayDirectoryName ||
    typeof candidate.packageDirectoryName !== "string" ||
    !/^run-[A-Za-z0-9.-]+-[a-f0-9]{12}$/u.test(
      candidate.packageDirectoryName
    ) ||
    typeof candidate.listeningIndexSha256 !== "string" ||
    !sha256Pattern.test(candidate.listeningIndexSha256) ||
    typeof candidate.stateSentinelSha256 !== "string" ||
    !sha256Pattern.test(candidate.stateSentinelSha256) ||
    typeof candidate.packagedVersion !== "string" ||
    !versionPattern.test(candidate.packagedVersion) ||
    candidate.enforcedMaximumPathLength !==
      phase3b1PrivateReplayMaximumPathLength ||
    !Number.isSafeInteger(candidate.maximumRetainedPathLength) ||
    (candidate.maximumRetainedPathLength ?? 0) <= 0 ||
    (candidate.maximumRetainedPathLength ?? Number.POSITIVE_INFINITY) >
      phase3b1PrivateReplayMaximumPathLength
  ) {
    throw new Error("The private replay contract was invalid.");
  }
  validateFileEvidence(
    candidate.executable,
    `apps/desktop/release/${candidate.packagedVersion}/win-unpacked/Cinematic Story Studio.exe`
  );
  validateFileEvidence(
    candidate.applicationArchive,
    `apps/desktop/release/${candidate.packagedVersion}/win-unpacked/resources/app.asar`
  );
  validateFileEvidence(
    candidate.service,
    `apps/desktop/release/${candidate.packagedVersion}/win-unpacked/resources/service/cinematic-story-service.exe`
  );
  return candidate as Phase3b1PrivateReplayContract;
}

export function buildPhase3b1PrivateReplayLauncher(
  contract: Phase3b1PrivateReplayContract,
  contractSha256: string
): string {
  validatePhase3b1PrivateReplayContract(contract);
  if (!sha256Pattern.test(contractSha256)) {
    throw new Error("The private replay contract hash was invalid.");
  }
  const stateId = contract.stateId;
  const version = contract.packagedVersion;
  const sanitizedEnvironmentNames =
    phase3b1PrivateReplaySanitizedEnvironmentNames
      .map((name) => `  "${name}"`)
      .join(",\n");
  return `$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$packageRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$contractPath = Join-Path $packageRoot "${phase3b1PrivateReplayContractFileName}"
$indexPath = Join-Path $packageRoot "listening-index.json"
if (-not (Test-Path -LiteralPath $contractPath -PathType Leaf)) { throw "The exact replay contract is unavailable." }
if (-not (Test-Path -LiteralPath $indexPath -PathType Leaf)) { throw "The exact listening index is unavailable." }
foreach ($packageFilePath in @($contractPath, $indexPath)) {
  $packageFile = Get-Item -LiteralPath $packageFilePath
  if (($packageFile.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "A private replay package file is not canonical." }
}
$contractHash = (Get-FileHash -LiteralPath $contractPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($contractHash -cne "${contractSha256}") { throw "The replay contract fingerprint changed." }
$contract = Get-Content -LiteralPath $contractPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ([int]$contract.schemaVersion -ne ${String(phase3b1PrivateReplaySchemaVersion)} -or [string]$contract.stateId -cne "${stateId}" -or [string]$contract.packagedVersion -cne "${version}") { throw "The replay contract identity is invalid." }
$packageDirectoryName = Split-Path -Leaf $packageRoot
if ([string]$contract.packageDirectoryName -cne $packageDirectoryName) { throw "The listening package identity changed." }
$indexHash = (Get-FileHash -LiteralPath $indexPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($indexHash -cne [string]$contract.listeningIndexSha256) { throw "The listening index fingerprint changed." }
$index = Get-Content -LiteralPath $indexPath -Raw -Encoding UTF8 | ConvertFrom-Json
$hostLocalAppData = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
if ([string]::IsNullOrWhiteSpace($hostLocalAppData)) { throw "The local application-data directory is unavailable." }
$stateRootCandidate = Join-Path (Join-Path $hostLocalAppData "${phase3b1PrivateReplayDirectoryName}") "${stateId}"
$stateRoot = (Resolve-Path -LiteralPath $stateRootCandidate).Path
$expectedStateRoot = [IO.Path]::GetFullPath($stateRootCandidate)
if (-not [string]::Equals($stateRoot, $expectedStateRoot, [StringComparison]::OrdinalIgnoreCase)) { throw "The retained replay state is not canonical." }
$stateParent = Get-Item -LiteralPath (Split-Path -Parent $stateRoot)
if (($stateParent.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "The retained replay-state parent is not canonical." }
$stateRootItem = Get-Item -LiteralPath $stateRoot
if (($stateRootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "The retained replay state is not canonical." }
$sentinelPath = Join-Path $stateRoot "${phase3b1PrivateReplaySentinelFileName}"
if (-not (Test-Path -LiteralPath $sentinelPath -PathType Leaf)) { throw "The retained replay-state binding is unavailable." }
$sentinelFile = Get-Item -LiteralPath $sentinelPath
if (($sentinelFile.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "The retained replay-state binding is not canonical." }
$sentinelHash = (Get-FileHash -LiteralPath $sentinelPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($sentinelHash -cne [string]$contract.stateSentinelSha256) { throw "The retained replay-state binding changed." }
$sentinel = Get-Content -LiteralPath $sentinelPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ([int]$sentinel.schemaVersion -ne 1 -or [string]$sentinel.stateId -cne "${stateId}" -or [string]$sentinel.packageDirectoryName -cne $packageDirectoryName -or [string]$sentinel.listeningIndexSha256 -cne $indexHash) { throw "The retained replay-state identity is invalid." }
if ([string]$sentinel.projectId -cne [string]$index.projectId -or @($sentinel.clips).Count -ne 6 -or @($index.clips).Count -ne 6) { throw "The retained replay project binding is invalid." }
for ($clipIndex = 0; $clipIndex -lt 6; $clipIndex += 1) {
  $stateClip = @($sentinel.clips)[$clipIndex]
  $indexClip = @($index.clips)[$clipIndex]
  if ([string]$stateClip.auditionClipId -cne [string]$indexClip.auditionClipId -or [string]$stateClip.auditionClipFingerprint -cne [string]$indexClip.auditionClipFingerprint -or [string]$stateClip.audioArtifactId -cne [string]$indexClip.audioArtifactId -or [string]$stateClip.audioSha256 -cne [string]$indexClip.audioSha256) { throw "A retained replay clip binding is invalid." }
}
foreach ($directoryName in @("AppData", "LocalAppData", "Temp")) {
  $directoryPath = Join-Path $stateRoot $directoryName
  if (-not (Test-Path -LiteralPath $directoryPath -PathType Container)) { throw "The retained replay state is incomplete." }
  $directory = Get-Item -LiteralPath $directoryPath
  if (($directory.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "The retained replay state is not canonical." }
}
$statePrefix = $stateRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
$pendingDirectories = [Collections.Generic.Stack[string]]::new()
$pendingDirectories.Push($stateRoot)
$retainedEntryCount = 0
$observedMaximumPathLength = $stateRoot.Length
while ($pendingDirectories.Count -gt 0) {
  $currentDirectory = $pendingDirectories.Pop()
  foreach ($retainedEntry in @(Get-ChildItem -LiteralPath $currentDirectory -Force)) {
    $retainedEntryCount += 1
    if ($retainedEntryCount -gt 100000) { throw "The retained replay state exceeded its bounded entry count." }
    if (($retainedEntry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "The retained replay state contained a link." }
    $retainedPath = [IO.Path]::GetFullPath([string]$retainedEntry.FullName)
    if (-not $retainedPath.StartsWith($statePrefix, [StringComparison]::OrdinalIgnoreCase)) { throw "A retained replay path escaped its state root." }
    $observedMaximumPathLength = [Math]::Max($observedMaximumPathLength, $retainedPath.Length)
    if ($observedMaximumPathLength -gt [int]$contract.enforcedMaximumPathLength) { throw "The retained replay state exceeded its enforced path budget." }
    if ($retainedEntry.PSIsContainer) { $pendingDirectories.Push($retainedPath) }
  }
}
[Console]::Out.WriteLine("CSS_REPLAY_MAXIMUM_RETAINED_PATH_LENGTH=$observedMaximumPathLength")
$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $packageRoot "..\\..\\..")).Path
$executable = Join-Path $repositoryRoot "apps\\desktop\\release\\${version}\\win-unpacked\\Cinematic Story Studio.exe"
$applicationArchive = Join-Path $repositoryRoot "apps\\desktop\\release\\${version}\\win-unpacked\\resources\\app.asar"
$service = Join-Path $repositoryRoot "apps\\desktop\\release\\${version}\\win-unpacked\\resources\\service\\cinematic-story-service.exe"
foreach ($entry in @(
  [PSCustomObject]@{ filePath = $executable; evidence = $contract.executable },
  [PSCustomObject]@{ filePath = $applicationArchive; evidence = $contract.applicationArchive },
  [PSCustomObject]@{ filePath = $service; evidence = $contract.service }
)) {
  $filePath = [string]$entry.filePath
  $evidence = $entry.evidence
  if (-not (Test-Path -LiteralPath $filePath -PathType Leaf)) { throw "An exact packaged application component is unavailable." }
  $file = Get-Item -LiteralPath $filePath
  if (($file.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "An exact packaged application component is not canonical." }
  $fileHash = (Get-FileHash -LiteralPath $filePath -Algorithm SHA256).Hash.ToLowerInvariant()
  if ([int64]$file.Length -ne [int64]$evidence.byteSize -or $fileHash -cne [string]$evidence.sha256) { throw "An exact packaged application component fingerprint changed." }
}
$relevantProcesses = @(
  Get-CimInstance Win32_Process -Filter "Name='Cinematic Story Studio.exe' OR Name='cinematic-story-service.exe'"
)
foreach ($relevantProcess in $relevantProcesses) {
  $observedName = [string]$relevantProcess.Name
  $observedPath = [string]$relevantProcess.ExecutablePath
  if ([string]::IsNullOrWhiteSpace($observedPath)) { throw "A relevant preexisting process identity could not be established; no process was changed." }
  $expectedPath = if ($observedName -ceq "Cinematic Story Studio.exe") { $executable } else { $service }
  if ([string]::Equals([IO.Path]::GetFullPath($observedPath), [IO.Path]::GetFullPath($expectedPath), [StringComparison]::OrdinalIgnoreCase)) { throw "An exact replay process is already running; no process was changed." }
  throw "Another relevant process is already running; no process was changed."
}
[Console]::Out.WriteLine("CSS_REPLAY_PREEXISTING_RELEVANT_PIDS=none")
$env:APPDATA = Join-Path $stateRoot "AppData"
$env:LOCALAPPDATA = Join-Path $stateRoot "LocalAppData"
$env:TEMP = Join-Path $stateRoot "Temp"
$env:TMP = $env:TEMP
${buildPhase3b1PrivateReplayInspectionArgumentsPowerShell()}
foreach ($environmentName in @(
${sanitizedEnvironmentNames}
)) {
  [Environment]::SetEnvironmentVariable($environmentName, $null, [EnvironmentVariableTarget]::Process)
}
$application = if ($applicationArguments.Count -eq 0) {
  Start-Process -FilePath $executable -WorkingDirectory (Split-Path -Parent $executable) -PassThru
} else {
  Start-Process -FilePath $executable -WorkingDirectory (Split-Path -Parent $executable) -ArgumentList $applicationArguments -PassThru
}
[Console]::Out.WriteLine("CSS_REPLAY_LAUNCHER_PID=$($application.Id)")
$application.WaitForExit()
exit $application.ExitCode
`;
}

export function buildPhase3b1PrivateReplayInspectionArgumentsPowerShell(): string {
  return `$applicationArguments = @()
$e2eMode = [Environment]::GetEnvironmentVariable("CSS_PHASE3B1_PRIVATE_REPLAY_E2E")
$e2eDebugPort = [Environment]::GetEnvironmentVariable("CSS_PHASE3B1_REPLAY_E2E_REMOTE_DEBUGGING_PORT")
$e2eRunner = [Environment]::GetEnvironmentVariable("CSS_PHASE3B1_PRIVATE_REPLAY_RUNNER")
if (-not [string]::IsNullOrWhiteSpace($e2eMode) -or -not [string]::IsNullOrWhiteSpace($e2eDebugPort) -or -not [string]::IsNullOrWhiteSpace($e2eRunner)) {
  $parsedDebugPort = 0
  if ($e2eRunner -cne "1" -or $e2eMode -cne "1" -or -not [int]::TryParse($e2eDebugPort, [ref]$parsedDebugPort) -or $parsedDebugPort -lt 49152 -or $parsedDebugPort -gt 65535) { throw "The private replay E2E inspection request was invalid." }
  $applicationArguments = @("--remote-debugging-address=127.0.0.1", "--remote-debugging-port=$parsedDebugPort")
}`;
}

function requireReplayStateId(value: string): void {
  if (!replayStateIdPattern.test(value)) {
    throw new Error("The private replay state identifier was invalid.");
  }
}

function validateFileEvidence(
  value: unknown,
  expectedRelativePath: string
): void {
  if (!isRecord(value)) {
    throw new Error("A private replay executable fingerprint was invalid.");
  }
  if (
    typeof value.relativePath !== "string" ||
    value.relativePath !== expectedRelativePath ||
    typeof value.byteSize !== "number" ||
    !Number.isSafeInteger(value.byteSize) ||
    value.byteSize <= 0 ||
    typeof value.sha256 !== "string" ||
    !sha256Pattern.test(value.sha256)
  ) {
    throw new Error("A private replay executable fingerprint was invalid.");
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isStrictChild(parent: string, child: string): boolean {
  const relative = path.relative(parent, child);
  return (
    relative.length > 0 &&
    relative !== ".." &&
    !relative.startsWith(`..${path.sep}`) &&
    !path.isAbsolute(relative)
  );
}
