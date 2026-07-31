import { createHash } from "node:crypto";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { deflateRawSync } from "node:zlib";

const fixtureDirectory = path.dirname(fileURLToPath(import.meta.url));
const canonicalTextPath = path.join(fixtureDirectory, "sample-story.txt");
const markdownTextPath = path.join(fixtureDirectory, "sample-story.md");
const expectedAnalysisPath = path.join(
  fixtureDirectory,
  "sample-story.expected.json",
);
const ZIP_VERSION = 20;
const ZIP_UTF8_FLAG = 0x0800;
const ZIP_STORE = 0;
const ZIP_DEFLATE = 8;
const FIXED_DOS_DATE = 0x0021;
const FIXED_DOS_TIME = 0;
const PDF_HEADER = Buffer.from("%PDF-1.7\n%\xE2\xE3\xCF\xD3\n", "binary");

export async function createSyntheticFixtures() {
  const canonicalText = normalizeText(await readFile(canonicalTextPath, "utf8"));
  return {
    txt: Buffer.from(canonicalText, "utf8"),
    docx: createDocx(canonicalText),
    epub: createEpub(canonicalText),
    pdf: createTextPdf(canonicalText),
  };
}

export async function createSyntheticStoryExpectations() {
  const [canonicalTextValue, markdownBytes, fixtures] = await Promise.all([
    readFile(canonicalTextPath, "utf8"),
    readFile(markdownTextPath),
    createSyntheticFixtures(),
  ]);
  const canonicalText = normalizeText(canonicalTextValue);
  const markdownText = normalizeText(markdownBytes.toString("utf8"));
  const chapters = sectionSpans(canonicalText, /^Chapter /u, /^Chapter /u);
  const scenes = sectionSpans(
    canonicalText,
    /^Scene /u,
    /^(?:Chapter |Scene )/u,
  );
  const characters = [
    characterExpectation(
      canonicalText,
      "character-mira",
      "Mira Vale",
      ["Mira", "Captain Mira Vale", "Captain", "Captain Vale"],
      "Captain Mira Vale",
    ),
    characterExpectation(
      canonicalText,
      "character-tovin",
      "Tovin Rook",
      ["Tovin", "Mr. Rook", "Rook"],
      "Tovin Rook",
    ),
    characterExpectation(
      canonicalText,
      "character-jun",
      "Jun Pell",
      ["Jun", "Engineer Jun Pell", "Engineer Pell"],
      "Engineer Jun Pell",
    ),
    characterExpectation(
      canonicalText,
      "character-nessa",
      "Nessa Quill",
      ["Nessa", "Captain Nessa Quill", "Archivist Quill", "Captain"],
      "Captain Nessa\nQuill",
    ),
    characterExpectation(
      canonicalText,
      "character-ilya",
      "Ilya Maren",
      ["Ilya", "Inspector Ilya Maren", "Inspector Maren"],
      "Inspector Ilya Maren",
    ),
    characterExpectation(
      canonicalText,
      "character-orin",
      "Orin Dax",
      ["Orin", "Keeper Orin Dax", "Keeper Dax"],
      "Keeper\nOrin Dax",
    ),
    characterExpectation(
      canonicalText,
      "character-elian",
      "Elian Sorell",
      ["Elian", "Dr. Elian Sorell", "Dr. Sorell", "doctor"],
      "Dr. Elian Sorell",
    ),
    characterExpectation(
      canonicalText,
      "character-sana",
      "Sana Vey",
      ["Sana", "Dr. Sana Vey", "Doctor Vey", "doctor"],
      "Dr. Sana Vey",
    ),
    characterExpectation(
      canonicalText,
      "character-alex-west",
      "Alex Reed",
      ["Alex", "Reed"],
      "Alex Reed",
      1,
    ),
    characterExpectation(
      canonicalText,
      "character-alex-east",
      "Alex Reed",
      ["Alex", "Reed"],
      "Alex Reed",
      2,
    ),
  ];
  const dialogueLines = [
    dialogueExpectation(
      canonicalText,
      "dialogue-northern-marker",
      '"The northern marker is awake again,"',
      ["character-tovin"],
      "character-tovin",
      false,
    ),
    dialogueExpectation(
      canonicalText,
      "dialogue-remembered-us",
      '"Then it remembered us before we remembered it."',
      ["character-mira"],
      "character-mira",
      false,
    ),
    dialogueExpectation(
      canonicalText,
      "dialogue-turn-back",
      '"We can still turn back."',
      ["character-mira", "character-tovin"],
      null,
      true,
    ),
    dialogueExpectation(
      canonicalText,
      "dialogue-first-signal",
      '"Only one of us remembers the first signal."',
      ["character-mira", "character-nessa"],
      null,
      true,
    ),
    dialogueExpectation(
      canonicalText,
      "dialogue-west-dial",
      '"The west dial is clear,"',
      ["character-alex-west", "character-alex-east"],
      null,
      true,
    ),
    dialogueExpectation(
      canonicalText,
      "dialogue-hide-route",
      '"Hide the route until Mira is ready,"',
      ["character-nessa"],
      "character-nessa",
      false,
    ),
    dialogueExpectation(
      canonicalText,
      "dialogue-apology",
      '"I was wrong to call you a\nliar."',
      ["character-mira"],
      "character-mira",
      false,
    ),
    dialogueExpectation(
      canonicalText,
      "dialogue-right-to-ask",
      '"You were right to ask,"',
      ["character-tovin"],
      "character-tovin",
      false,
    ),
    dialogueExpectation(
      canonicalText,
      "dialogue-red-valve",
      '"Close the red valve,"',
      ["character-elian", "character-sana"],
      null,
      true,
    ),
    dialogueExpectation(
      canonicalText,
      "dialogue-tomorrow",
      '"Tomorrow, the northern line will carry passengers again,"',
      ["character-mira"],
      "character-mira",
      false,
    ),
  ];
  const quotedMaterial = exactSpan(
    canonicalText,
    "quoted-plaque-instruction",
    '"KEEP THE LANTERN LIT."',
  );
  return {
    schemaVersion: "2.0.0",
    fixtureId: "phase-2-whole-book-intelligence",
    offsetUnit: "unicode-code-point",
    canonicalText: textFileEvidence(
      "sample-story.txt",
      Buffer.from(canonicalText, "utf8"),
      canonicalText,
    ),
    markdown: textFileEvidence(
      "sample-story.md",
      Buffer.from(markdownText, "utf8"),
      markdownText,
    ),
    binaryFixtures: Object.fromEntries(
      ["docx", "epub", "pdf"].map((format) => [
        format,
        {
          encodedFileName: `sample-story.${format}.base64`,
          decodedFileName: `sample-story.${format}`,
          ...fixtureEvidence(fixtures[format]),
        },
      ]),
    ),
    expectedCounts: {
      chapters: 3,
      scenes: 6,
      namedCharacters: 10,
      locations: 6,
      dialogueLines: dialogueLines.length,
      ambiguousDialogueLines: dialogueLines.filter(
        (line) => line.requiresHumanReview,
      ).length,
      povShifts: 1,
      continuityAnomalies: 1,
    },
    chapters,
    scenes,
    characters,
    locations: [
      locationExpectation(
        canonicalText,
        "location-relay-platform",
        "Relay Platform",
        "crossed the Relay Platform",
      ),
      locationExpectation(
        canonicalText,
        "location-clock-room",
        "Clock Room",
        "into the Clock Room",
      ),
      locationExpectation(
        canonicalText,
        "location-archive-vault",
        "Archive Vault",
        "into the Archive Vault beneath",
      ),
      locationExpectation(
        canonicalText,
        "location-north-signal-tower",
        "North Signal Tower",
        "climbed the North Signal\nTower",
      ),
      locationExpectation(
        canonicalText,
        "location-flooded-concourse",
        "Flooded Concourse",
        "At the Flooded Concourse",
      ),
      locationExpectation(
        canonicalText,
        "location-dawn-switchyard",
        "Dawn Switchyard",
        "in the Dawn Switchyard",
      ),
    ],
    dialogueLines,
    narrationDistinction: {
      quotedMaterial: {
        ...quotedMaterial,
        expectedClassification: "quoted_material",
      },
      internalThought: {
        ...exactSpan(
          canonicalText,
          "internal-thought-mira",
          "Not again, Mira thought.",
        ),
        expectedClassification: "internal_thought",
        viewpointCharacterId: "character-mira",
      },
    },
    pointOfView: {
      initial: {
        ...exactSpan(
          canonicalText,
          "pov-mira",
          "Mira wished Tovin would admit why the northern marker frightened him.",
        ),
        viewpointCharacterId: "character-mira",
      },
      shifted: {
        ...exactSpan(
          canonicalText,
          "pov-tovin",
          "For the first time that night, only he noticed how every bell matched his\nheartbeat.",
        ),
        viewpointCharacterId: "character-tovin",
        expectedShiftKind: "scene_boundary",
      },
    },
    timeline: {
      flashback: {
        ...exactSpan(
          canonicalText,
          "timeline-flashback",
          "Six years earlier, Tovin had followed Nessa into the Archive Vault",
        ),
        expectedKind: "flashback",
      },
      returnToPresent: {
        ...exactSpan(
          canonicalText,
          "timeline-present-return",
          "Back in the present, two hours before dawn",
        ),
        expectedKind: "relative_time",
      },
      relativeTime: exactSpan(
        canonicalText,
        "timeline-three-minutes-later",
        "Three minutes later",
      ),
    },
    relationshipChanges: [
      {
        relationshipId: "relationship-mira-tovin",
        sourceCharacterId: "character-mira",
        targetCharacterId: "character-tovin",
        before: exactSpan(
          canonicalText,
          "relationship-distrust",
          "Their strained partnership became\nopen distrust.",
        ),
        after: exactSpan(
          canonicalText,
          "relationship-alliance",
          "Their distrust eased into a cautious alliance.",
        ),
        expectedChange: "reversed",
      },
      {
        relationshipId: "relationship-mira-nessa",
        sourceCharacterId: "character-mira",
        targetCharacterId: "character-nessa",
        before: exactSpan(
          canonicalText,
          "relationship-old-rivalry",
          "Nessa abandoned their old rivalry to help.",
        ),
        after: exactSpan(
          canonicalText,
          "relationship-trust",
          "What began as rivalry ended as trust.",
        ),
        expectedChange: "reversed",
      },
    ],
    emotionalProgression: {
      characterId: "character-mira",
      states: [
        {
          emotion: "anger",
          evidence: exactSpan(
            canonicalText,
            "emotion-anger",
            "anger carried Mira through the black water",
          ),
        },
        {
          emotion: "grief",
          evidence: exactSpan(
            canonicalText,
            "emotion-grief",
            "anger gave way to\ngrief",
          ),
        },
        {
          emotion: "resolve",
          evidence: exactSpan(
            canonicalText,
            "emotion-resolve",
            "Her grief became resolve",
          ),
        },
      ],
    },
    continuityAnomaly: {
      findingId: "continuity-broken-lantern",
      expectedCategory: "unexplained_object_state_change",
      priorState: exactSpan(
        canonicalText,
        "lantern-broken",
        "The brass lantern slipped from Jun's hand. Its glass shattered on the stone.\nMira left the broken frame beside the backward clock.",
      ),
      conflictingState: exactSpan(
        canonicalText,
        "lantern-unbroken",
        "The same brass lantern hung unbroken from the tower hook, its glass clean and\nits flame steady.",
      ),
    },
  };
}

