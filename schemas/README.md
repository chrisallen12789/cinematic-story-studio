# Contract schemas

`v1/definitions.schema.json` is the canonical JSON Schema 2020-12 bundle for the
version 1 domain. Each `*.schema.json` beside it is a stable entry point for one
persisted entity or runtime contract.

Schema versions are part of persisted data. A breaking change requires a new
version directory and an explicit migration; changing a version 1 meaning in
place is not permitted. Additive changes must remain compatible with stored
version 1 documents.

Human corrections and approval decisions are immutable event records. Storage
code must append a superseding human record rather than update or delete an
earlier record. Runtime agents may propose new analysis, but must not supersede
or change a human-authoritative value.

Phase 1 adds entry points for `DocumentProbe`,
`DocumentExtractionResult`, `ImportManifest`, `ParserExecutionRecord`,
`ExtractedSection`, `SourceLocation`, `ExtractionWarning`, and
`IngestImportReviewDecisionRecord`. Except for the deliberately disambiguated
decision-record name, these are camel-case JSON projections of the same-named
frozen dataclasses in `cinematic_story_service.document_ingest`; schema tests
assert their required field sets exactly. `IngestImportReviewDecisionRecord`
maps the Python `document_ingest.ImportReviewDecision` dataclass and avoids a
name collision with the public TypeScript `ImportReviewDecision` string union.
`DocumentExtractionResult` is deliberately distinct from the smaller public
API `DocumentExtractionSummary`.

`DocumentExtractionRequest.source_path` is an internal private `Path` and is
intentionally not a serializable public schema field. The request is constructed
only after the service resolves an application-owned source key and revalidates
file type, size, and SHA-256. The JSON schemas therefore cannot grant arbitrary
filesystem authority.

The ingest `IngestImportReviewDecisionRecord` is adapter-domain decision
evidence (`actorClassification: human`). The authenticated API accepts the
separate `DecideImportReviewRequest`, whose `decision` uses the TypeScript
`ImportReviewDecision` union, and returns the existing immutable
`ApprovalDecision` plus the current `ImportReview` projection. Append-only
storage binds those decisions to source/extraction revisions and invalidates
them for changed input.

`SourceLocation.member` is an archive-relative logical location. It is never a
user filesystem path. Exact-text locations use half-open Unicode-code-point
offsets; package locations use a member with null offsets, and PDF locations
use a one-based page with null offsets. Canonical section offsets remain
distinct from the Phase 0 UTF-8 byte-span contract.
`ParserExecutionRecord` mirrors adapter/dependency/timing,
retryability, status, `networkAccessPermitted: false`, the exact
`ParserLimitsProfile`, and its fingerprint. The profile's
`parserDeadlineMs` is a parent-enforced wall-clock deadline for one spawned
parser attempt, including its ownership handshake. On Windows,
`parserProcessMemoryBytes` is enforced for each parser process through the
owned Job Object. `SecureIngestBoundaryLimits` separately names the service source
ceiling and bounded Import Review preview; neither is silently covered by the
parser-profile fingerprint. Job/extraction IDs and the same parser-profile
fingerprint are stored on the separate durable `ParserExecutionRow`. Neither
representation admits command output, manuscript excerpts, environment
values, or absolute paths.

Run the dependency-free structural checks from the repository root:

```text
node --test schemas/tests/schema-structure.test.mjs
```

Full instance validation should be performed at every service boundary with a
JSON Schema 2020-12 validator selected by the owning application package.

## Phase 2 whole-book analysis

`v2/definitions.schema.json` defines the frozen whole-book analysis profile,
eleven runtime-agent outputs, immutable snapshots, bounded evidence and exact
text, character/dialogue/POV/location/time/relationship/emotion/intent/
continuity entities, typed human corrections, and four review gates. The
profile canonical JSON and SHA-256 fingerprint are constants and any semantic
profile change requires a new semantic version and fingerprint.

The fingerprint covers every service-enforced analyzer ceiling that can change
run acceptance, publication, or output: 150,000 words, 250,000 entities,
32 KiB agent envelopes, 64 MiB durable checkpoints, at most five published
analysis snapshot stages, and the page/evidence/candidate/exact-text/warning
bounds. HTTP and
human-correction transport limits are control-plane validation rather than
analyzer behavior; accepted corrections carry their own immutable
fingerprints.

Every entity entry point is closed to unknown fields. A specialized entity ID
(`chapterId`, `sceneId`, `characterId`, and similar) must equal the inherited
`entityId`; JSON Schema 2020-12 cannot express arbitrary cross-field equality,
so services and clients enforce that invariant after schema validation.
Likewise, source-span offsets and exact-text hashes are checked against the
approved canonical extraction rather than trusted from a payload.

