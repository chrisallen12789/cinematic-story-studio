import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  createAdversarialFixture,
  createSyntheticScaleStory,
  createSyntheticFixtures,
  createSyntheticStoryExpectations,
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

test("canonical story fixture satisfies the public Phase 2 story-intelligence contract", async () => {
  const bytes = await readFile(fixturePath);
  const text = bytes.toString("utf8");

  assert.equal(bytes[0], "#".charCodeAt(0), "fixture must not contain a byte-order mark");
  assert.ok(bytes.length < 16 * 1024, "fixture must stay small enough for source review");
  assert.equal((text.match(/^# Chapter /gmu) ?? []).length, 3);
  assert.equal((text.match(/^## Scene /gmu) ?? []).length, 6);
  assert.equal((text.match(/^---$/gmu) ?? []).length, 5);
  for (const name of [
    "Mira",
    "Tovin",
    "Jun",
    "Nessa",
    "Ilya",
    "Orin",
    "Elian",
    "Sana",
  ]) {
    assert.match(text, new RegExp(`\\b${name}\\b`, "u"), name);
  }
  assert.match(text, /Captain Mira Vale/u);
  assert.match(text, /Captain Nessa\s+Quill/u);
  assert.match(text, /Archivist Quill/u);
  assert.match(text, /Inspector Ilya Maren/u);
  assert.match(text, /Keeper\s+Orin Dax/u);
  assert.match(text, /Dr\. Elian Sorell and Dr\. Sana Vey/u);
  assert.ok((text.match(/"/gu) ?? []).length >= 20);
  assert.match(text, /"The northern marker is awake again," Tovin said\./);
  assert.match(
    text,
    /"We can still turn back\."(?:\r?\n){2}No one claimed/,
  );
  assert.match(text, /Neither captain moved/u);
  assert.match(text, /Both doctors reached for it\. The speaker remained unresolved\./u);
  assert.match(text, /"KEEP THE LANTERN LIT\." The words were an instruction, not\s+a voice/u);
  assert.match(text, /Six years earlier/u);
  assert.match(text, /Back in the present, two hours before dawn/u);
  assert.match(text, /Three minutes later/u);
  assert.match(text, /Their strained partnership became\s+open distrust/u);
  assert.match(text, /Their distrust eased into a cautious alliance/u);
  assert.match(text, /anger gave way to\s+grief/u);
  assert.match(text, /Her grief became resolve/u);
  assert.match(text, /glass shattered on the stone/u);
  assert.match(text, /brass lantern hung unbroken from the tower hook/u);

  const digest = createHash("sha256").update(bytes).digest("hex");
  assert.match(digest, /^[a-f0-9]{64}$/);
  assert.equal(Buffer.from(text, "utf8").compare(bytes), 0);
});

test("Phase 2 source fixtures and expected offsets regenerate byte-for-byte", async () => {
  const first = await createSyntheticFixtures();
  const second = await createSyntheticFixtures();
  assert.deepEqual(second, first);
  const firstExpectations = await createSyntheticStoryExpectations();
  const secondExpectations = await createSyntheticStoryExpectations();
  assert.deepEqual(secondExpectations, firstExpectations);
  assert.deepEqual(
    JSON.parse(
      await readFile(
        path.join(
          syntheticFixtureDirectory,
          "sample-story.expected.json",
        ),
        "utf8",
      ),
    ),
    firstExpectations,
  );

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

  assert.deepEqual(firstExpectations.expectedCounts, {
    chapters: 3,
    scenes: 6,
    namedCharacters: 10,
    locations: 6,
    dialogueLines: 10,
    ambiguousDialogueLines: 4,
    povShifts: 1,
    continuityAnomalies: 1,
  });
  assert.equal(
    firstExpectations.canonicalText.sha256,
    fixtureEvidence(first.txt).sha256,
  );
  for (const format of ["docx", "epub", "pdf"]) {
    assert.deepEqual(
      firstExpectations.binaryFixtures[format],
      {
        encodedFileName: `sample-story.${format}.base64`,
        decodedFileName: `sample-story.${format}`,
        ...fixtureEvidence(first[format]),
      },
    );
  }

  const canonicalText = first.txt.toString("utf8");
  const expectedSpans = collectExpectedSpans(firstExpectations);
  assert.ok(expectedSpans.length >= 40);
  assert.equal(
    new Set(expectedSpans.map((span) => span.spanId)).size,
    expectedSpans.length,
    "expected span identifiers must be unique",
  );
  for (const span of expectedSpans) {
    assertExpectedSpan(canonicalText, span);
  }
});

test("scale story is generated at test time and exercises whole-book bounds", () => {
  const first = createSyntheticScaleStory();
  const second = createSyntheticScaleStory();

  assert.deepEqual(second, first);
  assert.ok(first.evidence.wordCount >= 100_000);
  assert.ok(first.evidence.sceneCount >= 400);
  assert.ok(first.evidence.dialogueLineCount >= 2_000);
  assert.ok(first.evidence.namedMentionCount >= 800);
  assert.ok(first.evidence.chapterCount >= 20);
  assert.ok(first.evidence.byteLength < 5 * 1024 * 1024);
  assert.equal(Buffer.byteLength(first.text, "utf8"), first.evidence.byteLength);
  assert.equal(
    createHash("sha256").update(first.text, "utf8").digest("hex"),
    first.evidence.sha256,
  );
  assert.match(first.text, /^Chapter 1: Deterministic Scale/mu);
  assert.match(first.text, /Scene 400: Generated Boundary/u);
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

function collectExpectedSpans(value, spans = []) {
  if (Array.isArray(value)) {
    for (const item of value) {
      collectExpectedSpans(item, spans);
    }
    return spans;
  }
  if (value !== null && typeof value === "object") {
    if (
      typeof value.spanId === "string" &&
      Number.isSafeInteger(value.startOffset) &&
      Number.isSafeInteger(value.endOffset) &&
      typeof value.textSha256 === "string"
    ) {
      spans.push(value);
      return spans;
    }
    for (const item of Object.values(value)) {
      collectExpectedSpans(item, spans);
    }
  }
  return spans;
}

function assertExpectedSpan(text, span) {
  assert.ok(span.startOffset >= 0, span.spanId);
  assert.ok(span.endOffset > span.startOffset, span.spanId);
  const exactText = [...text]
    .slice(span.startOffset, span.endOffset)
    .join("");
  if (Object.hasOwn(span, "exactText")) {
    assert.equal(exactText, span.exactText, span.spanId);
  }
  assert.equal(
    createHash("sha256").update(exactText, "utf8").digest("hex"),
    span.textSha256,
    span.spanId,
  );
}
