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

test("every public entity has a versioned entry point", async () => {
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
