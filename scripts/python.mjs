import {
  ensureServiceVenv,
  installServiceDevelopmentDependencies,
  requireServicePython,
  runServicePython,
} from "./lib/python-runtime.mjs";

async function main() {
  const [operation, ...args] = process.argv.slice(2);

  switch (operation) {
    case "install":
      await installServiceDevelopmentDependencies();
      break;
    case "venv":
      await ensureServiceVenv();
      break;
    case "exec":
      if (args.length === 0) {
        throw new Error("Usage: node scripts/python.mjs exec <python arguments...>");
      }
      await runServicePython(args);
      break;
    case "path":
      process.stdout.write(`${await requireServicePython()}\n`);
      break;
    default:
      throw new Error(
        "Usage: node scripts/python.mjs <install|venv|exec|path> [arguments...]",
      );
  }
}

main().catch((error) => {
  process.stderr.write(`Python helper: ${error.message}\n`);
  process.exitCode = 1;
});
