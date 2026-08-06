import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const testDirectory = path.dirname(fileURLToPath(import.meta.url));
const repositoryRoot = path.resolve(testDirectory, "..", "..");
const catalogPath = path.join(
  repositoryRoot,
  "apps",
  "local-service",
  "src",
  "cinematic_story_service",
  "catalogs",
  "synthetic_voice_catalog.v1.json",
);
const contractsPath = path.join(
  repositoryRoot,
  "packages",
  "contracts",
  "src",
  "voice-casting.ts",
);

function canonicalize(value) {
  if (Array.isArray(value)) {
    return value.map(canonicalize);
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, canonicalize(value[key])]),
    );
  }
  return value;
}

function sha256(value) {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

async function readCatalog() {
  return JSON.parse(await readFile(catalogPath, "utf8"));
}

test("synthetic voice catalog has a deterministic governed fingerprint", async () => {
  const catalog = await readCatalog();
  const fingerprint = catalog.fingerprint;
  const fingerprintInput = structuredClone(catalog);
  delete fingerprintInput.fingerprint;
  delete fingerprintInput.catalogRevision.catalogFingerprint;

  assert.equal(catalog.contractVersion, "3.0.0");
  assert.equal(
    catalog.catalogRevision.catalogRevisionId,
    "synthetic-voice-catalog-v1@1.0.0",
  );
  assert.equal(catalog.catalogRevision.revision, 1);
  assert.equal(catalog.catalogRevision.semanticVersion, "1.0.0");
  assert.equal(
    catalog.catalogRevision.rightsPolicyId,
    "voice-rights-policy-v1",
  );
  assert.equal(
    sha256(JSON.stringify(canonicalize(fingerprintInput))),
    fingerprint,
  );
  assert.equal(catalog.catalogRevision.catalogFingerprint, fingerprint);
  assert.equal(
    fingerprint,
    "68d116d1f66e4ea4bcceabfd0520fd889cf9da3074ee1b9186c43c285575c25f",
  );
});

test("catalog references are complete, fictional, and contain no audio capability", async () => {
  const catalog = await readCatalog();
  const providerIds = new Set(
    catalog.providers.map((provider) => provider.providerId),
  );
  const modelIds = new Set(catalog.models.map((model) => model.modelId));
  const voiceIds = new Set(
    catalog.voices.map((voice) => voice.voiceProfileId),
  );
  const providerVoiceKeys = new Set(
    catalog.voices.map(
      (voice) =>
        `${voice.providerId}\u0000${voice.modelId}\u0000${voice.providerVoiceId}`,
    ),
  );
  const rightsByVoice = new Map(
    catalog.rights.map((record) => [record.voiceProfileId, record]),
  );

  assert.equal(providerIds.size, catalog.providers.length);
  assert.equal(modelIds.size, catalog.models.length);
  assert.equal(voiceIds.size, catalog.voices.length);
  assert.equal(providerVoiceKeys.size, catalog.voices.length);
  assert.equal(catalog.voices.length, catalog.rights.length);
  assert.deepEqual(
    catalog.catalogRevision.providerDescriptorIds,
    catalog.providers.map((provider) => provider.providerId),
  );
  assert.deepEqual(
    catalog.catalogRevision.modelDescriptorIds,
    catalog.models.map((model) => model.modelId),
  );
  assert.deepEqual(
    catalog.catalogRevision.voiceProfileIds,
    catalog.voices.map((voice) => voice.voiceProfileId),
  );

  for (const provider of catalog.providers) {
    assert.match(provider.providerId, /^synthetic-/);
    assert.equal(provider.synthesisImplemented, false);
    assert.deepEqual(provider.outputCapability.formats, ["unknown"]);
    assert.deepEqual(provider.outputCapability.sampleRatesHz, []);
  }
  for (const model of catalog.models) {
    assert.ok(providerIds.has(model.providerId));
    assert.match(model.modelId, /^synthetic-/);
    assert.deepEqual(model.capability.outputCapability.formats, ["unknown"]);
    assert.deepEqual(model.capability.outputCapability.sampleRatesHz, []);
  }
  for (const voice of catalog.voices) {
    assert.ok(providerIds.has(voice.providerId));
    assert.ok(modelIds.has(voice.modelId));
    assert.match(voice.voiceProfileId, /^synthetic-/);
    assert.match(voice.providerVoiceId, /^fixture-/);
    assert.match(voice.displayLabel, /^Synthetic /);
    assert.ok(rightsByVoice.has(voice.voiceProfileId));
    assert.equal(
      rightsByVoice.get(voice.voiceProfileId).rightsRecordId,
      voice.rightsRecordId,
    );
    assert.equal(
      rightsByVoice.get(voice.voiceProfileId).state,
      voice.rightsState,
    );
    assert.equal(
      rightsByVoice.get(voice.voiceProfileId).consentStatus,
      voice.consentStatus,
    );
    assert.equal(
      voice.voiceCloningClassification ===
        "not_cloned_synthetic_fixture" ||
        voice.voiceCloningClassification === "unknown" ||
        voice.voiceCloningClassification === "prohibited",
      true,
    );
  }

  const serialized = JSON.stringify(catalog).toLowerCase();
  for (const forbidden of [
    ".wav",
    ".mp3",
    ".m4b",
    "celebrity",
    "impersonat",
    "biometric",
    "manuscript",
  ]) {
    assert.equal(serialized.includes(forbidden), false);
  }
});

test("catalog provides the complete governed edge-case matrix", async () => {
  const catalog = await readCatalog();
  const narratorCandidates = catalog.voices.filter(
    (voice) =>
      voice.voiceProfileId.startsWith("synthetic-narrator-") &&
      voice.narrationSuitability === "preferred" &&
      voice.longFormSuitability === "preferred",
  );
  const characterCandidates = catalog.voices.filter(
    (voice) =>
      voice.voiceProfileId.startsWith("synthetic-character-") &&
      voice.characterRoleSuitability.length > 0,
  );
  const rightsStates = new Set(catalog.rights.map((record) => record.state));
  const voiceStates = new Set(catalog.voices.map((voice) => voice.state));
  const locales = new Set(catalog.voices.map((voice) => voice.locale));
  const textures = new Set(
    catalog.voices.map((voice) => voice.vocalTexture),
  );
  const similarityGroups = new Map();
  const reuseGroups = new Map();

  for (const voice of catalog.voices) {
    if (voice.metadataSimilarityGroup) {
      similarityGroups.set(
        voice.metadataSimilarityGroup,
        (similarityGroups.get(voice.metadataSimilarityGroup) ?? 0) + 1,
      );
    }
    if (voice.reuseRiskGroup) {
      reuseGroups.set(
        voice.reuseRiskGroup,
        (reuseGroups.get(voice.reuseRiskGroup) ?? 0) + 1,
      );
    }
  }

  assert.ok(narratorCandidates.length >= 2);
  assert.ok(characterCandidates.length >= 12);
  assert.ok(locales.size >= 3);
  assert.ok(textures.size >= 6);
  assert.deepEqual(
    [...rightsStates].sort(),
    ["prohibited", "restricted", "unknown", "verified"],
  );
  for (const requiredState of [
    "active",
    "unavailable",
    "deprecated",
    "blocked",
  ]) {
    assert.ok(voiceStates.has(requiredState));
  }
  assert.ok(
    catalog.providers.some(
      (provider) =>
        provider.providerType === "cloud_capable_disabled" &&
        provider.runtimeAvailability === "disabled" &&
        provider.networkUseRequired &&
        provider.credentialsRequired,
    ),
  );
  assert.ok(
    catalog.voices.some(
      (voice) =>
        voice.language === "es" &&
        voice.locale === "es-MX" &&
        voice.knownLimitations.some((value) =>
          value.includes("language-mismatch"),
      ),
    ),
  );
  const disabledProviderIds = new Set(
    catalog.providers
      .filter((provider) => provider.runtimeAvailability === "disabled")
      .map((provider) => provider.providerId),
  );
  assert.ok(
    catalog.voices.some((voice) =>
      disabledProviderIds.has(voice.providerId),
    ),
  );
  assert.ok(
    catalog.voices.some(
      (voice) =>
        voice.longFormSuitability === "unsuitable" &&
        voice.maximumRecommendedWords === 800,
    ),
  );
  assert.ok([...similarityGroups.values()].some((count) => count >= 2));
  assert.ok([...reuseGroups.values()].some((count) => count >= 2));
});

test("casting profile constants and canonical fingerprint stay pinned", async () => {
  const source = await readFile(contractsPath, "utf8");
  const canonicalMatch = source.match(
    /^export const GOVERNED_VOICE_CASTING_PROFILE_CANONICAL_JSON\s*=\s*\n?\s*'([^']+)' as const;/mu,
  );
  const fingerprintMatch = source.match(
    /^export const GOVERNED_VOICE_CASTING_PROFILE_FINGERPRINT\s*=\s*\n?\s*"([a-f0-9]{64})" as const;/mu,
  );

  assert.ok(canonicalMatch);
  assert.ok(fingerprintMatch);
  assert.equal(sha256(canonicalMatch[1]), fingerprintMatch[1]);
  assert.equal(
    fingerprintMatch[1],
    "5377949573018b5d3a4f4cd343392155071640364d3ba36be80a1bf4ad58de97",
  );
  const profile = JSON.parse(canonicalMatch[1]);
  assert.equal(profile.profileId, "governed-voice-casting-v1@1.0.1");
  assert.equal(profile.producerId, "voice-casting-orchestrator@1.0.0");
  assert.equal(profile.rightsPolicyId, "voice-rights-policy-v1");
  assert.equal(profile.providerNeutral, true);
  assert.equal(profile.externalSemanticDependency, false);
  assert.deepEqual(profile.compatibilityRules, [
    "hard_constraints_fail_closed",
    "soft_preferences_score_separately",
    "unknown_remains_unknown",
    "no_automatic_assignment",
    "declared_metadata_only",
  ]);
  assert.deepEqual(profile.rightsEligibilityRules, [
    "verified_eligible",
    "restricted_requires_acknowledgement",
    "unknown_ineligible",
    "prohibited_ineligible",
    "restricted_private_audition_pending_evidence",
  ]);
  assert.deepEqual(profile.limits, {
    defaultPageSize: 50,
    maximumConflictsPerRun: 10000,
    maximumExplanationCodePoints: 2000,
    maximumFinalCandidatesPerRole: 12,
    maximumHardConstraintResults: 16,
    maximumPageSize: 200,
    maximumPreReductionCandidatesPerRole: 50,
    maximumProductionRoles: 300,
    maximumSoftPreferenceResults: 16,
    maximumVoiceProfiles: 5000,
    maximumVoiceReusePerProfile: 2,
    maximumWarningsPerEntity: 32,
  });
});
