import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const testDirectory = path.dirname(fileURLToPath(import.meta.url));
const versionDirectory = path.resolve(testDirectory, "..", "v1");
const definitionsPath = path.join(versionDirectory, "definitions.schema.json");

const entityEntries = {
  Project: "project.schema.json",
  ImportedStory: "imported-story.schema.json",
  SourceDocument: "source-document.schema.json",
  DocumentProbe: "document-probe.schema.json",
  DocumentExtractionResult:
    "document-extraction-result.schema.json",
  ImportManifest: "import-manifest.schema.json",
  ParserExecutionRecord: "parser-execution-record.schema.json",
  ExtractedSection: "extracted-section.schema.json",
  SourceLocation: "source-location.schema.json",
  ExtractionWarning: "extraction-warning.schema.json",
  IngestImportReviewDecisionRecord:
    "ingest-import-review-decision-record.schema.json",
  Chapter: "chapter.schema.json",
  Scene: "scene.schema.json",
  StoryBeat: "story-beat.schema.json",
  Character: "character.schema.json",
  DialogueLine: "dialogue-line.schema.json",
  DialogueAttribution: "dialogue-attribution.schema.json",
  VoiceProfile: "voice-profile.schema.json",
  CastingAssignment: "casting-assignment.schema.json",
  PerformanceDirection: "performance-direction.schema.json",
  AmbienceCue: "ambience-cue.schema.json",
  FoleyCue: "foley-cue.schema.json",
  MusicCue: "music-cue.schema.json",
  ProductionTimeline: "production-timeline.schema.json",
  ContinuityRecord: "continuity-record.schema.json",
  RenderJob: "render-job.schema.json",
  RenderManifest: "render-manifest.schema.json",
  QualityControlFinding: "quality-control-finding.schema.json",
  ApprovalDecision: "approval-decision.schema.json"
};

async function readJson(filePath) {
  return JSON.parse(await readFile(filePath, "utf8"));
}

function collectReferences(value, references = []) {
  if (Array.isArray(value)) {
    for (const entry of value) {
      collectReferences(entry, references);
    }
    return references;
  }

  if (value && typeof value === "object") {
    if (typeof value.$ref === "string") {
      references.push(value.$ref);
    }
    for (const entry of Object.values(value)) {
      collectReferences(entry, references);
    }
  }
  return references;
}

