# Casting jobs, recovery, and scale

## Durable job boundary

Every Phase 3A run is a persisted `analyze_casting` job. The API commits the
casting run, frozen input identity, idempotency record, job, attempt, and queued
event before a worker can claim it. The worker has no approval authority and
cannot publish directly outside the casting repository's final transaction.

The job freezes:

- current project/source/extraction/Import Review evidence;
- Phase 2 run, snapshot, correction-set, character-registry, and four gate
  decisions;
- `governed-voice-casting-v1@1.0.0` and its fingerprint;
- `synthetic-voice-catalog-v1@1.0.0` and its fingerprint;
- `voice-casting-orchestrator@1.0.0`;
- input revision and canonical input fingerprint; and
- the target casting-run ID.

Claim and publication recheck those values. A retry with changed story,
catalog, profile, or correction evidence is not another attempt of the same
input; the changed evidence requires a new run.

Explicit custom-role creation is a separate authenticated mutation after this
job has succeeded; it is not a hidden tenth job stage. The mutation rechecks
the exact succeeded-run, catalog, Phase 2 snapshot, correction-set, and
casting-profile evidence before appending any role-derived records.

## Controlled pipeline

The nine exact stages are:

1. `validate_phase_2_approvals`
2. `freeze_source_analysis_evidence`
3. `load_voice_catalog_revision`
4. `create_production_roles`
5. `evaluate_role_constraints`
6. `generate_bounded_candidates`
7. `evaluate_differentiation_conflicts`
8. `publish_casting_run`
9. `publish_reviewable_cast_snapshot`

Events contain stage, bounded counts, progress, attempt, state, IDs, and stable
redacted codes only. They do not contain manuscript text, character passages,
candidate rationale, correction values, rights documents, provider payloads,
credentials, or personal paths.

Progress is monotonic within an attempt and reaches `1` only after the
publication transaction commits. Cancellation is checked before and after
bounded stage work, before/after checkpointing, and before publication.

## Checkpoint and publication

The version-1 `voice_casting` checkpoint stores the validated bounded
role/candidate/conflict result and its fingerprints after deterministic
evaluation. Its serialized payload is limited to 64 MiB. Resume accepts it
only when checkpoint schema, input revision/fingerprint, producer/profile, and
catalog still match. Otherwise it returns a typed incompatible-checkpoint
failure and retains the evidence for diagnosis.

The final transaction:

1. claims a still-running, not-cancel-requested job;
2. revalidates current Phase 2, catalog, profile, correction, and run evidence;
3. validates roles, candidate bounds/order/fingerprints, and metadata-only
   conflicts;
4. writes the immutable run projections and reviewable cast snapshot;
5. initializes the three pending review projections;
6. marks the job/attempt successful and appends its terminal event; and
7. releases the active-job key.

A cancelled, failed, interrupted, stale, or invalid result cannot replace the
previous effective cast snapshot.

Appending a valid custom role uses its own immediate transaction. It writes the
content-free role, bounded candidates and conflicts, optional machine proposal,
a new immutable cast snapshot, and new revisions of the three gate reviews
together. Prior review decisions remain immutable but no longer match the
current snapshot/evidence. Any validation, evidence, definition-identity,
idempotency, or role-limit failure rolls back the entire mutation.

## Cancellation, retry, and interruption

- Cancel is idempotent. `queued|running` becomes `cancel_requested`, then
  `cancelled` at a safe boundary. No partial casting result is published.
- A retry is explicit and permitted only for a terminal retryable failure. It
  increments the attempt, preserves the earlier error/events, and reuses only
  a fingerprint-compatible checkpoint.
- Startup converts an abandoned running attempt to `interrupted`. A durable
  cancellation intent settles as cancelled rather than resumable.
- Resume is explicit and valid only for interrupted work with a compatible
  `voice_casting` checkpoint. It increments the attempt and continues from the
  verified boundary.
- Graceful desktop shutdown stops new claims, checkpoints at a safe boundary,
  marks unfinished work interrupted, releases leases, and exits. Casting has
  no owned model, provider, speech, or audio subprocess in Phase 3A.
- Replaying a creation, correction, or decision idempotency key with the same
  request fingerprint returns the original result; changing the request under
  the same key conflicts.
- A custom-role identity is additionally stable within its succeeded run:
  project, run, and client-owned definition ID derive the role ID. An identical
  idempotency replay returns that role, while changed request material or a
  conflicting reuse of the definition ID fails closed.

## Failure and recovery matrix

