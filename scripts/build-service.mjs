import { constants as fsConstants } from "node:fs";
import {
  access,
  cp,
  mkdir,
  readFile,
  rm,
  writeFile,
} from "node:fs/promises";
import path from "node:path";
import { withoutSensitiveValues } from "./lib/environment.mjs";
import {
  assertRepositoryChild,
  desktopRoot,
  repositoryRoot,
  serviceRoot,
  serviceSourceRoot,
} from "./lib/paths.mjs";
import { capture } from "./lib/process.mjs";
import { requireServicePython } from "./lib/python-runtime.mjs";

const executableName = "cinematic-story-service.exe";
const pyinstallerName = "cinematic-story-service";
const buildRoot = path.join(repositoryRoot, "build", "pyinstaller");
const distributionRoot = path.join(buildRoot, "dist");
const workRoot = path.join(buildRoot, "work");
const specificationRoot = path.join(buildRoot, "spec");
const stagingRoot = path.join(
  desktopRoot,
  "build-resources",
  "service",
);
const launcher = path.join(
  serviceSourceRoot,
  "cinematic_story_service",
  "launcher.py",
);
const specification = path.join(
  serviceRoot,
  "cinematic-story-service.spec",
);

async function resetGeneratedDirectory(target, label) {
  assertRepositoryChild(target, label);
  await rm(target, { recursive: true, force: true });
  await mkdir(target, { recursive: true });
}

async function assertReadable(target, message) {
  try {
    await access(target, fsConstants.R_OK);
  } catch {
    throw new Error(message);
  }
}

async function main() {
  if (process.platform !== "win32") {
    throw new Error("The packaged local-service artifact is currently a Windows-only build.");
  }

  const mode = process.env.CSS_PYINSTALLER_MODE?.trim() || "onefile";
  if (!["onefile", "onedir"].includes(mode)) {
    throw new Error("CSS_PYINSTALLER_MODE must be `onefile` or `onedir`.");
  }

  await assertReadable(
    path.join(serviceRoot, "pyproject.toml"),
    "The local-service pyproject is missing.",
  );
  await assertReadable(
    launcher,
    "The PyInstaller launcher is missing at src/cinematic_story_service/launcher.py.",
  );
  await assertReadable(
    specification,
    "The pinned PyInstaller specification is missing.",
  );

  const python = await requireServicePython();
  await resetGeneratedDirectory(buildRoot, "PyInstaller work root");
  await resetGeneratedDirectory(stagingRoot, "service staging root");
  await mkdir(distributionRoot, { recursive: true });
  await mkdir(workRoot, { recursive: true });
  await mkdir(specificationRoot, { recursive: true });

  process.stdout.write(`Building the local service with PyInstaller (${mode}).\n`);
  const pyinstallerArguments =
    mode === "onefile"
      ? [
          "-m",
          "PyInstaller",
          "--noconfirm",
          "--clean",
          "--distpath",
          distributionRoot,
          "--workpath",
          workRoot,
          specification,
        ]
      : [
          "-m",
          "PyInstaller",
          "--noconfirm",
          "--clean",
          "--noupx",
          "--console",
          "--onedir",
          "--name",
          pyinstallerName,
          "--paths",
          serviceSourceRoot,
          "--collect-submodules",
          "cinematic_story_service",
          "--collect-submodules",
          "uvicorn",
          "--collect-submodules",
          "fastapi",
          "--collect-submodules",
          "sqlalchemy.dialects.sqlite",
          "--exclude-module",
          "tkinter",
          "--distpath",
          distributionRoot,
          "--workpath",
          workRoot,
          "--specpath",
          specificationRoot,
          launcher,
        ];
  await capture(
    python,
    pyinstallerArguments,
    {
      cwd: repositoryRoot,
      env: withoutSensitiveValues(),
      label: "PyInstaller service build",
      maxBytes: 8 * 1024 * 1024,
    },
  );

  if (mode === "onefile") {
    const builtExecutable = path.join(distributionRoot, executableName);
    await assertReadable(
      builtExecutable,
      "PyInstaller did not create the expected one-file service executable.",
    );
    await cp(builtExecutable, path.join(stagingRoot, executableName));
  } else {
    const builtDirectory = path.join(distributionRoot, pyinstallerName);
    await assertReadable(
      path.join(builtDirectory, executableName),
      "PyInstaller did not create the expected one-dir service executable.",
    );
    await cp(builtDirectory, stagingRoot, { recursive: true });
  }

  const manifest = {
    formatVersion: 1,
    mode,
    executable: executableName,
    module: "cinematic_story_service.launcher",
  };
  await writeFile(
    path.join(stagingRoot, "service-build.json"),
    `${JSON.stringify(manifest, null, 2)}\n`,
    "utf8",
  );

  const stagedExecutable = path.join(stagingRoot, executableName);
  await assertReadable(stagedExecutable, "The staged service executable is missing.");

  const smokeResult = await capture(stagedExecutable, ["--help"], {
    allowFailure: true,
    cwd: stagingRoot,
    env: withoutSensitiveValues(),
    label: "Staged service startup smoke test",
    maxBytes: 256 * 1024,
    timeoutMs: 30_000,
  });
  if (smokeResult.code !== 0) {
    throw new Error("The staged service executable failed its startup/help smoke test.");
  }

  const stagedManifest = JSON.parse(
    await readFile(path.join(stagingRoot, "service-build.json"), "utf8"),
  );
  if (stagedManifest.executable !== executableName) {
    throw new Error("The staged service manifest failed validation.");
  }

  process.stdout.write(
    "Local service built, smoke-tested, and staged for electron-builder.\n",
  );
}

main().catch((error) => {
  process.stderr.write(`Service build: ${error.message}\n`);
  process.exitCode = 1;
});
