import { spawn } from "node:child_process";
import path from "node:path";

const MAX_CAPTURE_BYTES = 1024 * 1024;
const TRUSTED_CMD_ARGUMENT = /^[A-Za-z0-9@_./:=+,*-]+$/u;

export function platformCommand(command) {
  return process.platform === "win32" && command === "pnpm"
    ? "pnpm.cmd"
    : command;
}

export function normalizedInvocation(
  command,
  args,
  platform = process.platform,
) {
  if (platform !== "win32" || !command.toLowerCase().endsWith(".cmd")) {
    return { command, args };
  }
  if (
    command.toLowerCase() !== "pnpm.cmd" ||
    args.some((argument) => !TRUSTED_CMD_ARGUMENT.test(argument))
  ) {
    throw new Error(
      "Refusing to pass an untrusted command or argument through the Windows pnpm shim.",
    );
  }
  const commandInterpreter =
    process.env.ComSpec ??
    path.join(process.env.SystemRoot ?? "C:\\Windows", "System32", "cmd.exe");
  if (
    !path.win32.isAbsolute(commandInterpreter) ||
    path.win32.basename(commandInterpreter).toLowerCase() !== "cmd.exe"
  ) {
    throw new Error("The Windows command interpreter path is invalid.");
  }
  // Node 24 refuses to execute .cmd files directly. Only the fixed pnpm shim and
  // strictly allow-listed build arguments cross this adapter; application
  // subprocesses continue to launch directly without a shell.
  return {
    command: commandInterpreter,
    args: ["/d", "/s", "/c", command, ...args],
  };
}

export function run(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    const invocation = normalizedInvocation(command, args);
    const child = spawn(invocation.command, invocation.args, {
      cwd: options.cwd,
      env: options.env,
      shell: false,
      stdio: options.stdio ?? "inherit",
      windowsHide: true,
    });

    child.once("error", reject);
    child.once("exit", (code, signal) => {
      if (code === 0) {
        resolve();
        return;
      }

      const outcome = signal ? `signal ${signal}` : `exit code ${code ?? "unknown"}`;
      reject(new Error(`${options.label ?? command} failed with ${outcome}.`));
    });
  });
}

export function capture(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    const invocation = normalizedInvocation(command, args);
    const child = spawn(invocation.command, invocation.args, {
      cwd: options.cwd,
      env: options.env,
      shell: false,
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    });
    const stdout = [];
    const stderr = [];
    let capturedBytes = 0;
    let captureExceeded = false;
    let timedOut = false;
    const timeout =
      options.timeoutMs === undefined
        ? null
        : setTimeout(() => {
            timedOut = true;
            child.kill();
          }, options.timeoutMs);

    const collect = (destination) => (chunk) => {
      capturedBytes += chunk.length;
      if (capturedBytes > (options.maxBytes ?? MAX_CAPTURE_BYTES)) {
        captureExceeded = true;
        child.kill();
        return;
      }
      destination.push(chunk);
    };

    child.stdout.on("data", collect(stdout));
    child.stderr.on("data", collect(stderr));
    child.once("error", reject);
    child.once("exit", (code, signal) => {
      if (timeout) {
        clearTimeout(timeout);
      }
      if (timedOut) {
        reject(new Error(`${options.label ?? command} exceeded its time limit.`));
        return;
      }
      if (captureExceeded) {
        reject(new Error(`${options.label ?? command} produced too much output.`));
        return;
      }

      const result = {
        code,
        signal,
        stdout: Buffer.concat(stdout).toString("utf8"),
        stderr: Buffer.concat(stderr).toString("utf8"),
      };

      if (options.allowFailure || code === 0) {
        resolve(result);
        return;
      }

      const outcome = signal ? `signal ${signal}` : `exit code ${code ?? "unknown"}`;
      reject(new Error(`${options.label ?? command} failed with ${outcome}.`));
    });
  });
}