export function createSyntheticScaleStory({
  chapterCount = 20,
  scenesPerChapter = 20,
  dialogueLinesPerScene = 5,
  narrationWordsPerScene = 240,
} = {}) {
  if (
    !Number.isSafeInteger(chapterCount) ||
    !Number.isSafeInteger(scenesPerChapter) ||
    !Number.isSafeInteger(dialogueLinesPerScene) ||
    !Number.isSafeInteger(narrationWordsPerScene) ||
    chapterCount < 1 ||
    scenesPerChapter < 1 ||
    dialogueLinesPerScene < 1 ||
    narrationWordsPerScene < 1
  ) {
    throw new Error("Synthetic scale-story dimensions are invalid.");
  }
  const names = ["Mira", "Tovin", "Jun", "Nessa", "Ilya", "Orin", "Elian", "Sana"];
  const vocabulary = [
    "relay",
    "signal",
    "lantern",
    "platform",
    "archive",
    "clock",
    "tower",
    "route",
    ...names,
  ];
  const chunks = [];
  let sceneOrdinal = 0;
  for (let chapter = 1; chapter <= chapterCount; chapter += 1) {
    chunks.push(`Chapter ${chapter}: Deterministic Scale`);
    for (let scene = 1; scene <= scenesPerChapter; scene += 1) {
      sceneOrdinal += 1;
      chunks.push(`Scene ${sceneOrdinal}: Generated Boundary`);
      const narration = Array.from(
        { length: narrationWordsPerScene },
        (_, index) => vocabulary[(sceneOrdinal + index) % vocabulary.length],
      ).join(" ");
      chunks.push(`${narration}.`);
      for (let line = 0; line < dialogueLinesPerScene; line += 1) {
        const speaker = names[(sceneOrdinal + line) % names.length];
        chunks.push(
          `"Synthetic dialogue ${sceneOrdinal}-${line + 1} keeps the relay deterministic," ${speaker} said.`,
        );
      }
    }
  }
  const text = `${chunks.join("\n\n")}\n`;
  const wordCount = (text.match(/\b[\p{L}\p{N}-]+\b/gu) ?? []).length;
  return {
    text,
    evidence: {
      chapterCount,
      sceneCount: chapterCount * scenesPerChapter,
      dialogueLineCount:
        chapterCount * scenesPerChapter * dialogueLinesPerScene,
      namedMentionCount: names.reduce(
        (count, name) =>
          count + (text.match(new RegExp(`\\b${name}\\b`, "gu")) ?? []).length,
        0,
      ),
      wordCount,
      byteLength: Buffer.byteLength(text, "utf8"),
      sha256: createHash("sha256").update(text, "utf8").digest("hex"),
    },
  };
}

