import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const testDirectory = path.dirname(fileURLToPath(import.meta.url));
const versionDirectory = path.resolve(testDirectory, "..", "v1");
const definitionsPath = path.join(versionDirectory, "definitions.schema.json");
const phase2Directory = path.resolve(testDirectory, "..", "v2");
const phase2DefinitionsPath = path.join(
  phase2Directory,
  "definitions.schema.json",
);
const phase3Directory = path.resolve(testDirectory, "..", "v3");
const phase3DefinitionsPath = path.join(
  phase3Directory,
  "definitions.schema.json",
);

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

const phase2Entries = {
  AnalysisProfile: "analysis-profile.schema.json",
  StoryAnalysisRun: "story-analysis-run.schema.json",
  AnalysisAgentExecution: "analysis-agent-execution.schema.json",
  AnalysisSnapshot: "analysis-snapshot.schema.json",
  StoryStructure: "story-structure.schema.json",
  AnalysisChapter: "analysis-chapter.schema.json",
  AnalysisScene: "analysis-scene.schema.json",
  AnalysisBeat: "analysis-beat.schema.json",
  CharacterIdentity: "character-identity.schema.json",
  CharacterAlias: "character-alias.schema.json",
  CharacterMention: "character-mention.schema.json",
  AnalysisDialogueLine: "analysis-dialogue-line.schema.json",
  DialogueAttributionCandidate:
    "dialogue-attribution-candidate.schema.json",
  EffectiveDialogueAttribution:
    "effective-dialogue-attribution.schema.json",
  NarrationSpan: "narration-span.schema.json",
  PovSegment: "pov-segment.schema.json",
  StoryLocation: "story-location.schema.json",
  TimelineEvent: "timeline-event.schema.json",
  TemporalConstraint: "temporal-constraint.schema.json",
  CharacterRelationship: "character-relationship.schema.json",
  EmotionalState: "emotional-state.schema.json",
  DramaticIntent: "dramatic-intent.schema.json",
  ContinuityFinding: "continuity-finding.schema.json",
  AnalysisEvidenceSpan: "analysis-evidence-span.schema.json",
  AnalysisConfidence: "analysis-confidence.schema.json",
  AnalysisWarning: "analysis-warning.schema.json",
  AnalysisProvenance: "analysis-provenance.schema.json",
  CreateAnalysisCorrectionRequest:
    "create-analysis-correction-request.schema.json",
  AnalysisCorrection: "analysis-correction.schema.json",
  AnalysisGateReview: "analysis-gate-review.schema.json",
  AnalysisGateDecision: "analysis-gate-decision.schema.json",
  Phase2BuildEvidenceManifest:
    "phase-2-build-evidence-manifest.schema.json",
  Phase2PackagedE2eResult:
    "phase-2-packaged-e2e-result.schema.json",
};

