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