export function createAdversarialFixture(name) {
  switch (name) {
    case "zip-traversal-docx":
      return createZip([
        zipEntry("[Content_Types].xml", "<Types/>"),
        zipEntry("../escape.xml", "<escape/>"),
      ]);
    case "excessive-docx-members":
      return createZip(
        Array.from({ length: 2049 }, (_, index) =>
          zipEntry(`word/member-${String(index).padStart(4, "0")}.xml`, "<p/>"),
        ),
      );
    case "high-compression-docx":
      return createZip([
        zipEntry("[Content_Types].xml", "<Types/>"),
        zipEntry("word/document.xml", "A".repeat(1024 * 1024)),
      ]);
    case "oversized-docx-member":
      return createZip([
        zipEntry("[Content_Types].xml", "", {
          declaredSize: 32 * 1024 * 1024 + 1,
        }),
      ]);
    case "excessive-docx-expansion":
      return createZip([
        ...Array.from({ length: 6 }, (_, index) =>
          zipEntry(`word/expanded-${index}.xml`, "", {
            declaredSize: 32 * 1024 * 1024,
            declaredCompressedSize:
              Math.ceil((32 * 1024 * 1024) / 100),
          }),
        ),
        zipEntry("word/expanded-final.xml", "", {
          declaredSize: 8 * 1024 * 1024 + 1,
          declaredCompressedSize:
            Math.ceil((8 * 1024 * 1024 + 1) / 100),
        }),
      ]);
    case "excessive-path-depth-docx":
      return createZip([
        zipEntry(
          `${Array.from({ length: 20 }, () => "nested").join("/")}/document.xml`,
          "<document/>",
        ),
      ]);
    case "malformed-docx":
      return createZip([
        zipEntry("[Content_Types].xml", "<Types>"),
        zipEntry("word/document.xml", "<w:document>"),
      ]);
    case "malformed-epub":
      return createZip([
        zipEntry("mimetype", "application/not-epub", { stored: true }),
        zipEntry("META-INF/container.xml", "<container>"),
      ]);
    case "epub-remote-reference":
      return createEpub("Remote reference fixture.", {
        chapterExtra: '<img src="https://example.invalid/never-fetch.png" alt="remote"/>',
      });
    case "epub-script":
      return createEpub("Script fixture.", {
        chapterExtra: "<script>throw new Error('must never execute')</script>",
      });
    case "encrypted-pdf":
      return createEncryptedMarkerPdf();
    case "image-only-pdf":
      return createImageOnlyPdf();
    case "excessive-page-pdf":
      return createBlankPdf(2001);
    case "truncated-pdf": {
      const valid = createTextPdf("Truncated fixture.");
      return valid.subarray(0, valid.length - 24);
    }
    case "oversized-input":
      return Buffer.alloc(100 * 1024 * 1024 + 1, 0x41);
    default:
      throw new Error(`Unknown synthetic adversarial fixture: ${name}`);
  }
}

