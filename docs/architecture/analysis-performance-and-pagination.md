# Analysis performance and pagination

## Scale target

The Phase 2 deterministic baseline is tested with a generated,
repository-owned story between 100,000 and 105,000 words. The current scale
case produces 200 scenes, 2,000 dialogue lines, 2,000 character mentions,
4,000 beats, and 13,003 total analysis entities. The generator is compact and
deterministic; its expanded manuscript is never committed.

The fixed approved-extraction ceiling remains 10,000,000 Unicode code points.
Phase 2 adds explicit collection ceilings through the
`whole-book-intelligence-v1` analysis profile. The profile, agent versions,
and profile-owned analysis limits are canonically serialized and
fingerprinted; changing one creates a different analysis input identity. HTTP
body, correction-patch, reason, and review-rationale bounds are separately
versioned service/API controls rather than members of that profile digest.

## API limits

- Default collection page: 50 items.
- Maximum collection page: 200 items.
- Evidence excerpt: 512 Unicode code points per span.
- Evidence spans per claim: 16.
- Dialogue speaker candidates per line: 8.
- Exact-text field: 16,384 Unicode code points. A list response may expose only
  that bounded verbatim window together with the original code-point count,
  canonical source span, and hashes; the complete approved text remains in the
  immutable local extraction and is never copied into a list response.
- Warnings per entity: 32.
- HTTP mutation JSON body: 64 KiB.
- Canonical human-correction patch: 16 KiB and at most 32 named fields.
- Human-correction identifier arrays: at most 256 unique opaque IDs; a
  character split requires at least one mention ID.
- Human correction reason: 1,000 Unicode code points.
- Analysis review rationale: 4,000 Unicode code points.
- Compatible corrections considered by one correction-aware entity
  projection: 4,096 target identities; exceeding that service safety ceiling
  fails closed instead of materializing an unbounded overlay.
- List endpoints never return the complete manuscript.
- The UI never requires every dialogue line, mention, or finding to render at
  once.

Opaque cursors bind collection kind, run, snapshot revision, filters, and the
stable last ordering key. A cursor from another query or revision is rejected.
Pages use deterministic ordering with an ID tie-breaker.

## Bounded analysis

The shared profile and service enforce maximum words, entity records, evidence
spans, notes, candidate fan-out, serialized envelopes, and checkpoint size.
Analyzer loops use bounded neighborhoods and explicit cancellation points.
A limit failure is typed and publishes no replacement snapshot. Job events
contain stage/count/progress data only.

The initial service accepts at most 150,000 words in one analysis run,
250,000 persisted analysis entities, 32 KiB in one serialized runtime-agent
envelope, and 64 MiB in the durable whole-book checkpoint payload. These are
service safety ceilings rather than claims that every input below them will
receive semantically complete analysis.

Character and alias candidate indexes are built in one pass using normalized
keys and bounded local neighborhoods. Dialogue turn-taking examines a bounded
window. Entity-resolution code may not compare every mention with every
identity. Scale tests measure input/output counts, query plans, page sizes,
intermediate-checkpoint restart recovery, cooperative cancellation, stable
fingerprints, and peak process memory. The 100,000-word subprocess must stay
below a 320 MiB peak resident-set ceiling on Windows and Unix. This is an
executable regression ceiling, not a hard wall-clock or universal memory SLA
for unrelated hardware.

## Database indexes

Schema v3 indexes support current-run/snapshot lookup, collection pagination
by project/run/collection/stable ordinal/ID, and entity identity lookup.
Confidence, review-state, speaker-state, finding, target, and authority
filters are bounded predicates over that controlling run/collection window;
Phase 2 does not claim dedicated indexes for every JSON projection filter.

Scale tests inspect the controlling collection index and verify bounded
response serialization. Full source text is read only by the local analysis
worker and bounded authenticated entity projection, never returned as one
complete list payload.

## Phase 3A casting limits

Casting adds a separate fingerprinted profile rather than changing the Phase 2
analysis profile. It caps one run at 300 roles and 5,000 catalog profiles,
evaluates at most 50 pre-reduction candidates per role, publishes at most 12
per role, caps conflicts at 10,000, and caps immutable corrections at 200 per
run. Catalog, run, role, conflict,
assignment, and correction pages default to 50 and cap at 200; candidate pages
also respect the 12-candidate final bound. Explanations cap at 2,000 Unicode
code points.

The worker loads indexed descriptor/profile/rights maps once and performs at
most 15,000 bounded compatibility assessments rather than issuing a
role-by-voice database query pattern. The UI pages roles/catalogs and requests
one role's candidates. Exact job/restart/scale requirements are in
[casting jobs, recovery, and scale](casting-jobs-and-recovery.md); dated
observations belong in
[Phase 3A verification](../evidence/phase-3a-verification.md).
