import { fileURLToPath, URL } from "node:url";

import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: {
      "@shared": fileURLToPath(new URL("./src/shared", import.meta.url)),
      "@renderer": fileURLToPath(new URL("./src/renderer", import.meta.url)),
      "@cinematic-story-studio/contracts/api": fileURLToPath(
        new URL("../../packages/contracts/src/api.ts", import.meta.url)
      ),
      "@cinematic-story-studio/contracts/domain": fileURLToPath(
        new URL("../../packages/contracts/src/domain.ts", import.meta.url)
      )
    }
  },
  test: {
    include: ["src/**/*.test.{ts,tsx}"],
    environment: "jsdom",
    setupFiles: ["./src/renderer/test/setup.ts"],
    css: true,
    restoreMocks: true,
    clearMocks: true,
    coverage: {
      reporter: ["text", "html"],
      reportsDirectory: "./coverage"
    }
  }
});