export function decodeBase64Fixture(text) {
  if (
    typeof text !== "string" ||
    text.length === 0 ||
    text.includes("\0") ||
    !/^(?:[A-Za-z0-9+/]{4}|\s)*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?\s*$/u.test(
      text,
    )
  ) {
    throw new Error("The committed fixture is not strict base64.");
  }
  const compact = text.replace(/\s/gu, "");
  const decoded = Buffer.from(compact, "base64");
  if (decoded.toString("base64") !== compact) {
    throw new Error("The committed fixture base64 is not canonical.");
  }
  return decoded;
}

export function fixtureEvidence(bytes) {
  return {
    sizeBytes: bytes.length,
    sha256: createHash("sha256").update(bytes).digest("hex"),
  };
}

function textFileEvidence(fileName, bytes, text) {
  return {
    fileName,
    byteLength: bytes.length,
    codePointLength: [...text].length,
    sha256: createHash("sha256").update(bytes).digest("hex"),
  };
}

function characterExpectation(
  text,
  characterId,
  canonicalName,
  aliases,
  firstMention,
  occurrence,
) {
  return {
    characterId,
    canonicalName,
    aliases,
    firstMention: exactSpan(
      text,
      `${characterId}-first-mention`,
      firstMention,
      occurrence ?? 1,
      occurrence !== undefined,
    ),
  };
}