| Failure | Durable effect | Safe recovery |
| --- | --- | --- |
| Missing/rejected Phase 2 decision | No eligible run publication | Complete the current Phase 2 review and create a new run |
| Snapshot, correction set, or registry changed | Typed stale-input conflict; old cast history remains | Review changed analysis and explicitly recast |
| Catalog/profile fingerprint mismatch | Job/run fails closed; no replacement snapshot | Restore the exact governed input or create a new run against a reviewed revision |
| Catalog structure or rights record invalid | Catalog rejected before effective use | Correct and republish a new immutable catalog revision |
| Role, voice, candidate, conflict, or checkpoint limit crossed | Typed resource/validation failure | Reduce governed input or adopt a new reviewed profile; do not bypass the bound |
| Cancellation before publication | Attempt ends cancelled; checkpoint may remain; current cast unchanged | Retry or create a new run deliberately |
| Worker/service exit | Attempt becomes interrupted; committed checkpoint/history remains | Resume if compatible or explicit clean retry |
| Injected/transient internal failure | Attempt and redacted code retained; no partial result | Explicit retry after remediation |
| Correction loses a race | Stale fingerprint/revision conflict; proposed UI input remains local | Refresh, compare, and reapply |
| Custom-role append loses a race or uses stale evidence | No role, candidates, conflicts, proposal, snapshot, or review revision is partially written | Refresh the succeeded run and resubmit with the same durable definition and a fresh idempotency key only when the intended request changed |
| Correction supersession branches or crosses semantic domains | Transaction rejects a stale/wrong successor; current assignment, rejection, lock, and reviews remain unchanged | Refresh the correction history and append against the current semantic leaf |
| Rights/catalog/provider/model change after selection | Append-only invalidation latches the affected assignment and system-invalidates only its role gate plus Complete; catalog reversion does not restore either | Explicitly select current eligible evidence and append new human approvals |
| Schema-v3 backup or v4 migration fails | Working v3 database remains usable/read-only as applicable; verified backup retained | Follow migration-0004 recovery using a separate copy |

## Scale controls

The governed profile limits one run to:

| Resource | Bound |
| --- | ---: |
| Production roles | 300 |
| Voice profiles | 5,000 |
| Pre-reduction candidates per role | 50 |
| Published candidates per role | 12 |
| Conflicts per run | 10,000 |
| Voice reuse before threshold conflict | 2 roles |
| Hard results per assessment | 16 |
| Soft results per assessment | 16 |
| Blockers / warnings per review entity | 32 / 32, with fail-closed overflow sentinels |
| Explanation | 2,000 Unicode code points |
| Default / maximum page | 50 / 200 |
| Durable casting checkpoint | 64 MiB |

The compatibility engine may perform a bounded in-memory pass over at most 300
roles and 50 pre-reduction voices per role. This is at most 15,000 assessment
operations, not 300-by-5,000 unbounded database reads. Descriptor/voice/rights
maps are loaded once; candidates are stable-sorted and reduced before
publication. Repository list operations use stable indexed ordering, then
apply bounded in-service pages over collections whose hard ceilings are
documented below. Each opaque cursor binds the exact ordered row-ID set, so an
append or replacement invalidates the cursor instead of duplicating or
skipping an item. The schema's keyset-shaped indexes and their query plans are
verified as structural readiness evidence; Phase 3A does not claim that the
current repository executes those handcrafted EXPLAIN statements as its
runtime page query.

The UI requests pages and one role's candidates as needed. It does not
materialize all 300 roles or 5,000 catalog profiles simultaneously.

Gate warning calculation is also bounded. Candidate-derived conflicts count
only when they match a current human-selected voice for a related role in that
gate; current-human-assignment conflicts and no-voice conflicts count when the
related role is in scope. More than 32 warnings emits a bounded overflow
warning and blocker, so approval cannot proceed on silently truncated evidence.
Candidate `conflictWarnings` project at most 32 actual persisted conflict rows
referenced by that candidate. Assignment mutations recompute their dynamic
conflicts inside the transaction and reject the whole mutation before
publication if the run would cross the 10,000-conflict cap.

## Required scale evidence

Executable verification must generate synthetic metadata, not commit an
expanded private catalog. It must cover:

- 300 roles and 5,000 profiles at the accepted boundary;
- 50 assessments per role and at most 12 published candidates;
- deterministic role/candidate/conflict IDs, ordering, and fingerprints across
  reruns;
- metadata conflict evaluation and the 10,000-conflict guard;
- default and maximum pages, exact-row-set cursor binding, and separately
  labeled SQLite index/query-plan structure;
- no per-role database query for every voice;
- cancellation before publication;
- interrupted-checkpoint restart and resume;
- explicit failed-attempt retry; and
- unchanged prior effective cast after every non-success terminal path.

Observed time and memory belong in the dated
[Phase 3A verification record](../evidence/phase-3a-verification.md). The bounds
and a successful synthetic regression are not a universal latency, memory, or
production-capacity SLA.

## Database backup and rollback

Schema v4 is forward-only. Before v3 mutation, the service verifies the exact
issued v3 structure/ledger, creates an SQLite-consistent
`*.v3-backup.sqlite3`, verifies structure, quick/foreign-key checks and
canonical logical SHA-256 equality, then migrates atomically. The backup is
retained after success or failure.

There is no in-place v4-to-v3 downgrade. Stop the application, retain the v4
database and verified v3 backup, verify the backup independently, create a
separately named recovery copy with a coordinated SQLite-safe operation, and
open only that copy with the matching Phase 2 application. Never change
`user_version`/ledger, drop Phase 3A tables, copy a live WAL database
generically, or overwrite the only current database/backup. Exact instructions
and signatures are in [migration 0004](../migrations/0004-phase-3a-voice-casting.md).
