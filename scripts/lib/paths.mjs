import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptsDirectory = path.dirname(path.dirname(fileURLToPath(import.meta.url)));

export const repositoryRoot = path.dirname(scriptsDirectory);
export const desktopRoot = path.join(repositoryRoot, "apps", "desktop");
export const serviceRoot = path.join(repositoryRoot, "apps", "local-service");
export const serviceSourceRoot = path.join(serviceRoot, "src");
export const serviceVenvRoot = path.join(serviceRoot, ".venv");
export const servicePython = path.join(
  serviceVenvRoot,
  process.platform === "win32" ? "Scripts" : "bin",
  process.platform === "win32" ? "python.exe" : "python",
);

export function assertRepositoryChild(target, label = "path") {
  const relative = path.relative(repositoryRoot, path.resolve(target));
  if (
    relative === "" ||
    relative === ".." ||
    relative.startsWith(`..${path.sep}`) ||
    path.isAbsolute(relative)
  ) {
    throw new Error(`${label} must be a specific path inside the repository.`);
  }
}
