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
