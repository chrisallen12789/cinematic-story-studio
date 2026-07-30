# Shared contracts

This package is the dependency-free TypeScript view of the version 1 ingest
and production contracts in `../../schemas/v1` and the Phase 2 whole-book
story-intelligence contracts in `../../schemas/v2`. It contains data only: no
storage, provider, networking, or UI behavior belongs here.

Persisted data must be validated against the JSON Schemas at service boundaries.
TypeScript types do not replace runtime validation. Schema-breaking changes
require a new schema directory and a deliberate migration.

`HumanCorrection` and `ApprovalDecision` values are immutable events. Append a
new superseding event; do not mutate or delete the earlier event. Speaker
correction requests carry an expected revision so the service can reject stale
writes.

Phase 2 freezes the approved source, extraction, Import Review, story,
analysis profile, producer, and runtime-agent versions into each analysis run.
Generated entities preserve machine and effective fingerprints, bounded exact
evidence, confidence, warnings, and provenance. Specialized IDs such as
`characterId`, `sceneId`, and `dialogueLineId` must equal the common
`entityId`; this cross-field equality is enforced by boundary validators
because TypeScript and JSON Schema cannot compare two arbitrary values.

`AnalysisCorrectionSelection` is the authoritative persisted
category-to-collection-to-patch mapping.
`AnalysisCorrectionRequestSelection` is its request counterpart; structure
boundary requests submit only the frozen source identity and selected offsets,
and the service derives the canonical span digest transactionally. Their
closed patch types prohibit generic field mutation and keep human authority
append-only. Phase 2 correction requests and persisted corrections require a
nonblank, bounded human reason. Cursor endpoints bind
every opaque cursor to the
project, run, snapshot, collection, and normalized filter fingerprint.
Dialogue rows carry a closed speaker state that must agree with effective
attribution. Every Phase 2 gate action requires a nonblank bounded rationale,
and review reads return the complete immutable latest human decision or an
explicit null so restart and supersession can be verified.
