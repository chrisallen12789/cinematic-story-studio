import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { normalizedInvocation } from "../../scripts/lib/process.mjs";

const repositoryRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
);

async function json(relativePath) {
  return JSON.parse(
    await readFile(path.join(repositoryRoot, relativePath), "utf8"),
  );
}

test("root exposes the exact supported Phase 0 command surface", async () => {
  const manifest = await json("package.json");
  for (const command of ["dev", "lint", "typecheck", "test", "build"]) {
    assert.equal(typeof manifest.scripts[command], "string", `missing pnpm ${command}`);
  }
  assert.equal(manifest.scripts.postinstall, "node scripts/python.mjs install");
  assert.match(manifest.packageManager, /^pnpm@11\./);
});

test("workspace package names match root orchestration filters", async () => {
  const desktop = await json("apps/desktop/package.json");
  const contracts = await json("packages/contracts/package.json");
  const workspace = await readFile(
    path.join(repositoryRoot, "pnpm-workspace.yaml"),
    "utf8",
  );

  assert.equal(desktop.name, "@cinematic-story-studio/desktop");
  assert.equal(contracts.name, "@cinematic-story-studio/contracts");
  assert.match(workspace, /^\s+- apps\/\*\s*$/m);
  assert.match(workspace, /^\s+- packages\/\*\s*$/m);
  assert.match(workspace, /^allowBuilds:\s*$/m);
  assert.match(workspace, /^\s+electron: true\s*$/m);
  assert.match(workspace, /^\s+esbuild: true\s*$/m);
});

test("private local roots track placeholders only", async () => {
  for (const directory of [
    "local-projects",
    "local-models",
    "local-cache",
    "local-renders",
  ]) {
    await access(path.join(repositoryRoot, directory, ".gitkeep"));
  }

  const ignore = await readFile(path.join(repositoryRoot, ".gitignore"), "utf8");
  for (const directory of [
    "local-projects",
    "local-models",
    "local-cache",
    "local-renders",
  ]) {
    assert.match(ignore, new RegExp(`^${directory}/\\*$`, "m"));
    assert.match(ignore, new RegExp(`^!${directory}/\\.gitkeep$`, "m"));
  }
});

test("Windows pnpm shim adapter rejects shell metacharacters", () => {
  const invocation = normalizedInvocation(
    "pnpm.cmd",
    ["--filter", "@cinematic-story-studio/desktop", "run", "lint"],
    "win32",
  );
  assert.equal(path.win32.basename(invocation.command).toLowerCase(), "cmd.exe");
  assert.deepEqual(invocation.args, [
    "/d",
    "/s",
    "/c",
    "pnpm.cmd",
    "--filter",
    "@cinematic-story-studio/desktop",
    "run",
    "lint",
  ]);
  assert.throws(
    () => normalizedInvocation("pnpm.cmd", ["run", "lint & whoami"], "win32"),
    /untrusted/u,
  );
  assert.throws(
    () => normalizedInvocation("other.cmd", ["--version"], "win32"),
    /untrusted/u,
  );
});
