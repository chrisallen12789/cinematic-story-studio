import { randomBytes } from "node:crypto";
import {
  link,
  lstat,
  mkdir,
  realpath,
  rm,
  writeFile
} from "node:fs/promises";
import path from "node:path";

import type { RejectedLaunchBaselineObservation } from "./packaged-launch-rejection";

export type { RejectedLaunchBaselineObservation } from "./packaged-launch-rejection";

export const phase3b1PrivateFailureSidecarSchemaVersion = 1 as const;
export const maximumPhase3b1PrivateFailureSidecarBytes = 64 * 1024;

export const phase3b1PrivateFailureStages = Object.freeze([
  "prelaunch_inventory_3",
  "launch_3",
  "root_ownership_3",
  "readiness_3",
  "workflow_3",
  "shutdown_3",
  "prelaunch_inventory_4",
  "launch_4",
  "root_ownership_4",
  "readiness_4",
  "restore_4",
  "shutdown_4",
  "post_rejection_inventory_3",
  "post_rejection_inventory_4",
  "private_evidence_generation",
  "cleanup"
] as const);

export const phase3b1PrivateFailureCodes = Object.freeze([
  "launch_timeout",
  "launch_rejected",
  "inventory_failure",
  "other"
] as const);

export type Phase3b1PrivateFailureStage =
  (typeof phase3b1PrivateFailureStages)[number];
export type Phase3b1PrivateFailureCode =
  (typeof phase3b1PrivateFailureCodes)[number];

export type Phase3b1PrivateFailureJsonValue =
  | null
  | boolean
  | number
  | string
  | readonly Phase3b1PrivateFailureJsonValue[]
  | {
      readonly [key: string]: Phase3b1PrivateFailureJsonValue;
    };

export interface Phase3b1PrivateFailureClaims {
  readonly humanListeningClaimed: false;
  readonly naturalnessClaimed: false;
  readonly qualityClaimed: false;
  readonly consentClaimed: false;
  readonly commercialClearanceClaimed: false;
  readonly productionReadinessClaimed: false;
}

export interface Phase3b1PrivateFailureSidecar {
  readonly schemaVersion: typeof phase3b1PrivateFailureSidecarSchemaVersion;
  readonly result: "failed";
  readonly sourceHeadSha: string;
  readonly applicationVersion: string;
  readonly executableRelativePath: string;
  readonly launch: 3 | 4;
  readonly stage: Phase3b1PrivateFailureStage;
  readonly failureCode: Phase3b1PrivateFailureCode;
  readonly configuredLaunchTimeoutMs: number;
  readonly startedAt: string;
  readonly failedAt: string;
  readonly recordedAt: string;
  readonly syntheticGateCompleted: boolean;
  readonly ownershipEstablished: boolean;
  readonly ownedProcessExitClaimed: boolean;
  readonly cleanupCompleted: boolean;
  readonly rejectedLaunchBaselineObservation:
    | RejectedLaunchBaselineObservation
    | null;
  readonly claims: Phase3b1PrivateFailureClaims;
}

export interface WritePhase3b1PrivateFailureSidecarInput {
  /** Canonical repository-local `local-renders` directory. */
  readonly expectedLocalRendersParent: string;
  /** Prospective private run root; it must be a direct canonical child. */
  readonly privateRoot: string;
  readonly sourceHeadSha: string;
  readonly applicationVersion: string;
  readonly executableRelativePath: string;
  readonly launch: 3 | 4;
  readonly stage: Phase3b1PrivateFailureStage;
  readonly failureCode: Phase3b1PrivateFailureCode;
  readonly configuredLaunchTimeoutMs: number;
  readonly startedAt: string;
  readonly failedAt: string;
  readonly syntheticGateCompleted: boolean;
  readonly ownershipEstablished: boolean;
  readonly ownedProcessExitClaimed: boolean;
  readonly cleanupCompleted: boolean;
  /**
   * Kept structurally typed at this boundary so the launch-rejection observer
   * can evolve independently. The writer copies only bounded JSON data and
   * rejects error text and path-bearing values.
   */
  readonly rejectedLaunchBaselineObservation:
    | RejectedLaunchBaselineObservation
    | null;
}

export interface Phase3b1PrivateFailureSidecarWriteResult {
  readonly relativePath: string;
  readonly fileName: string;
  readonly byteSize: number;
  readonly value: Phase3b1PrivateFailureSidecar;
}

interface SidecarWriterOptions {
  readonly now?: () => Date;
  readonly tokenFactory?: () => string;
}

