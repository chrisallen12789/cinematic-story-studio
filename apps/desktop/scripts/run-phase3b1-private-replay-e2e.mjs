import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath, URL } from "node:url";

const packageEnvironment = "CSS_PHASE3B1_PRIVATE_REPLAY_PACKAGE";
const recordEnvironment = "CSS_PHASE3B1_RECORD_PRIVATE_DECISIONS";
const runnerEnvironment = "CSS_PHASE3B1_PRIVATE_REPLAY_RUNNER";
const desktopRoot = fileURLToPath(new URL("..", import.meta.url));
const packageDirectory = process.env[packageEnvironment];
const recordMode = process.env[recordEnvironment];

if (
  process.platform !== "win32" ||
  packageDirectory === undefined ||
  packageDirectory.trim().length === 0 ||
  packageDirectory.includes("\0") ||
  packageDirectory.length > 2_048 ||
  !path.isAbsolute(packageDirectory)
) {
  process.stderr.write(
    `${packageEnvironment} must name one explicit bounded absolute ignored private listening-package directory on Windows.\n`
  );
  process.exitCode = 2;
} else if (recordMode !== undefined && recordMode !== "1") {
  process.stderr.write(
    `${recordEnvironment}, when supplied, must be exactly 1. Omit it for immutable verification.\n`
  );
  process.exitCode = 2;
} else {
  const playwrightCli = fileURLToPath(
    new URL("../node_modules/@playwright/test/cli.js", import.meta.url)
  );
  const child = spawn(
    process.execPath,
    [
      playwrightCli,
      "test",
      "tests/e2e/phase3b1-private-replay.spec.ts",
      "--workers=1"
    ],
    {
      cwd: desktopRoot,
      env: {
        ...process.env,
        [packageEnvironment]: path.resolve(packageDirectory),
        [runnerEnvironment]: "1"
      },
      shell: false,
      stdio: "inherit",
      windowsHide: true
    }
  );
  process.exitCode = await new Promise((resolve) => {
    child.once("error", () => resolve(1));
    child.once("exit", (code) => resolve(code ?? 1));
  });
}
