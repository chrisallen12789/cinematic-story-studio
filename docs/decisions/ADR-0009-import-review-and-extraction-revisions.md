# ADR-0009: Import Review and extraction revisions

- Status: Accepted for Phase 1
- Date: 2026-07-29

## Context

Structured formats can extract incomplete, reordered, or warning-bearing text
without a parser crash. Starting analysis immediately would turn an uncertain
machine interpretation into downstream project state. Parser upgrades or limit
changes can also change derived text while source bytes remain identical.

## Decision

Separate the immutable `SourceDocument` from append-only
`DocumentExtraction` revisions. Every terminal extraction has a
`ParserExecutionRecord`, limits fingerprint, canonical-text hash, sections,
source mappings, warnings, and an evidence fingerprint.

Create a durable Import Review record for the exact source and extraction
revisions. Its decision history is append-only; the actor classification is
`human` with the service-owned local actor ID. The explicit decision endpoint
appends records; background extraction and analysis jobs do not write that
endpoint and no path updates or erases a prior decision. Approval decisions
are immutable. Analysis creation verifies the current approved evidence
fingerprint transactionally; a pending, rejected, stale, or mismatched
revision fails closed.

A changed-byte reimport appends a source revision. Explicit re-extraction
appends an extraction revision. Either invalidates the effective old approval;
successful extraction then opens a new pending review while preserving all
historical decisions and downstream evidence. An identical import reuses the
current source/extraction, idempotent replay of the same request returns the
original record, and a reused key with a different fingerprint conflicts.

## Consequences

The UI must expose a real Import Review state and cannot represent extraction
success as analysis approval. Restart and migration tests must preserve source,
extraction, decision, analysis, and correction history. The data model and API
carry more identifiers, but provenance and rollback are explicit. Parser
changes do not silently alter an already reviewed project.