const SHA1 = /^[a-f0-9]{40}$/u;
const SAFE_VERSION = /^[A-Za-z0-9][A-Za-z0-9._+-]{0,99}$/u;
const SAFE_TOKEN = /^[a-f0-9]{32}$/u;
const ISO_TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/u;
const FORBIDDEN_OBSERVATION_KEYS = new Set([
  "error",
  "errormessage",
  "errorstack",
  "message",
  "rawerror",
  "stack"
]);
const MAXIMUM_OBSERVATION_DEPTH = 8;
const MAXIMUM_OBSERVATION_ARRAY_LENGTH = 1024;
const MAXIMUM_OBSERVATION_OBJECT_KEYS = 256;
const MAXIMUM_OBSERVATION_KEY_LENGTH = 128;
const MAXIMUM_OBSERVATION_STRING_LENGTH = 4096;
const claims: Phase3b1PrivateFailureClaims = Object.freeze({
  humanListeningClaimed: false,
  naturalnessClaimed: false,
  qualityClaimed: false,
  consentClaimed: false,
  commercialClearanceClaimed: false,
  productionReadinessClaimed: false
});

export async function writePhase3b1PrivateFailureSidecar(
  input: WritePhase3b1PrivateFailureSidecarInput,
  options: SidecarWriterOptions = {}
): Promise<Phase3b1PrivateFailureSidecarWriteResult> {
  const parent = path.resolve(input.expectedLocalRendersParent);
  const privateRoot = path.resolve(input.privateRoot);
  await assertCanonicalDirectory(parent);
  assertDirectChild(parent, privateRoot);
  await createOrVerifyPrivateRoot(privateRoot);

  const failuresDirectory = path.join(privateRoot, "failures");
  await createOrVerifyFailuresDirectory(failuresDirectory);
  await assertBoundDirectory(parent, privateRoot, failuresDirectory);

  const recordedAt = canonicalTimestamp(
    (options.now ?? (() => new Date()))(),
    "recorded timestamp"
  );
  const value = createSidecarValue(input, recordedAt);
  const serialized = `${JSON.stringify(value, null, 2)}\n`;
  const byteSize = Buffer.byteLength(serialized, "utf8");
  if (byteSize > maximumPhase3b1PrivateFailureSidecarBytes) {
    throw new Error("The private failure sidecar exceeded its size limit.");
  }

  const token = (options.tokenFactory ?? randomToken)();
  if (!SAFE_TOKEN.test(token)) {
    throw new Error("The private failure sidecar token was invalid.");
  }
  const timestampToken = recordedAt.replace(/[-:.]/gu, "");
  const fileName =
    `phase3b1-failure-launch-${input.launch}-${timestampToken}-${token}.json`;
  const finalPath = path.join(failuresDirectory, fileName);
  const temporaryPath = path.join(
    failuresDirectory,
    `.${fileName}.${randomToken()}.tmp`
  );

  try {
    await writeFile(temporaryPath, serialized, {
      encoding: "utf8",
      flag: "wx",
      mode: 0o600
    });
    await assertBoundDirectory(parent, privateRoot, failuresDirectory);
    try {
      // A hard-link publication is both atomic and exclusive: unlike rename,
      // it cannot replace an existing final sidecar on any supported runner.
      await link(temporaryPath, finalPath);
    } catch (error: unknown) {
      if (isFileExistsError(error)) {
        throw new Error(
          "The private failure sidecar target already exists.",
          { cause: error }
        );
      }
      throw new Error("The private failure sidecar could not be published.", {
        cause: error
      });
    }
    await assertBoundDirectory(parent, privateRoot, failuresDirectory);
  } finally {
    await rm(temporaryPath, { force: true });
  }

  return {
    relativePath: `failures/${fileName}`,
    fileName,
    byteSize,
    value
  };
}

