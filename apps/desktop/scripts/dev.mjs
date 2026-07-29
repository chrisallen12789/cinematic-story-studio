import { spawn } from "node:child_process";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

import electron from "electron";
import { context as esbuildContext } from "esbuild";

const packageRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  ".."
);
const viteExecutable = path.join(
  packageRoot,
  "node_modules",
  "vite",
  "bin",
  "vite.js"
);
const developmentUrl = "http://127.0.0.1:5173";
const children = new Set();
let shuttingDown = false;

const mainContext = await esbuildContext({
  entryPoints: [path.join(packageRoot, "src/main/main.ts")],
  outfile: path.join(packageRoot, "dist/main/main.js"),
  bundle: true,
  platform: "node",
  format: "esm",
  target: "node22",
  external: ["electron"],
  sourcemap: "inline"
});
const preloadContext = await esbuildContext({
  entryPoints: [path.join(packageRoot, "src/preload/preload.ts")],
  outfile: path.join(packageRoot, "dist/preload/preload.cjs"),
  bundle: true,
  platform: "node",
  format: "cjs",
  target: "node22",
  external: ["electron"],
  sourcemap: "inline"
});

await Promise.all([mainContext.watch(), preloadContext.watch()]);

const vite = spawn(process.execPath, [viteExecutable, "--config", "vite.config.ts"], {
  cwd: packageRoot,
  env: process.env,
  shell: false,
  stdio: "inherit",
  windowsHide: true
});
children.add(vite);

await waitForPort(5173, "127.0.0.1", 20_000);

const desktop = spawn(electron, ["."], {
  cwd: packageRoot,
  env: {
    ...process.env,
    CSS_DESKTOP_DEV_URL: developmentUrl
  },
  shell: false,
  stdio: "inherit",
  windowsHide: true
});
children.add(desktop);

desktop.once("exit", (code) => {
  void shutdown(code ?? 0);
});
vite.once("exit", (code) => {
  if (!shuttingDown) {
    void shutdown(code ?? 1);
  }
});

process.once("SIGINT", () => {
  void shutdown(130);
});
process.once("SIGTERM", () => {
  void shutdown(143);
});

async function shutdown(code) {
  if (shuttingDown) {
    return;
  }
  shuttingDown = true;
  for (const child of children) {
    if (child.exitCode === null) {
      child.kill();
    }
  }
  await Promise.allSettled([mainContext.dispose(), preloadContext.dispose()]);
  process.exitCode = code;
}

async function waitForPort(port, host, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await canConnect(port, host)) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`Vite did not listen on ${host}:${port} within ${timeoutMs}ms.`);
}

function canConnect(port, host) {
  return new Promise((resolve) => {
    const socket = net.createConnection({ port, host });
    socket.setTimeout(250);
    socket.once("connect", () => {
      socket.destroy();
      resolve(true);
    });
    socket.once("timeout", () => {
      socket.destroy();
      resolve(false);
    });
    socket.once("error", () => {
      resolve(false);
    });
  });
}
