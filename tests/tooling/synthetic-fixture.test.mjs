import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repositoryRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
);
const fixturePath = path.join(
  repositoryRoot,
  "fixtures",
  "synthetic-story",
  "sample-story.md",
);

test("canonical story fixture satisfies the public Phase 0 contract", async () => {
  const bytes = await readFile(fixturePath);
  const text = bytes.toString("utf8");

  assert.equal(bytes[0], "#".charCodeAt(0), "fixture must not contain a byte-order mark");
  assert.ok(bytes.length < 8 * 1024, "fixture must stay small enough for source review");
  assert.ok((text.match(/^# Chapter /gm) ?? []).length >= 2);
  assert.ok((text.match(/^## Scene /gm) ?? []).length >= 2);
  assert.match(text, /^---$/m);
  assert.match(text, /\bMira\b/);
  assert.match(text, /\bTovin\b/);
  assert.ok((text.match(/"/g) ?? []).length >= 8, "fixture needs at least four quoted lines");
  assert.match(text, /"The northern marker is awake again," Tovin said\./);
  assert.match(
    text,
    /"We can still turn back\."(?:\r?\n){2}Neither traveler claimed/,
  );
  assert.match(text, /Neither traveler claimed the warning/);

  const digest = createHash("sha256").update(bytes).digest("hex");
  assert.match(digest, /^[a-f0-9]{64}$/);
  assert.equal(Buffer.from(text, "utf8").compare(bytes), 0);
});