function locationExpectation(text, locationId, canonicalName, firstMention) {
  return {
    locationId,
    canonicalName,
    firstMention: exactSpan(
      text,
      `${locationId}-first-mention`,
      firstMention,
    ),
  };
}

function dialogueExpectation(
  text,
  dialogueLineId,
  exactText,
  candidateCharacterIds,
  effectiveSpeakerId,
  requiresHumanReview,
) {
  return {
    dialogueLineId,
    exactText: exactSpan(text, dialogueLineId, exactText),
    distinction: "spoken_dialogue",
    candidateCharacterIds,
    effectiveSpeakerId,
    requiresHumanReview,
  };
}

function sectionSpans(text, startPattern, boundaryPattern) {
  const headings = [
    ...text.matchAll(/^(?:Chapter |Scene ).+$/gmu),
  ].map((match) => ({
    index: match.index,
    title: match[0],
  }));
  const starts = headings.filter((heading) =>
    startPattern.test(heading.title),
  );
  return starts.map((heading, ordinal) => {
    const nextBoundary = headings.find(
      (candidate) =>
        candidate.index > heading.index &&
        boundaryPattern.test(candidate.title),
    );
    const endIndex = nextBoundary?.index ?? text.length;
    const sourceText = text.slice(heading.index, endIndex);
    const completeSpan = spanFromUtf16Offsets(
      text,
      `${heading.title.startsWith("Chapter ") ? "chapter" : "scene"}-${ordinal + 1}`,
      heading.index,
      endIndex,
      sourceText,
    );
    const { exactText: _exactText, ...span } = completeSpan;
    return {
      [`${heading.title.startsWith("Chapter ") ? "chapter" : "scene"}Id`]:
        span.spanId,
      ordinal,
      title: heading.title,
      span,
    };
  });
}

function exactSpan(
  text,
  spanId,
  needle,
  occurrence = 1,
  allowAdditional = false,
) {
  if (
    typeof needle !== "string" ||
    needle.length === 0 ||
    !Number.isSafeInteger(occurrence) ||
    occurrence < 1
  ) {
    throw new Error(`Invalid expected span ${spanId}.`);
  }
  let startIndex = -1;
  let searchIndex = 0;
  for (let index = 0; index < occurrence; index += 1) {
    startIndex = text.indexOf(needle, searchIndex);
    if (startIndex < 0) {
      throw new Error(`Missing expected synthetic-story span: ${spanId}.`);
    }
    searchIndex = startIndex + needle.length;
  }
  if (
    text.indexOf(needle, searchIndex) >= 0 &&
    occurrence === 1 &&
    !allowAdditional
  ) {
    throw new Error(`Expected synthetic-story span is not unique: ${spanId}.`);
  }
  return spanFromUtf16Offsets(
    text,
    spanId,
    startIndex,
    startIndex + needle.length,
    needle,
  );
}

function spanFromUtf16Offsets(text, spanId, startIndex, endIndex, exactText) {
  return {
    spanId,
    startOffset: [...text.slice(0, startIndex)].length,
    endOffset: [...text.slice(0, endIndex)].length,
    exactText,
    textSha256: createHash("sha256")
      .update(exactText, "utf8")
      .digest("hex"),
  };
}

function createDocx(canonicalText) {
  const paragraphs = canonicalText
    .split("\n\n")
    .map((paragraph) => paragraph.replace(/\n/gu, " "));
  const body = paragraphs
    .map((paragraph) => {
      const style = paragraph.startsWith("Chapter ")
        ? '<w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
        : paragraph.startsWith("Scene ")
          ? '<w:pPr><w:pStyle w:val="Heading2"/></w:pPr>'
          : "";
      return `<w:p>${style}<w:r><w:t xml:space="preserve">${escapeXml(paragraph)}</w:t></w:r></w:p>`;
    })
    .join("");
  const contentTypes = xml(
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">' +
      '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>' +
      '<Default Extension="xml" ContentType="application/xml"/>' +
      '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>' +
      "</Types>",
  );
  const relationships = xml(
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
      '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>' +
      "</Relationships>",
  );
  const document = xml(
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">' +
      `<w:body>${body}<w:sectPr/></w:body>` +
      "</w:document>",
  );
  return createZip([
    zipEntry("[Content_Types].xml", contentTypes),
    zipEntry("_rels/.rels", relationships),
    zipEntry("word/document.xml", document),
  ]);
}