`AnalysisCorrection` uses an exact category/collection/patch `oneOf` mapping
for all sixteen governed correction categories. There is no arbitrary JSON
patch escape hatch. Create requests and persisted corrections require a
nonblank human reason of at most 1,000 Unicode code points. Human structure
add/remove/move corrections project through the closed `effectiveBoundary`
shape. Their create-request span contains frozen source identity and selected
offsets but no caller-supplied text hash; the service derives the digest
transactionally, while the persisted correction requires the full hashed
span. Character merge/split corrections project through `effectiveRegistry`.
Both preserve human authority and correction identity without mutating machine
entities. Dialogue rows expose a closed
`unknown|ambiguous|proposed|corrected` speaker state consistent with their
effective attribution. Every human analysis-gate decision requires a nonblank
rationale of at most 4,000 Unicode code points, and each gate review returns
the complete immutable latest human decision or an explicit null.
`Phase2PackagedE2eResult` describes the schema-v4 packaged
machine result, and `Phase2BuildEvidenceManifest` describes the successful
schema-v3 CI manifest with artifact hashes, eleven agents, fourteen observed
stages, the exact 26-action packaged workflow, three protected corrections,
four durable gate decisions, restart proof, and synthetic-story assertions.
Each correction carries the SHA-256 of its exact synthetic test reason. Every
approved gate is cross-linked to the canonical profile, analysis run, and
snapshot identity/revision/fingerprint before and after restart. The full
immutable decision record is compared in the application and represented in
CI evidence only by its SHA-256. Those CI proof shapes admit IDs, counts,
hashes, and bounded process metadata, but no manuscript, correction or
decision rationale, or evidence-excerpt text.

## Phase 3A governed voice casting

`v3/definitions.schema.json` adds closed, versioned boundaries for provider
and model descriptors, catalog revisions, project-independent casting voice
profiles, rights records, production roles, the deterministic casting
profile, durable casting runs, explainable candidates, metadata-only
conflicts, append-only assignments and corrections, three approval gates, and
immutable approved-cast snapshots. Version 1 voice and casting meanings remain
unchanged.

The current profile ID is `governed-voice-casting-v1@1.0.1`, the producer is
`voice-casting-orchestrator@1.0.0`, and the rights policy is
`voice-rights-policy-v1`. The canonical profile fingerprint is
`5377949573018b5d3a4f4cd343392155071640364d3ba36be80a1bf4ad58de97`.
Exact historical `governed-voice-casting-v1@1.0.0` references with fingerprint
`3eaa6b4d1333b49e55707b1e9aa20606f262e1315a043bff2912a0fe77f97fa6`
remain readable; crossed or unknown ID/fingerprint pairs fail closed.
The schema pins 300 roles, 5,000 profiles, 50 pre-reduction candidates per
role, 12 final candidates per role, page bounds of 50/200, and 2,000-code-point
explanations. A voice profile may be assigned to at most two roles before the
configured reuse-conflict rule fires.

Rights states are exactly `verified`, `restricted`, `unknown`, and
`prohibited`. Unknown and prohibited rights cannot be represented as eligible
for final approval. Restricted rights remain reviewable only through an
explicit acknowledgement correction. Corrections and decisions are immutable
events; automated reruns cannot silently replace them. Human review decisions
are `approved|changes_requested|rejected`, while only a system actor may append
`invalidated`; a system actor cannot approve. Production roles cover primary
and secondary narration, named characters, explicit unresolved speakers,
groups, quoted documents or announcements, internal thought, and custom roles.
Character roles keep stable Character Registry identity separate from analysis
entity identity. Importance and language/locale/performance requirements are
conservative approved-evidence projections, and candidate conflict warnings
are bounded projections of actual conflict rows.

`Phase3PackagedE2eResult` and `Phase3BuildEvidenceManifest` are bounded proof
entry points for casting persistence, rights governance, three gate decisions,
exact executable/service hashes, and exact owned-process exit. They admit
identifiers, counts, hashes, and process IDs, but no manuscript text, license
documents, credentials, voice samples, or audio.
The schema-v4 manifest retains every schema-v3 Phase 2 top-level field and
shape through pinned references, then adds one closed `voiceCastingContract`
section. Existing secure-ingest, whole-book-analysis, artifact, assertion, and
packaged-flow evidence is therefore not replaced or weakened.

The repository-owned development catalog lives at
`apps/local-service/src/cinematic_story_service/catalogs/synthetic_voice_catalog.v1.json`.
Its fingerprint is computed from recursively key-sorted JSON after removing
the two fingerprint fields; array order is significant. It is fictional
metadata only and deliberately covers unavailable, deprecated, restricted,
unknown, prohibited, provider-disabled, language-mismatch, long-form,
metadata-similarity, and narrator/character-reuse cases without synthesis or
audio.
