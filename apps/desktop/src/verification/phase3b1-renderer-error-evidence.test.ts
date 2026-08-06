import { describe, expect, it } from "vitest";

import {
  Phase3b1RendererError,
  phase3b1RendererErrorCodeFromError,
  requirePhase3b1RendererErrorCode
} from "./phase3b1-renderer-error-evidence";

describe("Phase 3B.1 renderer error evidence", () => {
  it("retains only a bounded typed renderer error code", () => {
    const error = new Phase3b1RendererError("PROJECT_CONTEXT_CHANGED");

    expect(error.message).toBe(
      "Phase 3B.1 comparison playback was blocked by renderer error code PROJECT_CONTEXT_CHANGED."
    );
    expect(phase3b1RendererErrorCodeFromError(error)).toBe(
      "PROJECT_CONTEXT_CHANGED"
    );
    expect(phase3b1RendererErrorCodeFromError(new Error("unsafe"))).toBeNull();
    expect(
      phase3b1RendererErrorCodeFromError({
        rendererErrorCode: "PROJECT_CONTEXT_CHANGED"
      })
    ).toBeNull();
  });

  it.each([
    "",
    "project_context_changed",
    "PROJECT CONTEXT CHANGED",
    "PROJECT/CONTEXT/CHANGED",
    "C:\\private\\error",
    `A${"B".repeat(128)}`
  ])("rejects an unsafe code without echoing it", (value) => {
    expect(() => requirePhase3b1RendererErrorCode(value)).toThrow(
      "The Phase 3B.1 renderer error code was invalid."
    );
    try {
      requirePhase3b1RendererErrorCode(value);
    } catch (error) {
      expect(String(error)).toBe(
        "Error: The Phase 3B.1 renderer error code was invalid."
      );
    }
  });
});
