// @vitest-environment node

import { readFile } from "node:fs/promises";

import { describe, expect, it } from "vitest";

import { rendererContentSecurityPolicy } from "./security-policy";

describe("renderer content security policy", () => {
  it.each([true, false])(
    "permits only local blob media in the packaged=%s policy",
    (isPackaged) => {
      const policy = rendererContentSecurityPolicy(isPackaged);
      expect(policy).toContain("media-src blob:");
      expect(policy).not.toMatch(/media-src [^;]*(?:https?:|data:|'self')/u);
      expect(policy).toContain("object-src 'none'");
      expect(policy).toContain("frame-src 'none'");
    }
  );

  it("keeps the development HTML bootstrap policy aligned for blob playback", async () => {
    const html = await readFile(
      new URL("../renderer/index.html", import.meta.url),
      "utf8"
    );
    expect(html).toContain("media-src blob:");
    expect(html).not.toMatch(/media-src [^;]*(?:https?:|data:|'self')/u);
  });
});
