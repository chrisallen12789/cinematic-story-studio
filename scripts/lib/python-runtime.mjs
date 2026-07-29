import { constants as fsConstants } from "node:fs";
import { access, mkdir } from "node:fs/promises";
import path from "node:path";
import {
  repositoryRoot,
  servicePython,
  serviceRoot,
  serviceVenvRoot,
} from "./paths.mjs";
import { capture, run } from "./process.mjs";

const REQUIRED_PYTHON = Object.freeze({ major: 3, minor: 12 });

async function isExecutable(target) {
  try {
    await access(target, fsConstants.X_OK);
    return true;
  } catch {
    return false;
  }
}

async function candidateWorks(candidate) {
  const versionCheck = [
    ...candidate.prefixArgs,
    "-c",
    [
      "import sys",
      `raise SystemExit(0 if sys.version_info >= (${REQUIRED_PYTHON.major}, ${REQUIRED_PYTHON.minor}) else 1)`,
    ].join("; "),
  ];
  const result = await capture(candidate.command, versionCheck, {
    allowFailure: true,
    maxBytes: 4096,
    label: "Python version check",
  }).catch(() => ({ code: 1 }));
  return result.code === 0;
}

async function findBootstrapPython() {
  const configured = process.env.CSS_BOOTSTRAP_PYTHON?.trim();
  const candidates = configured
    ? [{ command: configured, prefixArgs: [] }]
    : process.platform === "win32"
      ? [
          { command: "py", prefixArgs: ["-3.12"] },
          { command: "python", prefixArgs: [] },
          { command: "python3", prefixArgs: [] },
        ]
      : [
          { command: "python3.12", prefixArgs: [] },
          { command: "python3", prefixArgs: [] },
          { command: "python", prefixArgs: [] },
        ];

  for (const candidate of candidates) {
    if (await candidateWorks(candidate)) {
      return candidate;
    }
  }

  throw new Error(
    `Python ${REQUIRED_PYTHON.major}.${REQUIRED_PYTHON.minor}+ was not found. Install it from python.org or set CSS_BOOTSTRAP_PYTHON to its executable.`,
  );
}

export async function ensureServiceVenv() {
  if (await isExecutable(servicePython)) {
    return servicePython;
  }

  const bootstrap = await findBootstrapPython();
  await mkdir(path.dirname(serviceVenvRoot), { recursive: true });
  await run(
    bootstrap.command,
    [...bootstrap.prefixArgs, "-m", "venv", serviceVenvRoot],
    {
      cwd: repositoryRoot,
      label: "Python virtual environment creation",
    },
  );

  if (!(await isExecutable(servicePython))) {
    throw new Error("Python reported success but the service virtual environment is incomplete.");
  }
  return servicePython;
}

export async function requireServicePython() {
  if (!(await isExecutable(servicePython))) {
    throw new Error(
      "The local-service virtual environment is missing. Run `pnpm install` from the repository root.",
    );
  }
  return servicePython;
}

export async function installServiceDevelopmentDependencies() {
  const pyproject = path.join(serviceRoot, "pyproject.toml");
  try {
    await access(pyproject, fsConstants.R_OK);
  } catch {
    throw new Error("apps/local-service/pyproject.toml is required before installing Python dependencies.");
  }

  const python = await ensureServiceVenv();
  await run(
    python,
    [
      "-m",
      "pip",
      "install",
      "--disable-pip-version-check",
      "-e",
      "apps/local-service[dev]",
    ],
    {
      cwd: repositoryRoot,
      label: "Local-service development dependency install",
    },
  );
  return python;
}

export async function runServicePython(args, options = {}) {
  const python = options.ensureOnly
    ? await ensureServiceVenv()
    : await requireServicePython();
  await run(python, args, {
    cwd: options.cwd ?? repositoryRoot,
    env: options.env,
    label: options.label ?? "Python command",
  });
}
