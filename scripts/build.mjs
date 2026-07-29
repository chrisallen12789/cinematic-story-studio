import { constants as fsConstants } from "node:fs";
import { access, readFile } from "node:fs/promises";
import path from "node:path";
import { withoutSensitiveValues } from "./lib/environment.mjs";
import {
  desktopRoot,
  repositoryRoot,
} from "./lib/paths.mjs";
import { platformCommand, run } from "./lib/process.mjs";

const buildEnvironment = {
  ...withoutSensitiveValues(),
  CSC_IDENTITY_AUTO_DISCOVERY: "false",
};

async function runWorkspaceScript(packageName, script) {
  await run(
    platformCommand("pnpm"),
    ["--filter", packageName, "run", script],
    {
      cwd: repositoryRoot,
      env: buildEnvironment,
      label: `${packageName} ${script}`,
    },
  );
}

async function assertReadable(target, message) {
  try {
    await access(target, fsConstants.R_OK);
  } catch {
    throw new Error(message);
  }
}

async function main() {
  await runWorkspaceScript("@cinematic-story-studio/contracts", "build");
  await run(process.execPath, ["scripts/build-service.mjs"], {
    cwd: repositoryRoot,
    env: buildEnvironment,
    label: "Local-service package build",
  });
  await runWorkspaceScript("@cinematic-story-studio/desktop", "package:dir");

  const desktopPackage = JSON.parse(
    await readFile(path.join(desktopRoot, "package.json"), "utf8"),
  );
  const unpackedRoot = path.join(
    desktopRoot,
    "release",
    desktopPackage.version,
    "win-unpacked",
  );
  await assertReadable(
    path.join(unpackedRoot, "Cinematic Story Studio.exe"),
    "electron-builder did not create the expected unpackaged desktop executable.",
  );
  await assertReadable(
    path.join(
      unpackedRoot,
      "resources",
      "service",
      "cinematic-story-service.exe",
    ),
    "The unpackaged desktop artifact does not contain the staged local service.",
  );

  process.stdout.write(
    `Build complete: apps/desktop/release/${desktopPackage.version}/win-unpacked\n`,
  );
}

main().catch((error) => {
  process.stderr.write(`Root build: ${error.message}\n`);
  process.exitCode = 1;
});