function createSidecarValue(
  input: WritePhase3b1PrivateFailureSidecarInput,
  recordedAt: string
): Phase3b1PrivateFailureSidecar {
  requireMatching(input.sourceHeadSha, SHA1, "source head");
  requireMatching(input.applicationVersion, SAFE_VERSION, "app version");
  const executableRelativePath = requireRepositoryRelativeExecutable(
    input.executableRelativePath
  );
  if (input.launch !== 3 && input.launch !== 4) {
    throw new Error("The private failure launch was invalid.");
  }
  if (!phase3b1PrivateFailureStages.includes(input.stage)) {
    throw new Error("The private failure stage was invalid.");
  }
  if (!phase3b1PrivateFailureCodes.includes(input.failureCode)) {
    throw new Error("The private failure code was invalid.");
  }
  if (
    !Number.isSafeInteger(input.configuredLaunchTimeoutMs) ||
    input.configuredLaunchTimeoutMs < 1 ||
    input.configuredLaunchTimeoutMs > 10 * 60 * 1000
  ) {
    throw new Error("The configured launch timeout was invalid.");
  }
  const startedAt = requireIsoTimestamp(input.startedAt, "start timestamp");
  const failedAt = requireIsoTimestamp(input.failedAt, "failure timestamp");
  if (
    Date.parse(startedAt) > Date.parse(failedAt) ||
    Date.parse(failedAt) > Date.parse(recordedAt)
  ) {
    throw new Error("The private failure timestamps were out of order.");
  }
  if (input.ownedProcessExitClaimed && !input.ownershipEstablished) {
    throw new Error(
      "Owned-process exit cannot be claimed without established ownership."
    );
  }

  const rejectedLaunchBaselineObservation =
    input.rejectedLaunchBaselineObservation === null
      ? null
      : sanitizeObservationRecord(
          input.rejectedLaunchBaselineObservation,
          0
        );
  if (
    (input.failureCode === "launch_timeout" ||
      input.failureCode === "launch_rejected") &&
    rejectedLaunchBaselineObservation === null
  ) {
    throw new Error(
      "A rejected-launch baseline observation was required."
    );
  }

  return {
    schemaVersion: phase3b1PrivateFailureSidecarSchemaVersion,
    result: "failed",
    sourceHeadSha: input.sourceHeadSha,
    applicationVersion: input.applicationVersion,
    executableRelativePath,
    launch: input.launch,
    stage: input.stage,
    failureCode: input.failureCode,
    configuredLaunchTimeoutMs: input.configuredLaunchTimeoutMs,
    startedAt,
    failedAt,
    recordedAt,
    syntheticGateCompleted: input.syntheticGateCompleted,
    ownershipEstablished: input.ownershipEstablished,
    ownedProcessExitClaimed: input.ownedProcessExitClaimed,
    cleanupCompleted: input.cleanupCompleted,
    rejectedLaunchBaselineObservation,
    claims
  };
}

function sanitizeObservationRecord(
  value: unknown,
  depth: number
): RejectedLaunchBaselineObservation {
  if (!isPlainRecord(value)) {
    throw new Error("The rejected-launch observation was invalid.");
  }
  return sanitizeRecord(
    value,
    depth
  ) as unknown as RejectedLaunchBaselineObservation;
}

function sanitizeRecord(
  value: Readonly<Record<string, unknown>>,
  depth: number
): Readonly<Record<string, Phase3b1PrivateFailureJsonValue>> {
  requireObservationDepth(depth);
  const descriptors = Object.getOwnPropertyDescriptors(value);
  const keys = Object.keys(descriptors).sort((left, right) =>
    left.localeCompare(right)
  );
  if (keys.length > MAXIMUM_OBSERVATION_OBJECT_KEYS) {
    throw new Error("The rejected-launch observation was too large.");
  }

  const copy: Record<string, Phase3b1PrivateFailureJsonValue> = {};
  for (const key of keys) {
    if (
      key.length === 0 ||
      key.length > MAXIMUM_OBSERVATION_KEY_LENGTH ||
      FORBIDDEN_OBSERVATION_KEYS.has(normalizeObservationKey(key))
    ) {
      throw new Error("The rejected-launch observation key was unsafe.");
    }
    const descriptor = descriptors[key];
    if (descriptor === undefined || !("value" in descriptor)) {
      throw new Error("The rejected-launch observation was not data-only.");
    }
    copy[key] = sanitizeObservationValue(descriptor.value, depth + 1);
  }
  return Object.freeze(copy);
}

function sanitizeObservationValue(
  value: unknown,
  depth: number
): Phase3b1PrivateFailureJsonValue {
  requireObservationDepth(depth);
  if (value === null || typeof value === "boolean") return value;
  if (typeof value === "number") {
    if (!Number.isFinite(value)) {
      throw new Error("The rejected-launch observation number was invalid.");
    }
    return value;
  }
  if (typeof value === "string") {
    if (
      value.length > MAXIMUM_OBSERVATION_STRING_LENGTH ||
      containsAbsolutePath(value)
    ) {
      throw new Error("The rejected-launch observation string was unsafe.");
    }
    return value;
  }
  if (Array.isArray(value)) {
    if (value.length > MAXIMUM_OBSERVATION_ARRAY_LENGTH) {
      throw new Error("The rejected-launch observation was too large.");
    }
    const descriptors = Object.getOwnPropertyDescriptors(value);
    const copy: Phase3b1PrivateFailureJsonValue[] = [];
    for (let index = 0; index < value.length; index += 1) {
      const descriptor = descriptors[String(index)];
      if (descriptor === undefined || !("value" in descriptor)) {
        throw new Error("The rejected-launch observation was not data-only.");
      }
      copy.push(sanitizeObservationValue(descriptor.value, depth + 1));
    }
    return Object.freeze(copy);
  }
  if (isPlainRecord(value)) return sanitizeRecord(value, depth);
  throw new Error("The rejected-launch observation value was invalid.");
}