const phase3Entries = {
  VoiceProviderDescriptor: "voice-provider-descriptor.schema.json",
  VoiceModelDescriptor: "voice-model-descriptor.schema.json",
  VoiceCapability: "voice-capability.schema.json",
  VoiceCatalogRevision: "voice-catalog-revision.schema.json",
  CastingVoiceProfile: "casting-voice-profile.schema.json",
  VoiceRightsRecord: "voice-rights-record.schema.json",
  ProductionRole: "production-role.schema.json",
  NarratorRole: "narrator-role.schema.json",
  CharacterVoiceRole: "character-voice-role.schema.json",
  CustomProductionRole: "custom-production-role.schema.json",
  CastingProfile: "casting-profile.schema.json",
  CastingRun: "casting-run.schema.json",
  CastingCandidate: "casting-candidate.schema.json",
  CastingCompatibilityAssessment:
    "casting-compatibility-assessment.schema.json",
  CastingConflict: "casting-conflict.schema.json",
  CastAssignment: "cast-assignment.schema.json",
  CastingCorrection: "casting-correction.schema.json",
  CastingGateReview: "casting-gate-review.schema.json",
  CastingReviewDecision: "casting-review-decision.schema.json",
  ApprovedCastSnapshot: "approved-cast-snapshot.schema.json",
  SyntheticVoiceCatalog: "synthetic-voice-catalog.schema.json",
  Phase3PackagedE2eResult:
    "phase-3-packaged-e2e-result.schema.json",
  Phase3BuildEvidenceManifest:
    "phase-3-build-evidence-manifest.schema.json",
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

function collectRequiredDeclarationGaps(
  value,
  location = "#",
  gaps = []
) {
  if (Array.isArray(value)) {
    value.forEach((entry, index) => {
      collectRequiredDeclarationGaps(
        entry,
        `${location}/${index}`,
        gaps
      );
    });
    return gaps;
  }

  if (!value || typeof value !== "object") {
    return gaps;
  }

  if (Array.isArray(value.required)) {
    const declared = new Set(Object.keys(value.properties ?? {}));
    const missing = value.required.filter((name) => !declared.has(name));
    if (missing.length > 0) {
      gaps.push({ location, missing });
    }
  }

  for (const [name, entry] of Object.entries(value)) {
    collectRequiredDeclarationGaps(
      entry,
      `${location}/${name}`,
      gaps
    );
  }
  return gaps;
}

function collectReachableDefinitions(definitions, rootNames) {
  const pending = [...rootNames];
  const reachable = {};

  while (pending.length > 0) {
    const name = pending.pop();
    if (
      name === undefined ||
      Object.hasOwn(reachable, name)
    ) {
      continue;
    }
    const definition = definitions.$defs[name];
    assert.ok(definition, `missing reachable $defs/${name}`);
    reachable[name] = definition;
    for (const reference of collectReferences(definition)) {
      if (reference.startsWith("#/$defs/")) {
        pending.push(reference.slice("#/$defs/".length).split("/")[0]);
      }
    }
  }

  return reachable;
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

test("Phase 2 contracts have stable JSON Schema 2020-12 entry points", async () => {
  const fileNames = (await readdir(phase2Directory)).filter((name) =>
    name.endsWith(".schema.json"),
  );
  assert.ok(fileNames.length >= Object.keys(phase2Entries).length + 1);
  const definitions = await readJson(phase2DefinitionsPath);

  for (const fileName of fileNames) {
    const schema = await readJson(path.join(phase2Directory, fileName));
    assert.equal(
      schema.$schema,
      "https://json-schema.org/draft/2020-12/schema",
    );
    assert.match(
      schema.$id,
      /^https:\/\/schemas\.cinematic-story-studio\.dev\/v2\//u,
    );
  }

  for (const [definitionName, fileName] of Object.entries(
    phase2Entries,
  )) {
    assert.ok(
      definitions.$defs[definitionName],
      `missing Phase 2 $defs/${definitionName}`,
    );
    const entry = await readJson(path.join(phase2Directory, fileName));
    assert.equal(entry.title, definitionName);
    assert.equal(
      entry.$ref,
      `definitions.schema.json#/$defs/${definitionName}`,
    );
  }
});

test("all Phase 2 internal definition references resolve", async () => {
  const definitions = await readJson(phase2DefinitionsPath);
  const references = collectReferences(definitions);

  for (const reference of references.filter((entry) =>
    entry.startsWith("#/$defs/"),
  )) {
    const definitionName = reference
      .slice("#/$defs/".length)
      .split("/", 1)[0];
    assert.ok(
      Object.hasOwn(definitions.$defs, definitionName),
      `unresolved Phase 2 reference ${reference}`,
    );
  }
});

test("Phase 2 required fields are locally declared for strict validators", async () => {
  const definitions = await readJson(phase2DefinitionsPath);
  assert.deepEqual(collectRequiredDeclarationGaps(definitions), []);
});

test("Phase 2 profile canonical JSON, fingerprint, limits, producer, and agents are exact", async () => {
  const definitions = await readJson(phase2DefinitionsPath);
  const profile = definitions.$defs.AnalysisProfile;
  const values = definitions.$defs.AnalysisProfileValues;
  const canonicalJson = profile.properties.canonicalJson.const;
  const fingerprint = createHash("sha256")
    .update(canonicalJson, "utf8")
    .digest("hex");
  const canonicalProfile = JSON.parse(canonicalJson);

  assert.equal(
    profile.properties.fingerprint.const,
    "6ae73e83e89fbcfc0261ff339950407913cd990093fa13cdcc83ce3b1da810ec",
  );
  assert.equal(fingerprint, profile.properties.fingerprint.const);
  assert.deepEqual(canonicalProfile, {
    agentVersions: [
      { agentId: "story-structure", version: "1.0.0" },
      { agentId: "story-beats", version: "1.0.0" },
      { agentId: "character-identity", version: "1.0.0" },
      { agentId: "dialogue-attribution", version: "1.0.0" },
      { agentId: "point-of-view", version: "1.0.0" },
      { agentId: "story-setting", version: "1.0.0" },
      { agentId: "story-timeline", version: "1.0.0" },
      { agentId: "character-relationships", version: "1.0.0" },
      { agentId: "emotion-dramatic-intent", version: "1.0.0" },
      { agentId: "story-continuity", version: "1.0.0" },
      { agentId: "analysis-synthesis", version: "1.0.0" },
    ],
    analysisContractVersion: "2.0.0",
    confidenceClassification: {
      high: { maximumInclusive: 1, minimumInclusive: 0.85 },
      low: { maximumExclusive: 0.75, minimumExclusive: 0 },
      medium: { maximumExclusive: 0.85, minimumInclusive: 0.75 },
      unknown: { score: 0 },
    },
    deterministic: true,
    limits: {
      defaultPageSize: 50,
      maximumAgentEnvelopeBytes: 32_768,
      maximumAnalysisEntities: 250_000,
      maximumAnalysisWords: 150_000,
      maximumAttributionCandidatesPerLine: 8,
      maximumCheckpointBytes: 67_108_864,
      maximumEvidenceExcerptCodePoints: 512,
      maximumEvidenceSpansPerClaim: 16,
      maximumExactTextCodePoints: 16_384,
      maximumPageSize: 200,
      maximumSnapshotStages: 5,
      maximumWarningsPerEntity: 32,
    },
    offsetUnit: "unicode-code-point",
    producer: {
      producerId: "whole-book-analysis-orchestrator",
      producerVersion: "1.0.0",
    },
    profileId: "whole-book-intelligence-v1",
    semanticVersion: "1.0.0",
  });
  assert.deepEqual(values.required, [
    "agentVersions",
    "analysisContractVersion",
    "confidenceClassification",
    "deterministic",
    "limits",
    "offsetUnit",
    "producer",
    "profileId",
    "semanticVersion",
  ]);
  const limits = definitions.$defs.AnalysisProfileLimits;
  assert.deepEqual(limits.required, Object.keys(canonicalProfile.limits));
  assert.deepEqual(
    Object.fromEntries(
      Object.entries(limits.properties).map(([key, property]) => [
        key,
        property.const,
      ]),
    ),
    canonicalProfile.limits,
  );
  assert.equal(
    values.properties.agentVersions.prefixItems.length,
    11,
  );
  assert.equal(values.properties.agentVersions.items, false);
});

test("Phase 2 confidence classes and evidence/text bounds are exact", async () => {
  const definitions = await readJson(phase2DefinitionsPath);
  const confidence = definitions.$defs.AnalysisConfidence.oneOf;
  const byClass = Object.fromEntries(
    confidence.map((branch) => [
      branch.properties.classification.const,
      branch.properties.score,
    ]),
  );

  assert.deepEqual(byClass.unknown, { const: 0 });
  assert.equal(byClass.low.exclusiveMinimum, 0);
  assert.equal(byClass.low.exclusiveMaximum, 0.75);
  assert.equal(byClass.medium.minimum, 0.75);
  assert.equal(byClass.medium.exclusiveMaximum, 0.85);
  assert.equal(byClass.high.minimum, 0.85);
  assert.equal(byClass.high.maximum, 1);

  const exactTextExtension =
    definitions.$defs.ExactAnalysisText.allOf[1];
  assert.deepEqual(exactTextExtension.required, [
    "exactText",
    "exactTextSha256",
    "originalCodePointCount",
    "exactTextTruncated",
    "originalTextPreserved",
  ]);
  assert.equal(
    exactTextExtension.properties.exactText.maxLength,
    16_384,
  );
  assert.equal(
    definitions.$defs.AnalysisEvidenceSpan.allOf[1].properties
      .excerptText.maxLength,
    512,
  );
  assert.equal(
    definitions.$defs.AnalysisEntityHeader.properties.evidence.maxItems,
    16,
  );
  assert.equal(
    definitions.$defs.AnalysisEntityHeader.properties.warnings.maxItems,
    32,
  );
  assert.equal(
    definitions.$defs.AnalysisDialogueLine.allOf[1].properties
      .candidates.maxItems,
    8,
  );
  assert.ok(
    definitions.$defs.AnalysisEntityCollection.enum.includes(
      "narration-spans",
    ),
  );
  assert.deepEqual(
    definitions.$defs.NarrationSpan.properties.classification.enum,
    [
      "direct_narration",
      "internal_thought",
      "quoted_material",
      "epigraph_or_document",
      "unresolved",
    ],
  );
});

test("Phase 2 runs freeze every approved input, producer, agent, and snapshot precondition", async () => {
  const definitions = await readJson(phase2DefinitionsPath);
  const run = definitions.$defs.StoryAnalysisRun;
  for (const field of [
    "projectId",
    "storyId",
    "storyRevision",
    "storyFingerprint",
    "sourceDocumentId",
    "sourceRevision",
    "sourceSha256",
    "extractionId",
    "extractionRevision",
    "extractedTextSha256",
    "importReviewId",
    "importReviewRevision",
    "importReviewDecisionId",
    "approvedEvidenceFingerprint",
    "profile",
    "producer",
    "agentVersions",
    "inputFingerprint",
    "runFingerprint",
    "jobId",
    "status",
    "currentStage",
    "progress",
    "warnings",
    "snapshotCount",
    "currentSnapshot",
  ]) {
    assert.ok(run.required.includes(field), `run must require ${field}`);
  }
  const header = definitions.$defs.AnalysisEntityHeader;
  for (const field of [
    "entityId",
    "stableSemanticId",
    "machineEntityFingerprint",
    "effectiveValueFingerprint",
    "effectiveAuthority",
    "effectiveRevision",
  ]) {
    assert.ok(
      header.required.includes(field),
      `entity header must require ${field}`,
    );
  }
  assert.equal(
    definitions.$defs.AnalysisSnapshot.properties.collections.minItems,
    16,
  );
  assert.deepEqual(
    definitions.$defs.AnalysisSnapshot.properties.collections
      .prefixItems.map(
        (item) => item.allOf[1].properties.collection.const,
      ),
    [
      "agent-executions",
      "chapters",
      "scenes",
      "beats",
      "characters",
      "mentions",
      "dialogue-lines",
      "narration-spans",
      "pov-segments",
      "locations",
      "timeline-events",
      "temporal-constraints",
      "relationships",
      "emotional-states",
      "dramatic-intents",
      "continuity-findings",
    ],
  );
  assert.equal(
    definitions.$defs.AnalysisSnapshot.properties.immutable.const,
    true,
  );
});

test("Phase 2 corrections, continuity findings, and four review gates stay governed", async () => {
  const definitions = await readJson(phase2DefinitionsPath);
  const createCorrection =
    definitions.$defs.CreateAnalysisCorrectionRequest;
  const correction = definitions.$defs.AnalysisCorrection;
  assert.ok(createCorrection.required.includes("reason"));
  assert.equal(createCorrection.properties.reason.minLength, 1);
  assert.equal(createCorrection.properties.reason.maxLength, 1_000);
  assert.equal(createCorrection.properties.reason.pattern, ".*\\S.*");
  assert.ok(correction.required.includes("reason"));
  assert.equal(correction.properties.reason.minLength, 1);
  assert.equal(correction.properties.reason.maxLength, 1_000);
  assert.equal(correction.properties.reason.pattern, ".*\\S.*");
  assert.equal(correction.properties.immutable.const, true);
  assert.equal(
    correction.properties.lockedAgainstAutomation.const,
    true,
  );
  assert.deepEqual(correction.properties.category.enum, [
    "structure_boundary",
    "structure_label",
    "character_identity",
    "character_alias",
    "character_merge",
    "character_split",
    "mention_resolution",
    "dialogue_speaker",
    "point_of_view",
    "location_identity",
    "location_alias",
    "temporal_order",
    "relationship",
    "emotional_state",
    "dramatic_intent",
    "continuity_disposition",
  ]);
  assert.equal(
    definitions.$defs.AnalysisCorrectionPatch.oneOf.length,
    18,
  );
  assert.equal(
    definitions.$defs.AnalysisCorrectionSelection.oneOf.length,
    18,
  );
  assert.equal(
    definitions.$defs.AnalysisCorrectionRequestPatch.oneOf.length,
    18,
  );
  assert.equal(
    definitions.$defs.AnalysisCorrectionRequestSelection.oneOf.length,
    3,
  );
  assert.deepEqual(
    [
      ...new Set(
        definitions.$defs.AnalysisCorrectionSelection.oneOf.map(
          (branch) => branch.properties.category.const,
        ),
      ),
    ],
    correction.properties.category.enum,
  );
  assert.ok(
    correction.allOf.some(
      (branch) =>
        branch.$ref === "#/$defs/AnalysisCorrectionSelection",
    ),
  );
  assert.equal(
    createCorrection.properties.patch.$ref,
    "#/$defs/AnalysisCorrectionRequestPatch",
  );
  assert.ok(
    createCorrection.allOf.some(
      (branch) =>
        branch.$ref ===
        "#/$defs/AnalysisCorrectionRequestSelection",
    ),
  );
  const selectedSourceSpan =
    definitions.$defs.AnalysisSourceSpanSelection;
  assert.equal(
    Object.hasOwn(selectedSourceSpan.properties, "textSha256"),
    false,
  );
  assert.equal(
    selectedSourceSpan.required.includes("textSha256"),
    false,
  );
  assert.equal(
    definitions.$defs.StructureBoundaryCorrectionRequestPatch
      .properties.sourceSpan.$ref,
    "#/$defs/AnalysisSourceSpanSelection",
  );
  assert.equal(
    definitions.$defs.StructureBoundaryCorrectionPatch
      .additionalProperties,
    false,
  );
  assert.deepEqual(
    definitions.$defs.StructureBoundaryCorrectionPatch.oneOf.map(
      (branch) => branch.properties.operation.const,
    ),
    ["add", "remove", "move"],
  );
  const effectiveBoundary =
    definitions.$defs.HumanEffectiveBoundary;
  assert.deepEqual(effectiveBoundary.required, [
    "operation",
    "included",
    "parentEntityId",
    "ordinal",
    "sourceSpan",
    "authority",
    "correctionId",
  ]);
  assert.equal(effectiveBoundary.properties.authority.const, "human");
  assert.deepEqual(
    effectiveBoundary.oneOf.map((branch) => ({
      operation:
        branch.properties.operation.const ??
        branch.properties.operation.enum,
      included: branch.properties.included.const,
    })),
    [
      { operation: ["add", "move"], included: true },
      { operation: "remove", included: false },
    ],
  );
  for (const definitionName of ["AnalysisChapter", "AnalysisScene"]) {
    const entity = definitions.$defs[definitionName].allOf[1];
    assert.equal(
      entity.properties.effectiveBoundary.$ref,
      "#/$defs/HumanEffectiveBoundary",
    );
    assert.equal(
      entity.required.includes("effectiveBoundary"),
      false,
    );
  }

  const effectiveRegistry =
    definitions.$defs.HumanEffectiveRegistry;
  assert.deepEqual(
    effectiveRegistry.oneOf.map(
      (branch) => branch.properties.operation.const,
    ),
    ["merge", "split"],
  );
  for (const branch of effectiveRegistry.oneOf) {
    assert.equal(branch.properties.authority.const, "human");
    assert.ok(branch.required.includes("correctionId"));
  }
  assert.ok(
    effectiveRegistry.oneOf[0].required.includes(
      "mergeIntoCharacterId",
    ),
  );
  assert.ok(
    effectiveRegistry.oneOf[1].required.includes("splitIdentity"),
  );
  const splitIdentity =
    definitions.$defs.HumanSplitCharacterIdentity;
  assert.deepEqual(splitIdentity.required, [
    "registryCharacterId",
    "canonicalName",
    "normalizedCanonicalName",
    "mentionIds",
  ]);
  assert.equal(splitIdentity.properties.mentionIds.minItems, 1);
  assert.equal(splitIdentity.properties.mentionIds.uniqueItems, true);
  const characterIdentity =
    definitions.$defs.CharacterIdentity.allOf[1];
  assert.equal(
    characterIdentity.properties.effectiveRegistry.$ref,
    "#/$defs/HumanEffectiveRegistry",
  );
  assert.equal(
    characterIdentity.required.includes("effectiveRegistry"),
    false,
  );
  assert.deepEqual(definitions.$defs.AnalysisGateId.enum, [
    "story_structure_review",
    "character_registry_review",
    "dialogue_attribution_review",
    "whole_book_analysis_review",
  ]);
  assert.deepEqual(
    definitions.$defs.AnalysisGateReview.properties.state.enum,
    [
      "pending",
      "approved",
      "changes_requested",
      "rejected",
      "invalidated",
    ],
  );
  assert.ok(
    definitions.$defs.AnalysisGateReview.required.includes(
      "latestDecision",
    ),
  );
  assert.equal(
    definitions.$defs.AnalysisGateReview.properties.latestDecision
      .oneOf[0].$ref,
    "#/$defs/AnalysisGateDecision",
  );
  assert.equal(
    definitions.$defs.AnalysisGateDecision.properties.immutable.const,
    true,
  );
  assert.ok(
    definitions.$defs.AnalysisGateDecision.required.includes(
      "acknowledgedWarningIds",
    ),
  );
  assert.ok(
    definitions.$defs.AnalysisGateDecision.required.includes(
      "rationale",
    ),
  );
  assert.equal(
    definitions.$defs.AnalysisGateDecision.properties.rationale
      .minLength,
    1,
  );
  assert.equal(
    definitions.$defs.AnalysisGateDecision.properties.rationale
      .maxLength,
    4_000,
  );
  assert.equal(
    definitions.$defs.AnalysisGateDecision.properties.rationale
      .pattern,
    ".*\\S.*",
  );
  assert.ok(
    definitions.$defs.AnalysisGateEvidence.required.includes(
      "evidenceFingerprint",
    ),
  );
  const dialogueLine =
    definitions.$defs.AnalysisDialogueLine.allOf[1];
  assert.ok(dialogueLine.required.includes("speakerState"));
  assert.deepEqual(dialogueLine.properties.speakerState.enum, [
    "unknown",
    "ambiguous",
    "proposed",
    "corrected",
  ]);

  const continuity = definitions.$defs.ContinuityFinding.allOf[1];
  assert.deepEqual(continuity.properties.category.enum, [
    "possible_duplicate_character",
    "identity_contradiction",
    "alias_conflict",
    "dialogue_speaker_conflict",
    "chronology_conflict",
    "location_conflict",
    "attribute_conflict",
    "unexplained_object_state_change",
    "unexplained_character_state_change",
    "pov_discontinuity",
    "scene_boundary_uncertainty",
    "unresolved_reference",
    "extraction_uncertainty",
    "other",
  ]);
  assert.deepEqual(
    definitions.$defs.HumanContinuityDisposition.properties.disposition
      .enum,
    [
      "confirmed_issue",
      "intentional",
      "false_positive",
      "deferred",
      "corrected",
      "unresolved",
    ],
  );
});

test("Phase 2 semantic vocabularies, ranges, assignments, and durable executions are explicit", async () => {
  const definitions = await readJson(phase2DefinitionsPath);

  assert.deepEqual(
    definitions.$defs.PovSegment.allOf[1].properties.mode.enum,
    [
      "first_person",
      "second_person",
      "third_person_limited",
      "third_person_omniscient",
      "mixed",
      "experimental",
      "unknown",
    ],
  );
  assert.ok(
    definitions.$defs.NarrationDialogueDistinction.enum.includes(
      "epigraph_or_document",
    ),
  );
  assert.ok(
    definitions.$defs.NarrationDialogueDistinction.enum.includes(
      "unresolved_speech",
    ),
  );

  const identity = definitions.$defs.CharacterIdentity.allOf[1];
  for (const field of [
    "registryCharacterId",
    "projectId",
    "storyId",
    "registryScope",
    "stableAcrossCompatibleRuns",
    "honorifics",
    "pronounEvidence",
    "namedMentionIds",
    "ambiguousMentionIds",
    "firstEvidence",
    "lastEvidence",
  ]) {
    assert.ok(
      identity.required.includes(field),
      `character identity must require ${field}`,
    );
  }
  assert.equal(
    identity.properties.registryScope.const,
    "project_story",
  );
  assert.equal(
    identity.properties.stableAcrossCompatibleRuns.const,
    true,
  );
  assert.deepEqual(
    identity.properties.firstMentionId.oneOf[1],
    { type: "null" },
  );
  assert.deepEqual(
    identity.properties.lastMentionId.oneOf[1],
    { type: "null" },
  );
  const mentionBounds = definitions.$defs.CharacterIdentity.allOf[2];
  assert.equal(
    mentionBounds.if.properties.mentionCount.const,
    0,
  );
  assert.equal(
    mentionBounds.then.properties.firstMentionId.type,
    "null",
  );
  assert.equal(
    mentionBounds.then.properties.firstEvidence.maxItems,
    0,
  );
  assert.equal(
    mentionBounds.else.properties.firstEvidence.minItems,
    1,
  );
  assert.equal(
    identity.properties.namedMentionIds.maxItems,
    100_000,
  );
  assert.equal(
    identity.properties.ambiguousMentionIds.maxItems,
    100_000,
  );
  assert.equal(
    definitions.$defs.CharacterSplitCorrectionPatch.properties
      .mentionIds.maxItems,
    256,
  );
  const alias = definitions.$defs.CharacterAlias;
  assert.ok(alias.required.includes("effectiveRange"));
  assert.ok(alias.required.includes("change"));

  const location = definitions.$defs.StoryLocation.allOf[1];
  assert.ok(location.required.includes("sceneIds"));
  assert.ok(location.required.includes("sceneAssignments"));
  assert.equal(location.properties.sceneAssignments.minItems, 1);
  assert.equal(location.properties.sceneIds.maxItems, 10_000);
  assert.equal(location.properties.sceneAssignments.maxItems, 10_000);

  const temporal =
    definitions.$defs.TemporalConstraint.allOf[1];
  assert.ok(temporal.required.includes("approximate"));
  assert.equal(
    temporal.properties.approximate.type,
    "boolean",
  );

  const relationship =
    definitions.$defs.CharacterRelationship.allOf[1];
  assert.deepEqual(relationship.properties.kind.enum, [
    "family",
    "friendship",
    "romantic",
    "professional",
    "adversarial",
    "authority",
    "dependency",
    "alliance",
    "unknown",
    "custom",
  ]);
  for (const field of [
    "chapterId",
    "scope",
    "validFromEventId",
    "validThroughEventId",
    "change",
  ]) {
    assert.ok(
      relationship.required.includes(field),
      `relationship must require ${field}`,
    );
  }

  const emotion = definitions.$defs.EmotionalState.allOf[1];
  assert.ok(emotion.properties.emotion.enum.includes("unknown"));
  assert.ok(emotion.properties.emotion.enum.includes("custom"));
  assert.ok(emotion.required.includes("note"));
  assert.ok(emotion.required.includes("subjectType"));
  assert.equal(emotion.required.includes("characterId"), false);
  assert.equal(emotion.oneOf.length, 2);
  assert.equal(emotion.properties.note.maxLength, 1000);
  const intent = definitions.$defs.DramaticIntent.allOf[1];
  assert.ok(intent.properties.intent.enum.includes("question"));
  assert.ok(intent.properties.intent.enum.includes("custom"));
  assert.ok(intent.required.includes("note"));
  assert.ok(intent.required.includes("subjectType"));
  assert.ok(intent.required.includes("dramaticFunction"));
  assert.equal(intent.required.includes("characterId"), false);
  assert.equal(intent.oneOf.length, 4);
  assert.equal(intent.properties.note.maxLength, 1000);

  const execution = definitions.$defs.AnalysisAgentExecution;
  for (const field of [
    "progress",
    "currentStage",
    "checkpoint",
    "retryClassification",
    "retryPolicy",
    "failurePolicy",
    "failure",
    "provenance",
  ]) {
    assert.ok(
      execution.required.includes(field),
      `analysis execution must require ${field}`,
    );
  }
  assert.equal(execution.properties.progress.minimum, 0);
  assert.equal(execution.properties.progress.maximum, 1);
  assert.equal(
    execution.properties.failure.oneOf[0].properties.redacted.const,
    true,
  );
});

test("Phase 2 CI schemas pin packaged v4 and complete manifest proof", async () => {
  const definitions = await readJson(phase2DefinitionsPath);
  const packaged = definitions.$defs.Phase2PackagedE2eResult;
  const manifest = definitions.$defs.Phase2BuildEvidenceManifest;
  const story = definitions.$defs.Phase2StoryAnalysisEvidence;

  assert.equal(packaged.properties.schemaVersion.const, "4.0.0");
  assert.ok(packaged.required.includes("storyAnalysis"));
  assert.ok(packaged.required.includes("importReview"));
  assert.equal(
    packaged.properties.fixture.const,
    "fixtures/synthetic-story/sample-story.docx.base64",
  );
  assert.equal(manifest.properties.schemaVersion.const, "3.0.0");
  assert.equal(
    manifest.properties.storyAnalysisContract.additionalProperties,
    false,
  );
  assert.equal(
    Object.values(manifest.properties.assertions.properties).every(
      (property) => property.const === true,
    ),
    true,
  );
  assert.deepEqual(
    definitions.$defs.Phase2PackagedFlow.prefixItems.map(
      (item) => item.const,
    ),
    [
      "create",
      "import_synthetic_docx",
      "wait_for_extraction",
      "review_import",
      "approve_import",
      "analyze",
      "correct_speaker",
      "start_whole_book_analysis",
      "observe_analysis_stages",
      "inspect_structure",
      "inspect_character_registry",
      "correct_character_identity",
      "inspect_dialogue_and_narration",
      "correct_dialogue_speaker",
      "inspect_whole_book_intelligence",
      "disposition_continuity",
      "approve_story_structure_review",
      "approve_character_registry_review",
      "approve_dialogue_attribution_review",
      "approve_whole_book_analysis_review",
      "close",
      "restart",
      "restore",
      "verify_import_review_persistence",
      "verify_story_analysis_persistence",
      "close",
    ],
  );
  assert.equal(
    packaged.properties.flow.$ref,
    "#/$defs/Phase2PackagedFlow",
  );
  assert.equal(
    manifest.properties.packagedE2e.properties.flow.$ref,
    "#/$defs/Phase2PackagedFlow",
  );
  assert.equal(
    manifest.properties.packagedE2e.properties.screenshot.$ref,
    "#/$defs/BuildEvidenceSuccessfulScreenshot",
  );
  assert.equal(
    manifest.properties.packagedE2e.properties.launches.$ref,
    "#/$defs/Phase2SuccessfulPackagedE2eLaunches",
  );
  assert.deepEqual(
    {
      os: manifest.properties.runner.properties.os.const,
      architecture:
        manifest.properties.runner.properties.architecture.const,
      environment:
        manifest.properties.runner.properties.environment.const,
      workflow:
        manifest.properties.runner.properties.workflow.const,
      job: manifest.properties.runner.properties.job.const,
    },
    {
      os: "Windows",
      architecture: "X64",
      environment: "github-hosted",
      workflow: "Phase 2 Windows CI",
      job: "verify-and-build",
    },
  );
  assert.deepEqual(
    story.properties.agents.prefixItems.map(
      (item) => item.allOf[1].properties.agentId.const,
    ),
    [
      "story-structure",
      "story-beats",
      "character-identity",
      "dialogue-attribution",
      "point-of-view",
      "story-setting",
      "story-timeline",
      "character-relationships",
      "emotion-dramatic-intent",
      "story-continuity",
      "analysis-synthesis",
    ],
  );
  assert.equal(story.properties.observedStages.prefixItems.length, 14);
  assert.deepEqual(
    story.properties.corrections.required,
    [
      "characterIdentity",
      "dialogueSpeaker",
      "continuityDisposition",
    ],
  );
  assert.deepEqual(
    Object.values(story.properties.corrections.properties).map(
      (item) => item.allOf[1].properties.category.const,
    ),
    [
      "character_identity",
      "dialogue_speaker",
      "continuity_disposition",
    ],
  );
  assert.deepEqual(
    story.properties.gates.prefixItems.map(
      (item) => item.allOf[1].properties.gateId.const,
    ),
    [
      "story_structure_review",
      "character_registry_review",
      "dialogue_attribution_review",
      "whole_book_analysis_review",
    ],
  );
  assert.equal(
    Object.values(
      definitions.$defs.Phase2E2eAssertions.properties,
    ).every((property) => property.const === true),
    true,
  );
  assert.equal(
    Object.values(story.properties.restart.properties).every(
      (property) => property.const === true,
    ),
    true,
  );
  assert.equal(
    story.properties.counts.$ref,
    "#/$defs/Phase2E2eCounts",
  );
  assert.equal(
    definitions.$defs.Phase2E2eCounts.properties.temporalConstraints
      .minimum,
    1,
  );
  assert.equal(
    definitions.$defs.Phase2E2eCounts.properties.corrections.minimum,
    3,
  );

  const proofSchemas = collectReachableDefinitions(definitions, [
    "Phase2BuildEvidenceManifest",
    "Phase2PackagedE2eResult",
  ]);
  const serializedProofSchemas = JSON.stringify(proofSchemas);
  for (const privateContentField of [
    "exactText",
    "excerptText",
    "manuscript",
    "sourceText",
    "textContent",
  ]) {
    assert.equal(
      serializedProofSchemas.includes(`"${privateContentField}"`),
      false,
      `CI proof schemas must not admit ${privateContentField}`,
    );
  }
});

test("Phase 3A contracts have stable closed JSON Schema 2020-12 entry points", async () => {
  const fileNames = (await readdir(phase3Directory)).filter((name) =>
    name.endsWith(".schema.json")
  );
  assert.equal(fileNames.length, Object.keys(phase3Entries).length + 1);
  const definitions = await readJson(phase3DefinitionsPath);

  for (const fileName of fileNames) {
    const schema = await readJson(path.join(phase3Directory, fileName));
    assert.equal(
      schema.$schema,
      "https://json-schema.org/draft/2020-12/schema",
    );
    assert.match(
      schema.$id,
      /^https:\/\/schemas\.cinematic-story-studio\.dev\/v3\//,
    );
  }

  for (const [definitionName, fileName] of Object.entries(
    phase3Entries,
  )) {
    const definition = definitions.$defs[definitionName];
    assert.ok(
      definition,
      `missing Phase 3A $defs/${definitionName}`,
    );
    assert.equal(
      definition.additionalProperties === false ||
        definition.unevaluatedProperties === false,
      true,
      `${definitionName} must reject unknown fields`,
    );
    const entry = await readJson(path.join(phase3Directory, fileName));
    assert.equal(entry.title, definitionName);
    assert.equal(
      entry.$ref,
      `definitions.schema.json#/$defs/${definitionName}`,
    );
  }
});

test("all Phase 3A internal definition references resolve", async () => {
  const definitions = await readJson(phase3DefinitionsPath);
  for (const reference of collectReferences(definitions).filter((entry) =>
    entry.startsWith("#/$defs/")
  )) {
    const definitionName = reference
      .slice("#/$defs/".length)
      .split("/")[0];
    assert.ok(
      Object.hasOwn(definitions.$defs, definitionName),
      `unresolved Phase 3A reference ${reference}`,
    );
  }
});

test("Phase 3A governed profile, producer, rights policy, and bounds are pinned", async () => {
  const definitions = await readJson(phase3DefinitionsPath);
  const profile =
    definitions.$defs.CastingProfile.properties.values.properties;
  const limits = definitions.$defs.CastingLimits.properties;

  assert.equal(
    profile.profileId.const,
    "governed-voice-casting-v1@1.0.0",
  );
  assert.equal(
    profile.producerId.const,
    "voice-casting-orchestrator@1.0.0",
  );
  assert.equal(
    profile.rightsPolicyId.const,
    "voice-rights-policy-v1",
  );
  assert.equal(profile.deterministic.const, true);
  assert.equal(profile.providerNeutral.const, true);
  assert.equal(profile.externalSemanticDependency.const, false);
  assert.equal(profile.explanationRequired.const, true);
  assert.deepEqual(
    definitions.$defs.CastingCompatibilityRules.prefixItems.map(
      (item) => item.const,
    ),
    [
      "hard_constraints_fail_closed",
      "soft_preferences_score_separately",
      "unknown_remains_unknown",
      "no_automatic_assignment",
      "declared_metadata_only",
    ],
  );
  assert.deepEqual(
    definitions.$defs.CastingRightsEligibilityRules.prefixItems.map(
      (item) => item.const,
    ),
    [
      "verified_eligible",
      "restricted_requires_acknowledgement",
      "unknown_ineligible",
      "prohibited_ineligible",
    ],
  );
  assert.equal(
    definitions.$defs.CastingProfile.properties.fingerprint.const,
    "3eaa6b4d1333b49e55707b1e9aa20606f262e1315a043bff2912a0fe77f97fa6",
  );
  assert.equal(
    createHash("sha256")
      .update(
        definitions.$defs.CastingProfile.properties.canonicalJson.const,
        "utf8",
      )
      .digest("hex"),
    definitions.$defs.CastingProfile.properties.fingerprint.const,
  );
  assert.deepEqual(
    Object.fromEntries(
      Object.entries(limits).map(([name, property]) => [
        name,
        property.const,
      ]),
    ),
    {
      maximumProductionRoles: 300,
      maximumVoiceProfiles: 5000,
      maximumPreReductionCandidatesPerRole: 50,
      maximumFinalCandidatesPerRole: 12,
      defaultPageSize: 50,
      maximumPageSize: 200,
      maximumExplanationCodePoints: 2000,
      maximumWarningsPerEntity: 32,
      maximumHardConstraintResults: 16,
      maximumSoftPreferenceResults: 16,
      maximumConflictsPerRun: 10000,
      maximumVoiceReusePerProfile: 2,
    },
  );
  assert.equal(
    definitions.$defs.CastingCompatibilityAssessment.properties
      .explanation.$ref,
    "#/$defs/NonblankText",
  );
  assert.equal(definitions.$defs.NonblankText.maxLength, 2000);
});

test("Phase 3A rights, roles, compatibility, conflicts, corrections, and gates are explicit", async () => {
  const definitions = await readJson(phase3DefinitionsPath);

  assert.deepEqual(definitions.$defs.Warning.required, [
    "code",
    "severity",
    "message",
    "requiresHumanReview",
    "relatedEntityIds",
    "evidence",
  ]);
  assert.deepEqual(
    Object.keys(definitions.$defs.Warning.properties),
    [
      "code",
      "severity",
      "message",
      "requiresHumanReview",
      "relatedEntityIds",
      "evidence",
    ],
  );
  assert.equal(
    Object.hasOwn(definitions.$defs.Warning.properties, "warningId"),
    false,
  );
  assert.equal(
    definitions.$defs.Warning.properties.code.pattern,
    "^[A-Z][A-Z0-9_]{2,79}$",
  );
  assert.equal(
    definitions.$defs.Warning.properties.message.maxLength,
    1000,
  );
  assert.equal(
    definitions.$defs.Warning.properties.relatedEntityIds.maxItems,
    16,
  );
  assert.equal(
    definitions.$defs.Warning.properties.relatedEntityIds.uniqueItems,
    true,
  );
  assert.equal(
    definitions.$defs.Warning.properties.evidence.maxItems,
    16,
  );
  assert.equal(
    definitions.$defs.Warning.properties.evidence.items.$ref,
    "../v2/definitions.schema.json#/$defs/AnalysisEvidenceSpan",
  );
  assert.deepEqual(definitions.$defs.VoiceRightsState.enum, [
    "verified",
    "restricted",
    "unknown",
    "prohibited",
  ]);
  assert.deepEqual(definitions.$defs.ProductionRoleType.enum, [
    "primary_narrator",
    "secondary_narrator",
    "named_character",
    "unresolved_speaker",
    "group_or_crowd",
    "quoted_document_or_announcement",
    "internal_thought",
    "custom",
  ]);
  assert.equal(
    definitions.$defs.CustomProductionRole.allOf[1].properties.roleType.const,
    "custom",
  );
  assert.equal(
    definitions.$defs.CustomProductionRole.allOf[1].properties.phase2EntityId
      .type,
    "null",
  );
  assert.deepEqual(
    definitions.$defs.CastingCompatibilityAssessment.properties
      .rightsEligibility.enum,
    [
      "eligible",
      "restricted_requires_acknowledgement",
      "ineligible_unknown",
      "ineligible_prohibited",
    ],
  );
  for (const definitionName of [
    "CastingCompatibilityAssessment",
    "CastingCandidate",
    "CastingConflict",
  ]) {
    const definition = definitions.$defs[definitionName];
    assert.ok(
      definition.required.includes("baseEvidenceFingerprint"),
      `${definitionName} must expose immutable base evidence separately`,
    );
    assert.equal(
      definition.properties.baseEvidenceFingerprint.$ref,
      "#/$defs/Sha256",
    );
    assert.ok(
      definition.required.includes("outputFingerprint"),
      `${definitionName} must fingerprint its public projection`,
    );
  }
  assert.equal(
    definitions.$defs.CastingConflict.properties.metadataOnly.const,
    true,
  );
  assert.equal(
    definitions.$defs.CastingConflict.properties
      .acousticSimilarityClaimed.const,
    false,
  );
  assert.equal(
    definitions.$defs.CastingConflict.properties.roleIds.maxItems,
    300,
  );
  assert.equal(
    definitions.$defs.CastingConflict.properties.voiceProfileIds.maxItems,
    300,
  );
  const voiceReuseCorrection =
    definitions.$defs.CastingCorrection.properties.correctedValue.oneOf.find(
      (value) => Object.hasOwn(value.properties, "approvedRoleIds"),
    );
  assert.equal(voiceReuseCorrection.properties.approvedRoleIds.minItems, 2);
  assert.equal(voiceReuseCorrection.properties.approvedRoleIds.maxItems, 300);
  for (const field of [
    "voiceProfileVersion",
    "voiceEvidenceFingerprint",
    "rightsRecordId",
    "rightsRecordRevision",
    "rightsEvidenceFingerprint",
  ]) {
    assert.ok(
      definitions.$defs.CastAssignment.required.includes(field),
      `assignment must freeze selected voice evidence field ${field}`,
    );
  }
  assert.deepEqual(
    definitions.$defs.CastingCorrectionCategory.enum,
    [
      "select_voice",
      "clear_assignment",
      "lock_assignment",
      "unlock_assignment",
      "mark_intentionally_uncast",
      "change_role_label",
      "change_casting_requirement",
      "acknowledge_restricted_rights",
      "approve_voice_reuse",
      "reject_candidate",
      "record_custom_rationale",
    ],
  );
  assert.equal(
    definitions.$defs.CastingCorrection.properties.immutable.const,
    true,
  );
  assert.equal(
    definitions.$defs.CastingCorrection.properties
      .lockedAgainstAutomation.const,
    true,
  );
  assert.deepEqual(definitions.$defs.CastingGateId.enum, [
    "narrator_casting_review",
    "character_casting_review",
    "complete_cast_review",
  ]);
  assert.equal(
    definitions.$defs.CastingGateReview.properties.openWarningIds.maxItems,
    32,
  );
  assert.equal(
    definitions.$defs.CastingGateReview.properties.acknowledgedWarningIds
      .maxItems,
    32,
  );
  assert.ok(
    definitions.$defs.CastingConflict.properties.resolutionState.enum.includes(
      "superseded",
    ),
  );
  const completeGateRule =
    definitions.$defs.CastingGateReview.allOf[0];
  assert.equal(
    completeGateRule.if.properties.gateId.const,
    "complete_cast_review",
  );
  assert.deepEqual(
    completeGateRule.then.properties.prerequisiteGateIds.prefixItems.map(
      (item) => item.const,
    ),
    [
      "narrator_casting_review",
      "character_casting_review",
    ],
  );
  assert.equal(
    definitions.$defs.CastingReviewDecision.properties.immutable.const,
    true,
  );
  assert.deepEqual(
    definitions.$defs.CastingReviewDecision.properties.decision.enum,
    [
      "approved",
      "changes_requested",
      "rejected",
      "invalidated",
    ],
  );
  assert.deepEqual(
    definitions.$defs.CastingReviewDecision.properties.actor.properties
      .classification.enum,
    ["human", "system"],
  );
  assert.deepEqual(
    definitions.$defs.CastingReviewDecision.allOf,
    [
      {
        if: {
          properties: {
            decision: {
              const: "invalidated",
            },
          },
          required: ["decision"],
        },
        then: {
          properties: {
            actor: {
              properties: {
                classification: {
                  const: "system",
                },
              },
              required: ["classification"],
            },
          },
          required: ["actor"],
        },
        else: {
          properties: {
            actor: {
              properties: {
                classification: {
                  const: "human",
                },
              },
              required: ["classification"],
            },
          },
          required: ["actor"],
        },
      },
    ],
    "only the system may invalidate a casting review; all other decisions are human",
  );
  assert.equal(
    definitions.$defs.ApprovedCastSnapshot.properties.immutable.const,
    true,
  );
});

test("Phase 3A correction categories bind to exact corrected-value variants", async () => {
  const definitions = await readJson(phase3DefinitionsPath);
  const correction = definitions.$defs.CastingCorrection;
  const expectedCouplings = new Map([
    ["select_voice", ["voiceProfileId"]],
    ["clear_assignment", ["expectedAssignmentId"]],
    ["lock_assignment", ["assignmentId"]],
    ["unlock_assignment", ["lockedAssignmentId"]],
    ["mark_intentionally_uncast", ["intentionallyUncast"]],
    ["change_role_label", ["effectiveDisplayLabel"]],
    ["change_casting_requirement", ["requirement"]],
    [
      "acknowledge_restricted_rights",
      ["rightsRecordId", "rightsRecordRevision"],
    ],
    ["approve_voice_reuse", ["conflictId", "approvedRoleIds"]],
    ["reject_candidate", ["candidateId"]],
    ["record_custom_rationale", ["rationale"]],
  ]);
  const fieldKey = (fields) => [...fields].sort().join("|");

  assert.deepEqual(
    definitions.$defs.CastingCorrectionCategory.enum,
    [...expectedCouplings.keys()],
  );
  assert.equal(correction.oneOf.length, expectedCouplings.size);
  assert.equal(
    correction.properties.correctedValue.oneOf.length,
    expectedCouplings.size,
  );

  const couplingByCategory = new Map(
    correction.oneOf.map((branch) => [
      branch.properties.category.const,
      [...branch.properties.correctedValue.required].sort(),
    ]),
  );
  assert.equal(couplingByCategory.size, expectedCouplings.size);

  const valueShapeByFields = new Map(
    correction.properties.correctedValue.oneOf.map((variant) => {
      const requiredFields = [...variant.required].sort();
      assert.equal(variant.type, "object");
      assert.equal(variant.additionalProperties, false);
      assert.deepEqual(
        Object.keys(variant.properties).sort(),
        requiredFields,
      );
      return [fieldKey(requiredFields), variant];
    }),
  );
  assert.equal(valueShapeByFields.size, expectedCouplings.size);

  for (const [category, expectedFields] of expectedCouplings) {
    const couplingFields = couplingByCategory.get(category);
    assert.deepEqual(
      couplingFields,
      [...expectedFields].sort(),
      `${category} must require its exact correctedValue fields`,
    );
    assert.ok(
      valueShapeByFields.has(fieldKey(expectedFields)),
      `${category} must reference one closed correctedValue variant`,
    );

    for (const [shapeCategory, shapeFields] of expectedCouplings) {
      const couplingAcceptsShape = couplingFields.every((field) =>
        shapeFields.includes(field),
      );
      assert.equal(
        couplingAcceptsShape,
        shapeCategory === category,
        `${category} must reject the ${shapeCategory} correctedValue shape`,
      );
    }
  }

  const voiceReuseShape = valueShapeByFields.get(
    fieldKey(["conflictId", "approvedRoleIds"]),
  );
  assert.equal(
    voiceReuseShape.properties.approvedRoleIds.minItems,
    2,
  );
  assert.equal(
    voiceReuseShape.properties.approvedRoleIds.maxItems,
    300,
  );
  assert.equal(
    voiceReuseShape.properties.approvedRoleIds.uniqueItems,
    true,
  );
});

test("Phase 3A durable job stages and frozen Phase 2 prerequisites are complete", async () => {
  const definitions = await readJson(phase3DefinitionsPath);
  assert.deepEqual(definitions.$defs.CastingJobStage.enum, [
    "validate_phase_2_approvals",
    "freeze_source_analysis_evidence",
    "load_voice_catalog_revision",
    "create_production_roles",
    "evaluate_role_constraints",
    "generate_bounded_candidates",
    "evaluate_differentiation_conflicts",
    "publish_casting_run",
    "publish_reviewable_cast_snapshot",
  ]);
  for (const field of [
    "progress",
    "checkpoint",
    "attempt",
    "retryPolicy",
    "failurePolicy",
    "resumeOfCastingRunId",
    "retryOfCastingRunId",
    "retryClassification",
    "cancellationRequested",
    "idempotencyFingerprint",
    "failure",
  ]) {
    assert.ok(
      definitions.$defs.CastingRun.required.includes(field),
      `casting run must require durable control field ${field}`,
    );
  }
  assert.equal(
    definitions.$defs.CastingRun.properties.failurePolicy.const,
    "fail_closed_preserve_effective_cast_snapshot",
  );
  assert.deepEqual(
    definitions.$defs.CastingPhase2Prerequisites.properties
      .phase2GateDecisionIds.required,
    [
      "storyStructureReview",
      "characterRegistryReview",
      "dialogueAttributionReview",
      "wholeBookAnalysisReview",
    ],
  );
  for (const field of [
    "sourceDocumentId",
    "sourceRevision",
    "extractionId",
    "extractionRevision",
    "extractedTextSha256",
    "importReviewDecisionId",
    "analysisRunId",
    "analysisSnapshotId",
    "analysisSnapshotRevision",
    "analysisSnapshotFingerprint",
    "analysisCorrectionSetFingerprint",
    "characterRegistryFingerprint",
    "phase2GateDecisionIds",
    "evidenceFingerprint",
  ]) {
    assert.ok(
      definitions.$defs.CastingPhase2Prerequisites.required.includes(
        field,
      ),
      `casting prerequisite must require ${field}`,
    );
  }
});

test("Phase 3A CI proof schemas require casting and exact process evidence without private text", async () => {
  const definitions = await readJson(phase3DefinitionsPath);
  const packaged = definitions.$defs.Phase3PackagedE2eResult;
  const manifest = definitions.$defs.Phase3BuildEvidenceManifest;

  assert.equal(packaged.properties.schemaVersion.const, "5.0.0");
  assert.equal(manifest.properties.schemaVersion.const, "4.0.0");
  assert.equal(
    definitions.$defs.Phase3PackagedFlow.prefixItems.length,
    32,
  );
  assert.equal(
    definitions.$defs.Phase3PackagedFlow.prefixItems.at(-1).const,
    "verify_final_owned_process_exit",
  );
  assert.equal(
    definitions.$defs.Phase3SuccessfulScreenshot.properties
      .captureStatus.const,
    "success",
  );
  assert.equal(
    packaged.properties.fixture.const,
    "fixtures/synthetic-story/sample-story.docx.base64",
  );
  assert.equal(
    definitions.$defs.Phase3ProcessOwnershipProof.properties
      .ownershipEstablished.const,
    true,
  );
  assert.equal(
    definitions.$defs.Phase3ProcessOwnershipProof.properties
      .launchShutdowns.minItems,
    2,
  );
  assert.equal(
    definitions.$defs.Phase3ProcessOwnershipProof.properties
      .launchShutdowns.maxItems,
    2,
  );
  assert.equal(
    definitions.$defs.Phase3LaunchShutdownProof.properties.electron
      .properties.exitCode.const,
    0,
  );
  assert.equal(
    definitions.$defs.Phase3LaunchShutdownProof.properties.electron
      .properties.forceKillUsed.const,
    false,
  );
  assert.equal(
    definitions.$defs.Phase3LaunchShutdownProof.properties.service
      .properties.method.const,
    "stdin_eof",
  );
  assert.equal(
    definitions.$defs.Phase3LaunchShutdownProof.properties.service
      .properties.exitCode.const,
    0,
  );
  assert.equal(
    definitions.$defs.Phase3LaunchShutdownProof.properties.service
      .properties.signalCode.const,
    null,
  );
  assert.equal(
    definitions.$defs.Phase3LaunchShutdownProof.properties.service
      .properties.forceKillUsed.const,
    false,
  );
  assert.equal(
    definitions.$defs.Phase3ProcessOwnershipProof.properties.forcedPids
      .maxItems,
    0,
  );
  assert.equal(
    definitions.$defs.Phase3ProcessOwnershipProof.properties
      .remainingOwnedPids.maxItems,
    0,
  );
  assert.equal(
    definitions.$defs.Phase3ProcessOwnershipProof.properties
      .unrelatedProcessesTerminated.const,
    false,
  );
  assert.deepEqual(
    definitions.$defs.Phase3RoleVoiceAssignmentEvidence.required,
    [
      "assignmentId",
      "roleId",
      "roleType",
      "voiceProfileId",
      "authority",
      "rightsState",
    ],
  );
  assert.equal(
    definitions.$defs.Phase3RoleVoiceAssignmentEvidence.properties
      .roleType.$ref,
    "#/$defs/ProductionRoleType",
  );
  assert.equal(
    definitions.$defs.Phase3RoleVoiceAssignmentEvidence.properties
      .authority.const,
    "human_locked",
  );
  assert.deepEqual(
    definitions.$defs.Phase3RoleVoiceAssignmentEvidence.properties
      .rightsState.enum,
    ["verified", "restricted"],
  );
  assert.equal(
    definitions.$defs.Phase3CastingProof.properties
      .narratorAssignment.$ref,
    "#/$defs/Phase3NarratorRoleVoiceAssignmentEvidence",
  );
  assert.deepEqual(
    definitions.$defs.Phase3NarratorRoleVoiceAssignmentEvidence
      .allOf[1].properties.roleType.enum,
    ["primary_narrator", "secondary_narrator"],
  );
  assert.deepEqual(
    definitions.$defs.Phase3CharacterRoleVoiceAssignmentEvidence
      .allOf[1].properties.roleType.enum,
    [
      "named_character",
      "unresolved_speaker",
      "group_or_crowd",
      "quoted_document_or_announcement",
      "internal_thought",
      "custom",
    ],
  );
  assert.equal(
    definitions.$defs.Phase3CastingProof.properties
      .characterAssignments.minItems,
    2,
  );
  assert.equal(
    definitions.$defs.Phase3CastingProof.properties
      .characterAssignments.maxItems,
    300,
  );
  assert.equal(
    Object.values(
      definitions.$defs.Phase3Assertions.properties,
    ).every((property) => property.const === true),
    true,
  );
  assert.equal(
    manifest.properties.artifacts.$ref,
    "../v2/definitions.schema.json#/$defs/Phase2BuildEvidenceManifest/properties/artifacts",
  );
  assert.equal(
    manifest.properties.assertions.$ref,
    "../v2/definitions.schema.json#/$defs/Phase2BuildEvidenceManifest/properties/assertions",
  );
  assert.equal(
    manifest.properties.voiceCastingContract.$ref,
    "#/$defs/Phase3VoiceCastingEvidence",
  );
  assert.deepEqual(manifest.required, [
    "schemaVersion",
    "artifactPathScope",
    "workflowHeadSha",
    "testedCheckoutSha",
    "pullRequestHeadSha",
    "appVersion",
    "artifacts",
    "assertions",
    "secureIngest",
    "storyAnalysisContract",
    "packagedE2e",
    "voiceCastingContract",
    "testTimestamp",
    "runner",
  ]);
  assert.equal(
    definitions.$defs.Phase3VoiceCastingEvidence.properties
      .correctionPersistence.const,
    true,
  );
  assert.equal(
    definitions.$defs.Phase3VoiceCastingEvidence.properties
      .assignments.properties.narratorAssignment.$ref,
    "#/$defs/Phase3NarratorRoleVoiceAssignmentEvidence",
  );
  assert.equal(
    definitions.$defs.Phase3VoiceCastingEvidence.properties
      .assignments.properties.characterAssignments.items.$ref,
    "#/$defs/Phase3CharacterRoleVoiceAssignmentEvidence",
  );

  const proofSchemas = collectReachableDefinitions(definitions, [
    "Phase3BuildEvidenceManifest",
    "Phase3PackagedE2eResult",
  ]);
  const serializedProofSchemas = JSON.stringify(proofSchemas);
  for (const privateContentField of [
    "exactText",
    "excerptText",
    "manuscript",
    "sourceText",
    "textContent",
    "licenseDocument",
    "credential",
  ]) {
    assert.equal(
      serializedProofSchemas.includes(`"${privateContentField}"`),
      false,
      `CI proof schemas must not admit ${privateContentField}`,
    );
  }
});
