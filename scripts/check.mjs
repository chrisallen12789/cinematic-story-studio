import { readdir } from "node:fs/promises";
import path from "node:path";
import { withoutSensitiveValues } from "./lib/environment.mjs";
import { repositoryRoot } from "./lib/paths.mjs";
import { platformCommand, run } from "./lib/process.mjs";
import { runServicePython } from "./lib/python-runtime.mjs";

const FIXED_TEST_TOKEN = "phase-zero-test-token-not-valid-outside-automated-checks";

function sanitizedTestEnvironment() {
  const environment = withoutSensitiveValues();
  return {
    ...environment,
    CSS_DEV_MODE: "1",
    CSS_DEV_TOKEN: FIXED_TEST_TOKEN,
    CSS_NETWORK_POLICY: "loopback-only",
    ALL_PROXY: "",
    HTTP_PROXY: "",
    HTTPS_PROXY: "",
    NO_PROXY: "127.0.0.1,localhost",
    all_proxy: "",
    http_proxy: "",
    https_proxy: "",
    no_proxy: "127.0.0.1,localhost",
  };
}

async function runWorkspaceScript(packageName, script, environment = process.env) {
  await run(
    platformCommand("pnpm"),
    ["--filter", packageName, "run", script],
    {
      cwd: repositoryRoot,
      env: environment,
      label: `${packageName} ${script}`,
    },
  );
}

async function collectModuleScripts(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const target = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      files.push(...(await collectModuleScripts(target)));
    } else if (entry.isFile() && entry.name.endsWith(".mjs")) {
      files.push(target);
    }
  }
  return files.sort();
}

async function lint() {
  process.stdout.write("Checking root Node helper syntax.\n");
  for (const script of await collectModuleScripts(path.join(repositoryRoot, "scripts"))) {
    await run(process.execPath, ["--check", script], {
      cwd: repositoryRoot,
      label: "Node helper syntax check",
    });
  }

  await runWorkspaceScript("@cinematic-story-studio/desktop", "lint");
  await runServicePython(["-m", "ruff", "check", "apps/local-service"], {
    label: "Python lint",
  });
  await run(process.execPath, ["scripts/repo-scan.mjs", "--tracked"], {
    cwd: repositoryRoot,
    label: "Tracked repository policy scan",
  });
  await run(process.execPath, ["scripts/git-check.mjs"], {
    cwd: repositoryRoot,
    label: "Git diff check",
  });
}

async function typecheck() {
  await runWorkspaceScript("@cinematic-story-studio/contracts", "typecheck");
  await runWorkspaceScript("@cinematic-story-studio/desktop", "typecheck");
  await runServicePython(["-m", "mypy", "apps/local-service/src"], {
    label: "Python type check",
  });
}

async function testAll() {
  const environment = sanitizedTestEnvironment();
  await run(
    process.execPath,
    [
      "--test",
      "schemas/tests/schema-structure.test.mjs",
      "schemas/tests/phase3b-schema-structure.test.mjs",
      "tests/tooling/build-evidence.test.mjs",
      "tests/tooling/repository-policy.test.mjs",
      "tests/tooling/root-tooling.test.mjs",
      "tests/tooling/synthetic-fixture.test.mjs",
      "tests/tooling/synthetic-voice-catalog.test.mjs",
    ],
    {
      cwd: repositoryRoot,
      env: environment,
      label: "Repository contract and safeguard tests",
    },
  );

  await runWorkspaceScript("@cinematic-story-studio/contracts", "build", environment);
  await runServicePython(["-m", "pytest", "apps/local-service/tests"], {
    env: environment,
    label: "Local-service tests",
  });
  await runWorkspaceScript("@cinematic-story-studio/desktop", "test", environment);
}

async function main() {
  const command = process.argv[2];
  if (command === "lint") {
    await lint();
  } else if (command === "typecheck") {
    await typecheck();
  } else if (command === "test") {
    await testAll();
  } else {
    throw new Error("Usage: node scripts/check.mjs <lint|typecheck|test>");
  }
}

main().catch((error) => {
  process.stderr.write(`Check failed: ${error.message}\n`);
  process.exitCode = 1;
});