function requireObservationDepth(depth: number): void {
  if (depth > MAXIMUM_OBSERVATION_DEPTH) {
    throw new Error("The rejected-launch observation was too deeply nested.");
  }
}

function isPlainRecord(
  value: unknown
): value is Readonly<Record<string, unknown>> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return false;
  }
  const prototype = Object.getPrototypeOf(value) as object | null;
  return prototype === Object.prototype || prototype === null;
}

function normalizeObservationKey(value: string): string {
  return value.toLowerCase().replace(/[^a-z]/gu, "");
}

function containsAbsolutePath(value: string): boolean {
  return (
    path.win32.isAbsolute(value) ||
    path.posix.isAbsolute(value) ||
    /(?:^|[\s"'(=])(?:[A-Za-z]:[\\/]|\\\\(?:\?\\)?|\/(?:Users|home|tmp|var|etc|opt|mnt|Volumes|private)(?:\/|$))/iu.test(
      value
    ) ||
    /file:\/\//iu.test(value)
  );
}

function requireRepositoryRelativeExecutable(value: string): string {
  if (
    value.length === 0 ||
    value.length > 500 ||
    value.includes("\\") ||
    path.win32.isAbsolute(value) ||
    path.posix.isAbsolute(value) ||
    path.posix.normalize(value) !== value ||
    value === "." ||
    value.startsWith("../") ||
    value.includes("/../") ||
    !value.toLowerCase().endsWith(".exe")
  ) {
    throw new Error("The packaged executable path was not repository-relative.");
  }
  return value;
}

function requireMatching(
  value: string,
  pattern: RegExp,
  label: string
): void {
  if (!pattern.test(value)) {
    throw new Error(`The private failure ${label} was invalid.`);
  }
}

function requireIsoTimestamp(value: string, label: string): string {
  if (!ISO_TIMESTAMP.test(value)) {
    throw new Error(`The private failure ${label} was invalid.`);
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.valueOf()) || parsed.toISOString() !== value) {
    throw new Error(`The private failure ${label} was invalid.`);
  }
  return value;
}

function canonicalTimestamp(value: Date, label: string): string {
  if (Number.isNaN(value.valueOf())) {
    throw new Error(`The private failure ${label} was invalid.`);
  }
  return value.toISOString();
}

async function createOrVerifyFailuresDirectory(
  failuresDirectory: string
): Promise<void> {
  try {
    await mkdir(failuresDirectory, { mode: 0o700 });
  } catch (error: unknown) {
    if (!isFileExistsError(error)) {
      throw new Error("The private failure directory could not be created.", {
        cause: error
      });
    }
  }
  await assertCanonicalDirectory(failuresDirectory);
}

async function createOrVerifyPrivateRoot(privateRoot: string): Promise<void> {
  try {
    await mkdir(privateRoot, { mode: 0o700 });
  } catch (error: unknown) {
    if (!isFileExistsError(error)) {
      throw new Error("The private evidence root could not be created.", {
        cause: error
      });
    }
  }
  await assertCanonicalDirectory(privateRoot);
}

async function assertBoundDirectory(
  parent: string,
  privateRoot: string,
  failuresDirectory: string
): Promise<void> {
  await assertCanonicalDirectory(parent);
  await assertCanonicalDirectory(privateRoot);
  assertDirectChild(parent, privateRoot);
  await assertCanonicalDirectory(failuresDirectory);
  if (!samePath(path.dirname(failuresDirectory), privateRoot)) {
    throw new Error("The private failure directory escaped its root.");
  }
}

async function assertCanonicalDirectory(candidate: string): Promise<void> {
  try {
    const [metadata, canonical] = await Promise.all([
      lstat(candidate),
      realpath(candidate)
    ]);
    if (
      !metadata.isDirectory() ||
      metadata.isSymbolicLink() ||
      !samePath(canonical, candidate)
    ) {
      throw new Error("noncanonical");
    }
  } catch {
    throw new Error("A private failure evidence directory was not canonical.");
  }
}

function assertDirectChild(parent: string, candidate: string): void {
  const relative = path.relative(parent, candidate);
  if (
    relative.length === 0 ||
    path.isAbsolute(relative) ||
    relative === ".." ||
    relative.startsWith(`..${path.sep}`) ||
    relative.includes(path.sep)
  ) {
    throw new Error(
      "The private evidence root was not a direct child of local-renders."
    );
  }
}

function samePath(left: string, right: string): boolean {
  const normalizedLeft = path.resolve(left);
  const normalizedRight = path.resolve(right);
  return process.platform === "win32"
    ? normalizedLeft.toLowerCase() === normalizedRight.toLowerCase()
    : normalizedLeft === normalizedRight;
}

function randomToken(): string {
  return randomBytes(16).toString("hex");
}

function isFileExistsError(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "code" in error &&
    error.code === "EEXIST"
  );
}