test("all schema documents are valid JSON Schema 2020-12 documents", async () => {
  const fileNames = (await readdir(versionDirectory)).filter((name) =>
    name.endsWith(".schema.json")
  );
  assert.ok(fileNames.length >= Object.keys(entityEntries).length + 3);

  const schemas = await Promise.all(
    fileNames.map((name) => readJson(path.join(versionDirectory, name)))
  );
  for (const schema of schemas) {
    assert.equal(
      schema.$schema,
      "https://json-schema.org/draft/2020-12/schema"
    );
    assert.match(schema.$id, /^https:\/\/schemas\.cinematic-story-studio\.dev\/v1\//);
  }
});

test("every listed persisted entity or runtime contract has a versioned entry point", async () => {
  const definitions = await readJson(definitionsPath);

  for (const [entityName, fileName] of Object.entries(entityEntries)) {
    assert.ok(definitions.$defs[entityName], `missing $defs/${entityName}`);
    const entry = await readJson(path.join(versionDirectory, fileName));
    assert.equal(entry.title, entityName);
    assert.equal(entry.$ref, `definitions.schema.json#/$defs/${entityName}`);
    assert.equal(
      definitions.$defs[entityName].unevaluatedProperties,
      false,
      `${entityName} must reject unknown persisted fields`
    );
  }
});

test("all internal definition references resolve", async () => {
  const definitions = await readJson(definitionsPath);
  const references = collectReferences(definitions);

  for (const reference of references.filter((entry) =>
    entry.startsWith("#/$defs/")
  )) {
    const definitionName = reference.slice("#/$defs/".length);
    assert.ok(
      Object.hasOwn(definitions.$defs, definitionName),
      `unresolved reference ${reference}`
    );
  }
});

test("runtime-agent executions carry the complete control envelope", async () => {
  const definitions = await readJson(definitionsPath);
  const required = new Set(definitions.$defs.AgentExecutionEnvelope.required);

  for (const field of [
    "executionId",
    "agentId",
    "agentVersion",
    "purpose",
    "acceptedInputs",
    "outputSchemaRef",
    "confidence",
    "warnings",
    "humanReview",
    "retryPolicy",
    "failurePolicy",
    "provenance",
    "providerModel",
    "cost"
  ]) {
    assert.ok(required.has(field), `execution envelope must require ${field}`);
  }

  assert.equal(
    definitions.$defs.RuntimeAgentDefinition.properties.acceptedInputSchemas
      .minItems,
    1
  );
  assert.ok(
    definitions.$defs.RuntimeAgentDefinition.required.includes("outputSchemaRef")
  );
});

test("human corrections and approval decisions are immutable", async () => {
  const definitions = await readJson(definitionsPath);
  const humanCorrection = definitions.$defs.HumanCorrection;
  const approvalDecision = definitions.$defs.ApprovalDecision;

  assert.equal(humanCorrection.properties.immutable.const, true);
  assert.equal(humanCorrection.properties.lockedAgainstAutomation.const, true);
  assert.equal(
    humanCorrection.properties.authority.properties.source.const,
    "human"
  );
  assert.ok(humanCorrection.required.includes("correctedValue"));
  assert.ok(humanCorrection.required.includes("recordedAt"));

  const approvalShape = approvalDecision.allOf.find(
    (part) => part.properties?.immutable
  );
  assert.equal(approvalShape.properties.immutable.const, true);

  const dialogueAttribution = definitions.$defs.DialogueAttribution;
  const humanAuthorityRule = dialogueAttribution.allOf.find((part) => part.then);
  assert.equal(
    humanAuthorityRule.then.properties.effectiveAuthority.const,
    "human"
  );
});

test("secure ingest schemas mirror the typed Python boundary", async () => {
  const definitions = await readJson(definitionsPath);
  const sourceDocument = definitions.$defs.SourceDocument.allOf.find(
    (part) => part.properties?.documentId
  );
  assert.deepEqual(sourceDocument.required, [
    "documentId",
    "projectId",
    "displayName",
    "mediaType",
    "declaredFormat",
    "contentSha256",
    "byteLength",
    "importedAt",
    "originalTextPreserved",
    "originalBytesPreserved",
    "storageKey",
    "extractionStatus",
    "sourceRevision",
    "warnings"
  ]);
  assert.equal(
    sourceDocument.properties.declaredFormat.$ref,
    "#/$defs/DocumentFormat"
  );
  assert.equal(sourceDocument.properties.originalBytesPreserved.const, true);
  assert.deepEqual(sourceDocument.properties.extractionStatus.enum, [
    "pending",
    "running",
    "complete",
    "partial",
    "failed"
  ]);
  assert.equal(sourceDocument.properties.sourceRevision.minimum, 1);
  assert.equal(
    sourceDocument.properties.supersedesDocumentId.$ref,
    "#/$defs/Id"
  );
  assert.equal(sourceDocument.properties.textSha256.$ref, "#/$defs/Sha256");
  assert.deepEqual(sourceDocument.properties.newlineStyle.enum, [
    "none",
    "mixed",
    "crlf",
    "lf",
    "cr"
  ]);
  assert.equal(
    Object.hasOwn(sourceDocument.properties, "pageCount"),
    false
  );

  const extraction = definitions.$defs.DocumentExtractionResult;
  assert.deepEqual(extraction.required, [
    "contractVersion",
    "adapterId",
    "adapterVersion",
    "parserDependency",
    "parserVersion",
    "sourceSha256",
    "sourceByteCount",
    "declaredFormat",
    "detectedFormat",
    "mediaType",
    "canonicalText",
    "extractedTextSha256",
    "sections",
    "warnings",
    "confidence",
    "startedAt",
    "completedAt",
    "retryability",
    "reviewRequired",
    "provenance",
    "pageCount",
    "title",
    "status",
    "encoding",
    "newlineStyle",
    "manifest",
    "parserExecution"
  ]);
  assert.equal(
    extraction.properties.sourceByteCount.maximum,
    100 * 1024 * 1024
  );
  assert.equal(
    extraction.properties.canonicalText.maxLength,
    10_000_000
  );
  assert.equal(
    extraction.properties.reviewRequired.const,
    true
  );
  assert.deepEqual(definitions.$defs.DocumentProbe.required, [
    "contractVersion",
    "declaredFormat",
    "detectedFormat",
    "mediaType",
    "sourceSha256",
    "sourceByteCount"
  ]);
  assert.deepEqual(definitions.$defs.SourceLocation.required, [
    "kind",
    "member",
    "page",
    "start",
    "end"
  ]);
  assert.deepEqual(definitions.$defs.ExtractedSection.required, [
    "ordinal",
    "kind",
    "title",
    "start",
    "end",
    "location"
  ]);
  assert.deepEqual(definitions.$defs.ExtractionWarning.required, [
    "code",
    "severity",
    "message",
    "requiresHumanReview"
  ]);
  assert.deepEqual(definitions.$defs.ImportManifest.required, [
    "contractVersion",
    "originalPreserved",
    "sourceSha256",
    "sourceByteCount",
    "declaredFormat",
    "detectedFormat",
    "mediaType",
    "extractedTextSha256",
    "extractedCharacterCount",
    "sectionCount",
    "pageCount"
  ]);
  assert.equal(
    definitions.$defs.ExtractedSection.properties.ordinal.minimum,
    0
  );
  assert.equal(
    definitions.$defs.ExtractedSection.properties.ordinal.maximum,
    9_999
  );

  const parserRecord = definitions.$defs.ParserExecutionRecord;
  assert.deepEqual(parserRecord.required, [
    "contractVersion",
    "adapterId",
    "adapterVersion",
    "parserDependency",
    "parserVersion",
    "startedAt",
    "completedAt",
    "durationMs",
    "retryability",
    "networkAccessPermitted",
    "status",
    "limitsProfile",
    "limitsFingerprint"
  ]);
  assert.equal(
    parserRecord.properties.networkAccessPermitted.const,
    false
  );
  assert.equal(parserRecord.properties.durationMs.minimum, 0);
  assert.equal(
    Object.hasOwn(parserRecord.properties.durationMs, "maximum"),
    false,
    "measured duration must not imply a hard parser kill timeout"
  );
  assert.equal(
    parserRecord.properties.limitsProfile.$ref,
    "#/$defs/ParserLimitsProfile"
  );
  assert.equal(
    parserRecord.properties.limitsFingerprint.$ref,
    "#/$defs/Sha256"
  );
  assert.deepEqual(definitions.$defs.ParserLimitsProfile.required, [
    "profileId",
    "ingestContractVersion",
    "archiveMembers",
    "archiveMemberNameCodePoints",
    "archiveMemberBytes",
    "archiveExpandedBytes",
    "maximumCompressionRatio",
    "archivePathDepth",
    "canonicalTextCodePoints",
    "extractedSections",
    "pdfPages",
    "parserDeadlineMs",
    "parserProcessMemoryBytes"
  ]);
  const parserLimits =
    definitions.$defs.ParserLimitsProfile.properties;
  assert.equal(parserLimits.profileId.const, "secure-ingest-v1");
  assert.equal(parserLimits.ingestContractVersion.const, "1.0.0");
  assert.equal(parserLimits.archiveMembers.const, 2_048);
  assert.equal(parserLimits.archiveMemberNameCodePoints.const, 512);
  assert.equal(
    parserLimits.archiveMemberBytes.const,
    32 * 1024 * 1024
  );
  assert.equal(
    parserLimits.archiveExpandedBytes.const,
    200 * 1024 * 1024
  );
  assert.equal(parserLimits.maximumCompressionRatio.const, 100);
  assert.equal(parserLimits.archivePathDepth.const, 20);
  assert.equal(
    parserLimits.canonicalTextCodePoints.const,
    10_000_000
  );
  assert.equal(parserLimits.extractedSections.const, 10_000);
  assert.equal(parserLimits.pdfPages.const, 2_000);
  assert.equal(
    parserLimits.parserDeadlineMs.maximum,
    30_000
  );
  assert.equal(
    parserLimits.parserProcessMemoryBytes.const,
    768 * 1024 * 1024
  );
  const boundaryLimits =
    definitions.$defs.SecureIngestBoundaryLimits.properties;
  assert.equal(
    boundaryLimits.sourceBytes.const,
    100 * 1024 * 1024
  );
  assert.equal(boundaryLimits.previewCodePoints.const, 8_000);

  const decision =
    definitions.$defs.IngestImportReviewDecisionRecord;
  assert.deepEqual(decision.required, [
    "contractVersion",
    "decisionId",
    "projectId",
    "sourceDocumentId",
    "extractionRevision",
    "decision",
    "actorClassification",
    "decidedAt",
    "warningAcknowledgements",
    "reason",
    "provenance"
  ]);
  assert.equal(decision.properties.actorClassification.const, "human");
  assert.ok(
    decision.properties.decision.enum.includes("changes_requested")
  );
});

test("source locations cannot contain absolute or parent-traversal paths", async () => {
  const definitions = await readJson(definitionsPath);
  const memberPattern = new RegExp(
    definitions.$defs.SourceLocation.properties.member.pattern,
    "u"
  );

  assert.match("word/document.xml", memberPattern);
  assert.match("OEBPS/chapter-1.xhtml", memberPattern);
  assert.match("OEBPS/Chapter 1 – Synthetic.xhtml", memberPattern);
  assert.doesNotMatch("../escape.xml", memberPattern);
  assert.doesNotMatch("./ambiguous.xml", memberPattern);
  assert.doesNotMatch("OEBPS/../escape.xml", memberPattern);
  assert.doesNotMatch("OEBPS/line\nbreak.xhtml", memberPattern);
  assert.doesNotMatch("/absolute.xml", memberPattern);
  assert.doesNotMatch("C:\\private\\story.xml", memberPattern);
});