function createEpub(canonicalText, { chapterExtra = "" } = {}) {
  const chapters = splitChapters(canonicalText);
  const chapterEntries = chapters.map((chapter, index) => {
    const ordinal = index + 1;
    const body = chapter.paragraphs
      .map((paragraph, paragraphIndex) => {
        const tag = paragraphIndex === 0 ? "h1" : paragraph.startsWith("Scene ") ? "h2" : "p";
        return `<${tag}>${escapeXml(paragraph.replace(/\n/gu, " "))}</${tag}>`;
      })
      .join("");
    return zipEntry(
      `OEBPS/chapter-${ordinal}.xhtml`,
      xml(
        '<html xmlns="http://www.w3.org/1999/xhtml"><head>' +
          `<title>${escapeXml(chapter.title)}</title>` +
          `</head><body>${body}${chapterExtra}</body></html>`,
      ),
    );
  });
  const manifest = chapters
    .map(
      (_, index) =>
        `<item id="chapter-${index + 1}" href="chapter-${index + 1}.xhtml" media-type="application/xhtml+xml"/>`,
    )
    .join("");
  const spine = chapters
    .map((_, index) => `<itemref idref="chapter-${index + 1}"/>`)
    .join("");
  return createZip([
    zipEntry("mimetype", "application/epub+zip", { stored: true }),
    zipEntry(
      "META-INF/container.xml",
      xml(
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">' +
          '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>' +
          "</container>",
      ),
    ),
    zipEntry(
      "OEBPS/content.opf",
      xml(
        '<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="book-id" version="3.0">' +
          '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">' +
          '<dc:identifier id="book-id">urn:css:synthetic-story</dc:identifier>' +
          "<dc:title>Cinematic Story Studio Synthetic Story</dc:title>" +
          "<dc:language>en</dc:language>" +
          '<meta property="dcterms:modified">2026-01-01T00:00:00Z</meta>' +
          `</metadata><manifest>${manifest}</manifest><spine>${spine}</spine></package>`,
      ),
    ),
    ...chapterEntries,
  ]);
}

function createTextPdf(canonicalText) {
  const chapters = splitChapters(canonicalText);
  const pageStreams = chapters.map((chapter) => {
    const lines = chapter.paragraphs.flatMap((paragraph) =>
      wrapAscii(paragraph.replace(/\n/gu, " "), 84),
    );
    return textContentStream(lines);
  });
  return createPdf(pageStreams.map((stream) => ({ stream, resources: "<< /Font << /F1 3 0 R >> >>" })));
}

function createBlankPdf(pageCount) {
  return createPdf(
    Array.from({ length: pageCount }, () => ({
      stream: Buffer.from("", "ascii"),
      resources: "<< >>",
    })),
  );
}

function createImageOnlyPdf() {
  return createPdf(
    [
      {
        stream: Buffer.from("q 72 0 0 72 72 648 cm /Im1 Do Q\n", "ascii"),
        resources: "<< /XObject << /Im1 IMAGE_OBJECT_REFERENCE >> >>",
      },
    ],
    {
      imageObject:
        "<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceGray /BitsPerComponent 8 /Length 1 >>\nstream\n\x7f\nendstream",
    },
  );
}

function createEncryptedMarkerPdf() {
  return createPdf(
    [{ stream: textContentStream(["Encrypted synthetic fixture."]), resources: "<< /Font << /F1 3 0 R >> >>" }],
    {
      encryptDictionary:
        "<< /Filter /Standard /V 1 /R 2 /Length 40 /O <0000000000000000000000000000000000000000000000000000000000000000> /U <0000000000000000000000000000000000000000000000000000000000000000> /P -4 >>",
    },
  );
}

