import { spawn } from "node:child_process";
import { URL, fileURLToPath } from "node:url";

const requiredEnvironment = [
  "CSS_PACKAGED_E2E_EXECUTABLE",
  "CSS_PACKAGED_E2E_EVIDENCE_PATH",
  "CSS_PACKAGED_E2E_RESULT_PATH"
];
const missing = requiredEnvironment.filter((name) => {
  const value = process.env[name];
  return value === undefined || value.trim().length === 0;
});

if (missing.length > 0) {
  process.stderr.write(
    `Missing required packaged E2E environment: ${missing.join(", ")}\n`
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
      "tests/e2e/packaged-persistence.spec.ts"
    ],
    {
      cwd: fileURLToPath(new URL("..", import.meta.url)),
      env: process.env,
      shell: false,
      stdio: "inherit",
      windowsHide: true
    }
  );
  process.exitCode = await new Promise((resolve) => {
    child.once("error", () => {
      resolve(1);
    });
    child.once("exit", (code) => {
      resolve(code ?? 1);
    });
  });
}
