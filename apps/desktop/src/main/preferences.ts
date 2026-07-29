import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";

import { parseIdentifier } from "./validation.js";

interface SafePreferences {
  readonly recentProjectId?: string;
}

const MAX_PREFERENCES_BYTES = 4_096;

export class PreferenceStore {
  readonly #filePath: string;

  constructor(userDataPath: string) {
    this.#filePath = path.join(userDataPath, "desktop-preferences.json");
  }

  async getRecentProjectId(): Promise<string | null> {
    try {
      const content = await readFile(this.#filePath);
      if (content.byteLength > MAX_PREFERENCES_BYTES) {
        return null;
      }
      const parsed = JSON.parse(content.toString("utf8")) as unknown;
      if (
        parsed === null ||
        typeof parsed !== "object" ||
        Array.isArray(parsed)
      ) {
        return null;
      }
      const value = (parsed as SafePreferences).recentProjectId;
      if (value === undefined) {
        return null;
      }
      return parseIdentifier(value, "recentProjectId");
    } catch {
      return null;
    }
  }

  async setRecentProjectId(projectId: string | null): Promise<void> {
    const preferences: SafePreferences =
      projectId === null
        ? {}
        : { recentProjectId: parseIdentifier(projectId, "recentProjectId") };
    await writeFile(
      this.#filePath,
      `${JSON.stringify(preferences)}\n`,
      {
        encoding: "utf8",
        flag: "w",
        mode: 0o600
      }
    );
  }
}
