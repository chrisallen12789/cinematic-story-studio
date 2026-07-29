import { constants as fsConstants } from "node:fs";
import { access } from "node:fs/promises";
import { withoutSensitiveValues } from "./lib/environment.mjs";
import {
  repositoryRoot,
  servicePython,
  serviceSourceRoot,
} from "./lib/paths.mjs";
import { platformCommand, run } from "./lib/process.mjs";

async function main() {
  try {
    await access(servicePython, fsConstants.X_OK);
  } catch {
    throw new Error("The service environment is missing. Run `pnpm install` first.");
  }

  const environment = {
    ...withoutSensitiveValues(),
    CSS_PYTHON: servicePython,
    CSS_SERVICE_PYTHON: servicePython,
    PYTHONPATH: serviceSourceRoot,
  };

  process.stdout.write(
    "Starting the desktop in development mode; Electron will own the loopback service process.\n",
  );
  await run(
    platformCommand("pnpm"),
    ["--filter", "@cinematic-story-studio/desktop", "run", "dev"],
    {
      cwd: repositoryRoot,
      env: environment,
      label: "Desktop development process",
    },
  );
}

main().catch((error) => {
  process.stderr.write(`Development launcher: ${error.message}\n`);
  process.exitCode = 1;
});