function createPdf(pages, { imageObject = null, encryptDictionary = null } = {}) {
  const objects = [];
  const addObject = (value) => {
    objects.push(Buffer.isBuffer(value) ? value : Buffer.from(value, "binary"));
    return objects.length;
  };
  const catalogId = addObject("");
  const pagesId = addObject("");
  const fontId = addObject("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>");
  const imageId = imageObject === null ? null : addObject(imageObject);
  const pageIds = [];
  for (const page of pages) {
    const contentId = addObject(streamObject(page.stream));
    const resources =
      imageId === null
        ? page.resources
        : page.resources.replace("IMAGE_OBJECT_REFERENCE", `${imageId} 0 R`);
    const pageId = addObject(
      `<< /Type /Page /Parent ${pagesId} 0 R /MediaBox [0 0 612 792] /Resources ${resources} /Contents ${contentId} 0 R >>`,
    );
    pageIds.push(pageId);
  }
  const encryptId = encryptDictionary === null ? null : addObject(encryptDictionary);
  objects[catalogId - 1] = Buffer.from(`<< /Type /Catalog /Pages ${pagesId} 0 R >>`, "ascii");
  objects[pagesId - 1] = Buffer.from(
    `<< /Type /Pages /Count ${pageIds.length} /Kids [${pageIds.map((id) => `${id} 0 R`).join(" ")}] >>`,
    "ascii",
  );

  const chunks = [PDF_HEADER];
  const offsets = [0];
  let offset = PDF_HEADER.length;
  for (let index = 0; index < objects.length; index += 1) {
    offsets.push(offset);
    const object = Buffer.concat([
      Buffer.from(`${index + 1} 0 obj\n`, "ascii"),
      objects[index],
      Buffer.from("\nendobj\n", "ascii"),
    ]);
    chunks.push(object);
    offset += object.length;
  }
  const xrefOffset = offset;
  const xref = [
    `xref\n0 ${objects.length + 1}\n`,
    "0000000000 65535 f \n",
    ...offsets.slice(1).map((value) => `${String(value).padStart(10, "0")} 00000 n \n`),
  ].join("");
  const encryptTrailer = encryptId === null ? "" : ` /Encrypt ${encryptId} 0 R`;
  const trailer =
    `${xref}trailer\n<< /Size ${objects.length + 1} /Root ${catalogId} 0 R${encryptTrailer} ` +
    "/ID [<0123456789abcdef0123456789abcdef><0123456789abcdef0123456789abcdef>] >>\n" +
    `startxref\n${xrefOffset}\n%%EOF\n`;
  chunks.push(Buffer.from(trailer, "ascii"));
  return Buffer.concat(chunks);
}

function textContentStream(lines) {
  const commands = ["BT", "/F1 10 Tf", "12 TL", "54 738 Td"];
  for (const [index, line] of lines.entries()) {
    if (index > 0) {
      commands.push("T*");
    }
    commands.push(`(${escapePdfText(line)}) Tj`);
  }
  commands.push("ET", "");
  return Buffer.from(commands.join("\n"), "ascii");
}

function streamObject(bytes) {
  return Buffer.concat([
    Buffer.from(`<< /Length ${bytes.length} >>\nstream\n`, "ascii"),
    bytes,
    Buffer.from("endstream", "ascii"),
  ]);
}

function createZip(entries) {
  const localChunks = [];
  const centralChunks = [];
  let offset = 0;
  for (const entry of entries) {
    const name = Buffer.from(entry.name, "utf8");
    const source = Buffer.isBuffer(entry.bytes) ? entry.bytes : Buffer.from(entry.bytes, "utf8");
    const method = entry.stored ? ZIP_STORE : ZIP_DEFLATE;
    const compressed =
      method === ZIP_STORE ? source : deflateRawSync(source, { level: 9 });
    const checksum = crc32(source);
    const localHeader = Buffer.alloc(30);
    localHeader.writeUInt32LE(0x04034b50, 0);
    localHeader.writeUInt16LE(ZIP_VERSION, 4);
    localHeader.writeUInt16LE(ZIP_UTF8_FLAG, 6);
    localHeader.writeUInt16LE(method, 8);
    localHeader.writeUInt16LE(FIXED_DOS_TIME, 10);
    localHeader.writeUInt16LE(FIXED_DOS_DATE, 12);
    localHeader.writeUInt32LE(checksum, 14);
    localHeader.writeUInt32LE(
      entry.declaredCompressedSize ?? compressed.length,
      18,
    );
    localHeader.writeUInt32LE(entry.declaredSize ?? source.length, 22);
    localHeader.writeUInt16LE(name.length, 26);
    const localRecord = Buffer.concat([localHeader, name, compressed]);
    localChunks.push(localRecord);

    const centralHeader = Buffer.alloc(46);
    centralHeader.writeUInt32LE(0x02014b50, 0);
    centralHeader.writeUInt16LE(0x0314, 4);
    centralHeader.writeUInt16LE(ZIP_VERSION, 6);
    centralHeader.writeUInt16LE(ZIP_UTF8_FLAG, 8);
    centralHeader.writeUInt16LE(method, 10);
    centralHeader.writeUInt16LE(FIXED_DOS_TIME, 12);
    centralHeader.writeUInt16LE(FIXED_DOS_DATE, 14);
    centralHeader.writeUInt32LE(checksum, 16);
    centralHeader.writeUInt32LE(
      entry.declaredCompressedSize ?? compressed.length,
      20,
    );
    centralHeader.writeUInt32LE(entry.declaredSize ?? source.length, 24);
    centralHeader.writeUInt16LE(name.length, 28);
    centralHeader.writeUInt32LE(0, 38);
    centralHeader.writeUInt32LE(offset, 42);
    centralChunks.push(Buffer.concat([centralHeader, name]));
    offset += localRecord.length;
  }
  const centralDirectory = Buffer.concat(centralChunks);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(entries.length, 8);
  end.writeUInt16LE(entries.length, 10);
  end.writeUInt32LE(centralDirectory.length, 12);
  end.writeUInt32LE(offset, 16);
  return Buffer.concat([...localChunks, centralDirectory, end]);
}

