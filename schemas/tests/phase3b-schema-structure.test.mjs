import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const testDirectory = path.dirname(fileURLToPath(import.meta.url));
const phase3Directory = path.resolve(testDirectory, "..", "v3");
const definitionsPath = path.join(
  phase3Directory,
  "local-speech-auditions.schema.json",
);

const entries = {
  SpeechProviderAdapterDescriptor:
    "speech-provider-adapter-descriptor.schema.json",
  LocalSpeechRuntimeDescriptor:
    "local-speech-runtime-descriptor.schema.json",
  SpeechRuntimeProfile: "speech-runtime-profile.schema.json",
  SpeechRuntimeInstance: "speech-runtime-instance.schema.json",
  SpeechRuntimeHealth: "speech-runtime-health.schema.json",
  ModelPackageManifest: "model-package-manifest.schema.json",
  ModelInstallationRecord: "model-installation-record.schema.json",
  ModelVerificationRecord: "model-verification-record.schema.json",
  VoiceRuntimeBinding: "voice-runtime-binding.schema.json",
  TextNormalizationPlan: "text-normalization-plan.schema.json",
  PronunciationDictionary: "pronunciation-dictionary.schema.json",
  PronunciationEntry: "pronunciation-entry.schema.json",
  CompiledPronunciationPlan:
    "compiled-pronunciation-plan.schema.json",
  AuditionSession: "audition-session.schema.json",
  AuditionScript: "audition-script.schema.json",
  AuditionScriptDetail: "audition-script-detail.schema.json",
  SpeechPreviewRequest: "speech-preview-request.schema.json",
  SpeechProviderRequest: "speech-provider-request.schema.json",
  SpeechProviderResult: "speech-provider-result.schema.json",
  AuditionClip: "audition-clip.schema.json",
  AudioArtifactRecord: "audio-artifact-record.schema.json",
  AuditionCacheRecord: "audition-cache-record.schema.json",
  AudioQualityRecord: "audio-quality-record.schema.json",
  AuditionReview: "audition-review.schema.json",
  AuditionReviewDecision: "audition-review-decision.schema.json",
  AuditionRoleStatus: "audition-role-status.schema.json",
  VoiceReadinessSnapshot: "voice-readiness-snapshot.schema.json",
};

async function readJson(filePath) {
  return JSON.parse(await readFile(filePath, "utf8"));
}

function collectReferences(value, references = []) {
  if (Array.isArray(value)) {
    value.forEach((entry) => collectReferences(entry, references));
  } else if (value && typeof value === "object") {
    if (typeof value.$ref === "string") {
      references.push(value.$ref);
    }
    Object.values(value).forEach((entry) =>
      collectReferences(entry, references),
    );
  }
  return references;
}

