import { rm } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

import { build as esbuild } from "esbuild";
import { build as viteBuild } from "vite";

const packageRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  ".."
);
const distDirectory = path.join(packageRoot, "dist");

await rm(distDirectory, { recursive: true, force: true });

await Promise.all([
  esbuild({
    entryPoints: [path.join(packageRoot, "src/main/main.ts")],
    outfile: path.join(distDirectory, "main/main.js"),
    bundle: true,
    platform: "node",
    format: "esm",
    target: "node22",
    external: ["electron"],
    sourcemap: false,
    minify: false
  }),
  esbuild({
    entryPoints: [path.join(packageRoot, "src/preload/preload.ts")],
    outfile: path.join(distDirectory, "preload/preload.cjs"),
    bundle: true,
    platform: "node",
    format: "cjs",
    target: "node22",
    external: ["electron"],
    sourcemap: false,
    minify: false
  }),
  viteBuild({
    configFile: path.join(packageRoot, "vite.config.ts")
  })
]);