function zipEntry(
  name,
  bytes,
  {
    stored = false,
    declaredSize = null,
    declaredCompressedSize = null,
  } = {},
) {
  return {
    name,
    bytes,
    stored,
    declaredSize,
    declaredCompressedSize,
  };
}

function splitChapters(text) {
  const paragraphs = text.split("\n\n");
  const chapters = [];
  for (const paragraph of paragraphs) {
    if (paragraph.startsWith("Chapter ")) {
      chapters.push({ title: paragraph, paragraphs: [paragraph] });
    } else {
      chapters.at(-1)?.paragraphs.push(paragraph);
    }
  }
  return chapters;
}

function wrapAscii(text, width) {
  const words = text.split(/\s+/u);
  const lines = [];
  let line = "";
  for (const word of words) {
    if (line.length > 0 && line.length + word.length + 1 > width) {
      lines.push(line);
      line = word;
    } else {
      line = line.length === 0 ? word : `${line} ${word}`;
    }
  }
  if (line.length > 0) {
    lines.push(line);
  }
  return lines;
}

function escapeXml(value) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function escapePdfText(value) {
  return value.replace(/([\\()])/gu, "\\$1");
}

function normalizeText(value) {
  return `${value.replace(/\r\n?/gu, "\n").trimEnd()}\n`;
}

function xml(body) {
  return `<?xml version="1.0" encoding="UTF-8"?>${body}`;
}

function crc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) {
      crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function wrapBase64(bytes) {
  return `${bytes.toString("base64").match(/.{1,76}/gu).join("\n")}\n`;
}

async function main() {
  const [command, argument] = process.argv.slice(2);
  const fixtures = await createSyntheticFixtures();
  if (command === "--print-base64" && ["docx", "epub", "pdf"].includes(argument)) {
    process.stdout.write(wrapBase64(fixtures[argument]));
    return;
  }
  if (command === "--write-directory" && typeof argument === "string" && argument.length > 0) {
    const outputDirectory = path.resolve(argument);
    await mkdir(outputDirectory, { recursive: true });
    await Promise.all([
      writeFile(path.join(outputDirectory, "sample-story.txt"), fixtures.txt),
      writeFile(path.join(outputDirectory, "sample-story.docx"), fixtures.docx),
      writeFile(path.join(outputDirectory, "sample-story.epub"), fixtures.epub),
      writeFile(path.join(outputDirectory, "sample-story.pdf"), fixtures.pdf),
    ]);
    return;
  }
  if (command === "--print-expectations" && argument === undefined) {
    const expectations = await createSyntheticStoryExpectations();
    process.stdout.write(`${JSON.stringify(expectations, null, 2)}\n`);
    return;
  }
  if (command === "--write-committed" && argument === undefined) {
    const expectations = await createSyntheticStoryExpectations();
    await Promise.all([
      ...["docx", "epub", "pdf"].map((format) =>
        writeFile(
          path.join(fixtureDirectory, `sample-story.${format}.base64`),
          wrapBase64(fixtures[format]),
          "ascii",
        ),
      ),
      writeFile(
        expectedAnalysisPath,
        `${JSON.stringify(expectations, null, 2)}\n`,
        "utf8",
      ),
    ]);
    return;
  }
  throw new Error(
    "Usage: node generate-fixtures.mjs --print-base64 <docx|epub|pdf> | --write-directory <path> | --print-expectations | --write-committed",
  );
}

if (
  process.argv[1] !== undefined &&
  path.resolve(process.argv[1]) === path.resolve(fileURLToPath(import.meta.url))
) {
  main().catch(() => {
    process.stderr.write("Synthetic fixture generation failed.\n");
    process.exitCode = 1;
  });
}