function requiredDeclarationGaps(value, location = "#", gaps = []) {
  if (Array.isArray(value)) {
    value.forEach((entry, index) =>
      requiredDeclarationGaps(entry, `${location}/${index}`, gaps),
    );
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
  Object.entries(value).forEach(([name, entry]) =>
    requiredDeclarationGaps(entry, `${location}/${name}`, gaps),
  );
  return gaps;
}

test("Phase 3B local speech contracts have closed versioned entry points", async () => {
  const definitions = await readJson(definitionsPath);
  assert.equal(
    definitions.$schema,
    "https://json-schema.org/draft/2020-12/schema",
  );
  assert.equal(
    definitions.$id,
    "https://schemas.cinematic-story-studio.dev/v3/local-speech-auditions.schema.json",
  );

  for (const [entityName, fileName] of Object.entries(entries)) {
    assert.ok(definitions.$defs[entityName], `missing Phase 3B $defs/${entityName}`);
    assert.equal(
      definitions.$defs[entityName].additionalProperties,
      false,
      `${entityName} must reject unknown public fields`,
    );
    const entry = await readJson(path.join(phase3Directory, fileName));
    assert.equal(entry.title, entityName);
    assert.equal(
      entry.$ref,
      `local-speech-auditions.schema.json#/$defs/${entityName}`,
    );
  }

  for (const reference of collectReferences(definitions).filter((entry) =>
    entry.startsWith("#/$defs/"),
  )) {
    const definitionName = reference.slice("#/$defs/".length);
    assert.ok(
      Object.hasOwn(definitions.$defs, definitionName),
      `unresolved Phase 3B reference ${reference}`,
    );
  }
  assert.deepEqual(requiredDeclarationGaps(definitions), []);
});

test("Phase 3B schemas pin lifecycle, scale, audio, and governance bounds", async () => {
  const { $defs: definitions } = await readJson(definitionsPath);
  assert.equal(definitions.ContractVersion.const, "1.0.0");
  assert.equal(definitions.ProtocolVersion.const, "1.0.0");
  assert.equal(
    definitions.LocalSpeechRuntimeDescriptor.properties.shellUsed.const,
    false,
  );
  assert.equal(
    definitions.LocalSpeechRuntimeDescriptor.properties.networkPolicy.const,
    "deny_during_synthesis",
  );
  assert.equal(
    definitions.SpeechRuntimeInstance.properties.handshakeAuthenticated.const,
    true,
  );
  assert.equal(
    definitions.SpeechRuntimeInstance.properties.networkPolicy.const,
    "python_socket_api_denied",
  );
  assert.deepEqual(
    definitions.SpeechRuntimeInstance.properties.observedNetworkRequestCount.oneOf,
    [{ type: "null" }, { type: "integer", minimum: 0 }],
  );
  assert.deepEqual(
    definitions.SpeechRuntimeInstance.properties.restartReconciliation.oneOf,
    [
      { type: "null" },
      { $ref: "#/$defs/SpeechRuntimeRestartReconciliation" },
    ],
  );
  assert.equal(
    definitions.SpeechRuntimeRestartReconciliation.properties.reasonCode.const,
    "SERVICE_RESTART_INTERRUPTED",
  );
  for (const field of [
    "ownershipConfirmed",
    "gracefulShutdownConfirmed",
    "processExitConfirmed",
  ]) {
    assert.equal(
      definitions.SpeechRuntimeRestartReconciliation.properties[field].const,
      false,
    );
  }
  for (const field of [
    "shutdownAcknowledged",
    "gracefulShutdownConfirmed",
    "terminatedByParent",
    "ownershipConfirmed",
    "confirmedExited",
    "ownedProcessesConfirmedExited",
  ]) {
    assert.deepEqual(
      definitions.SpeechRuntimeInstance.properties[field].type,
      ["boolean", "null"],
    );
  }
  assert.equal(
    definitions.SpeechRuntimeInstance.properties.jobObjectAssigned.type,
    "boolean",
  );
  assert.equal(
    definitions.SpeechRuntimeInstance.properties.deniedNetworkAttemptCount
      .maximum,
    1_000_000,
  );
  assert.equal(
    definitions.ModelInstallationRecord.properties.actionReasonCode.maxLength,
    1_000,
  );
  assert.match(
    definitions.ModelInstallationRecord.properties.actionReasonCode.pattern,
    /u0000/u,
  );
  assert.deepEqual(
    definitions.SpeechProviderRequest.properties.runtimeInstanceId.oneOf,
    [{ type: "null" }, { $ref: "#/$defs/Id" }],
  );
  assert.equal(definitions.ProviderRequestExecutionDetails.oneOf.length, 2);
  const [invocationDetails, retryDetails] =
    definitions.ProviderRequestExecutionDetails.oneOf;
  assert.equal(invocationDetails.additionalProperties, false);
  assert.equal(
    invocationDetails.required.includes("voiceRuntimeBindingFingerprint"),
    true,
  );
  assert.equal(
    invocationDetails.required.includes("restrictedLocalUseAcknowledged"),
    true,
  );
  assert.equal(retryDetails.additionalProperties, false);
  assert.equal(retryDetails.required.includes("attempt"), true);
  assert.equal(
    retryDetails.required.includes("supersedesProviderRequestId"),
    true,
  );
  assert.equal(
    definitions.ProviderRequestProvenance.required.includes("inputFingerprint"),
    true,
  );
  assert.equal(definitions.ModelPackageManifest.properties.files.maxItems, 4096);
  assert.deepEqual(
    definitions.ModelPackageManifest.properties.sourceClassification.enum,
    [
      "official_release",
      "maintainer_referenced_conversion",
      "repository_fixture",
    ],
  );
  assert.equal(definitions.ModelPackageFile.properties.executable.const, false);
  assert.equal(
    definitions.PronunciationDictionary.properties.entryCount.maximum,
    1000,
  );
  assert.deepEqual(
    definitions.CompiledPronunciationPlan.properties.scopeContext.required,
    ["chapterId", "sceneId", "customScopeIds"],
  );
  assert.equal(
    definitions.CompiledPronunciationPlan.properties.scopeContext.properties
      .customScopeIds.maxItems,
    50,
  );
  assert.equal(definitions.AuditionSession.properties.scriptCount.maximum, 20);
  assert.equal(definitions.AuditionSession.properties.clipCount.maximum, 2000);
  assert.equal(Object.hasOwn(definitions.AuditionScript.properties, "text"), false);
  assert.equal(definitions.AuditionScriptDetail.required.includes("text"), true);
  assert.equal(definitions.AuditionScriptDetail.properties.text.maxLength, 4000);
  assert.equal(definitions.AuditionRoleStatus.allOf.length, 3);
  assert.deepEqual(
    definitions.AuditionRoleStatus.allOf.map(
      (condition) => condition.if.properties.runtimeBindingStatus.const,
    ),
    ["compatible", "incompatible", "unavailable"],
  );
  assert.equal(definitions.AudioArtifactRecord.properties.byteSize.maximum, 25165824);
  assert.equal(definitions.AudioArtifactRecord.properties.sampleRateHz.const, 24000);
  assert.equal(definitions.AudioArtifactRecord.properties.channels.const, 1);
  assert.deepEqual(
    definitions.AudioArtifactRecord.properties.availability.enum,
    ["present", "purged", "corrupt", "quarantined"],
  );
  assert.equal(
    definitions.AudioArtifactRecord.properties.playbackEligible.type,
    "boolean",
  );
  assert.equal(
    definitions.AudioArtifactRecord.allOf[0].then.properties.playbackEligible.const,
    true,
  );
  assert.equal(
    definitions.AudioArtifactRecord.allOf[0].else.properties.playbackEligible.const,
    false,
  );
  assert.equal(
    definitions.AudioQualityRecord.properties.subjectiveQualityClaimed.const,
    false,
  );
  assert.equal(
    definitions.VoiceReadinessSnapshot.properties.authorizesFullBookRendering.const,
    false,
  );
  assert.deepEqual(definitions.AuditionReview.properties.gateId.enum, [
    "per_role_audition_review",
    "narrator_audition_review",
    "character_audition_review",
    "pronunciation_review",
    "voice_readiness_review",
  ]);
});

test("Phase 3B schemas expose no path, token, credential, or generic markup authority", async () => {
  const definitions = await readJson(definitionsPath);
  const serialized = JSON.stringify(definitions);
  for (const prohibitedField of [
    "absolutePath",
    "filePath",
    "fileUrl",
    "bearerToken",
    "credential",
    "modelUrl",
    "commandString",
    "rawSsml",
  ]) {
    assert.equal(
      serialized.includes(`\"${prohibitedField}\"`),
      false,
      `Phase 3B contracts must not expose ${prohibitedField}`,
    );
  }
  assert.equal(
    definitions.$defs.ModelPackageFile.properties.relativePath.pattern.includes(
      "\\.\\.",
    ),
    true,
  );
  assert.match(
    definitions.$defs.PronunciationEntry.properties.pronunciation.pattern,
    /<>/,
  );
});
