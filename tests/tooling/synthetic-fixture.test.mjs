import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  createAdversarialFixture,
  createSyntheticFixtures,
  decodeBase64Fixture,
  fixtureEvidence,
} from "../../fixtures/synthetic-story/generate-fixtures.mjs";

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
const syntheticFixtureDirectory = path.dirname(fixturePath);

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

test("Phase 1 source fixtures regenerate byte-for-byte from public text", async () => {
  const first = await createSyntheticFixtures();
  const second = await createSyntheticFixtures();
  assert.deepEqual(second, first);

  const committed = await Promise.all(
    ["docx", "epub", "pdf"].map(async (format) =>
      decodeBase64Fixture(
        await readFile(
          path.join(
            syntheticFixtureDirectory,
            `sample-story.${format}.base64`,
          ),
          "ascii",
        ),
      ),
    ),
  );
  assert.deepEqual(committed, [first.docx, first.epub, first.pdf]);

  assert.equal(first.docx.subarray(0, 4).toString("binary"), "PK\u0003\u0004");
  assert.equal(first.epub.subarray(0, 4).toString("binary"), "PK\u0003\u0004");
  assert.equal(first.pdf.subarray(0, 5).toString("ascii"), "%PDF-");
  assert.equal(first.txt.compare(await readFile(
    path.join(syntheticFixtureDirectory, "sample-story.txt"),
  )), 0);

  for (const bytes of [first.txt, first.docx, first.epub, first.pdf]) {
    assert.ok(bytes.length > 0);
    assert.ok(bytes.length < 32 * 1024);
    assert.match(fixtureEvidence(bytes).sha256, /^[a-f0-9]{64}$/u);
  }
});

test("synthetic adversarial packages stay compact and deterministic", () => {
  const cases = [
    "zip-traversal-docx",
    "excessive-docx-members",
    "high-compression-docx",
    "oversized-docx-member",
    "excessive-docx-expansion",
    "excessive-path-depth-docx",
    "malformed-docx",
    "malformed-epub",
    "epub-remote-reference",
    "epub-script",
    "encrypted-pdf",
    "image-only-pdf",
    "excessive-page-pdf",
    "truncated-pdf",
  ];

  for (const name of cases) {
    const first = createAdversarialFixture(name);
    const second = createAdversarialFixture(name);
    assert.deepEqual(second, first, name);
    assert.ok(first.length > 0, name);
    assert.ok(first.length <= 8 * 1024 * 1024, name);
  }

  assert.deepEqual(
    centralZipEntries(createAdversarialFixture("oversized-docx-member")),
    [
      {
        name: "[Content_Types].xml",
        declaredSize: 32 * 1024 * 1024 + 1,
      },
    ],
  );
  assert.equal(
    centralZipEntries(
      createAdversarialFixture("excessive-docx-expansion"),
    ).reduce((total, entry) => total + entry.declaredSize, 0),
    200 * 1024 * 1024 + 1,
  );
  assert.equal(
    centralZipEntries(
      createAdversarialFixture("excessive-path-depth-docx"),
    )[0].name.split("/").length,
    21,
  );

  assert.throws(
    () => decodeBase64Fixture("not base64"),
    /strict base64/u,
  );
  assert.throws(
    () => createAdversarialFixture("unknown"),
    /Unknown synthetic adversarial fixture/u,
  );
});

test("oversized adversarial input is exactly one byte above the service ceiling", () => {
  const oversized = createAdversarialFixture("oversized-input");

  assert.equal(oversized.length, 100 * 1024 * 1024 + 1);
  assert.equal(oversized[0], 0x41);
  assert.equal(oversized.at(-1), 0x41);
});

function centralZipEntries(bytes) {
  const entries = [];
  let offset = bytes.indexOf(
    Buffer.from([0x50, 0x4b, 0x01, 0x02]),
  );
  while (
    offset >= 0 &&
    bytes.readUInt32LE(offset) === 0x02014b50
  ) {
    const declaredSize = bytes.readUInt32LE(offset + 24);
    const nameLength = bytes.readUInt16LE(offset + 28);
    const extraLength = bytes.readUInt16LE(offset + 30);
    const commentLength = bytes.readUInt16LE(offset + 32);
    const nameStart = offset + 46;
    entries.push({
      name: bytes
        .subarray(nameStart, nameStart + nameLength)
        .toString("utf8"),
      declaredSize,
    });
    offset =
      nameStart + nameLength + extraLength + commentLength;
  }
  return entries;
}
