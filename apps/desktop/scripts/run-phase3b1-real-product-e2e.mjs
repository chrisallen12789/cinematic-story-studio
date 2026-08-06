import { spawn, spawnSync } from "node:child_process";
import { lstat, readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, URL } from "node:url";

const desktopRoot = fileURLToPath(new URL("..", import.meta.url));
const repositoryRoot = path.resolve(desktopRoot, "../..");
const packageRecord = JSON.parse(
  await readFile(path.join(desktopRoot, "package.json"), "utf8")
);
if (
  typeof packageRecord.version !== "string" ||
  !/^\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?$/u.test(packageRecord.version)
) {
  throw new Error("The desktop application version is invalid.");
}

const version = packageRecord.version;
const releaseRoot = path.join(desktopRoot, "release", version);
const executable = path.join(
  releaseRoot,
  "win-unpacked",
  "Cinematic Story Studio.exe"
);
const modelPackageZip = path.join(
  repositoryRoot,
  "local-models",
  "kokoro-phase3b-package.zip"
);
const privateEvidenceRoot = path.join(
  repositoryRoot,
  "local-renders",
  "phase3b1-real-product-path"
);

for (const [label, target] of [
  ["exact packaged desktop executable", executable],
  ["exact ignored Kokoro model ZIP", modelPackageZip]
]) {
  let metadata;
  try {
    metadata = await lstat(target);
  } catch {
    throw new Error(
      `The ${label} is missing. Build the application and prepare the already-local allow-listed package before running this gate.`
    );
  }
  if (!metadata.isFile() || metadata.isSymbolicLink()) {
    throw new Error(`The ${label} must be a regular file.`);
  }
}

const head = runGit(["rev-parse", "HEAD"]);
if (!/^[a-f0-9]{40}$/u.test(head)) {
  throw new Error("Git did not return an exact lowercase source head SHA.");
}
const trackedStatus = runGit([
  "status",
  "--porcelain=v1",
  "--untracked-files=all"
]);
if (trackedStatus.length !== 0) {
  throw new Error(
    "The Phase 3B.1 real product-path gate requires a frozen clean tracked and untracked source tree. Ignored local models, renders, and build outputs are preserved."
  );
}

const environment = {
  ...process.env,
  CSS_PACKAGED_E2E_EXECUTABLE: executable,
  CSS_PACKAGED_E2E_EVIDENCE_PATH: path.join(
    releaseRoot,
    "packaged-e2e.png"
  ),
  CSS_PACKAGED_E2E_RESULT_PATH: path.join(
    releaseRoot,
    "packaged-e2e-result.json"
  ),
  CSS_PHASE3_PACKAGED_E2E_RESULT_PATH: path.join(
    releaseRoot,
    "phase-3-packaged-e2e-result.json"
  ),
  CSS_PHASE3_VOICE_CASTING_EVIDENCE_PATH: path.join(
    releaseRoot,
    "phase-3-voice-casting-evidence.json"
  ),
  CSS_PHASE3B_PACKAGED_E2E_RESULT_PATH: path.join(
    releaseRoot,
    "phase-3b-packaged-e2e-result.json"
  ),
  CSS_PHASE3B1_REAL_MODEL_PACKAGE_ZIP: modelPackageZip,
  CSS_PHASE3B1_PRIVATE_EVIDENCE_ROOT: privateEvidenceRoot,
  CSS_PHASE3B1_SOURCE_HEAD_SHA: head
};

const child = spawn(
  process.execPath,
  [path.join(desktopRoot, "scripts", "run-packaged-e2e.mjs")],
  {
    cwd: desktopRoot,
    env: environment,
    shell: false,
    stdio: "inherit",
    windowsHide: true
  }
);
process.exitCode = await new Promise((resolve) => {
  child.once("error", () => resolve(1));
  child.once("exit", (code) => resolve(code ?? 1));
});

function runGit(arguments_) {
  const result = spawnSync("git", arguments_, {
    cwd: repositoryRoot,
    encoding: "utf8",
    shell: false,
    windowsHide: true,
    maxBuffer: 1024 * 1024
  });
  if (result.error !== undefined || result.status !== 0) {
    const detail = String(result.stderr ?? "").trim();
    throw new Error(
      `Git ${arguments_.join(" ")} failed${detail.length === 0 ? "." : `: ${detail}`}`
    );
  }
  return String(result.stdout ?? "").trim();
}
