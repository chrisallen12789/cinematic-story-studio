# Failure and Recovery

## Principles

Durable state is authoritative; memory and UI state are projections. Recovery is bounded, idempotent, and evidence-preserving. Never label incomplete analysis/audio as finished, silently retry a potentially paid request, overwrite a human correction, or repair the only database copy in place.

## Failure matrix

| Failure | Detection and durable effect | Recovery |
| --- | --- | --- |
| Service fails before ready | Bootstrap timeout/exit; no trusted port | Electron stops owned child, reports sanitized code, permits bounded retry/diagnostics. |
| Service connection lost | Health/request failure | Disable mutations, preserve unsaved UI form, bounded owned-service restart, reload authoritative revisions. |
| Desktop exits/crashes | Parent/control channel closes | Service checkpoints safe work, marks attempts interrupted, closes DB, exits; next start reconciles leases/staging. |
| Worker dies/restarts | Lease expires/instance gone | A `running` attempt becomes `interrupted`; a committed `cancel_requested` intent settles `cancelled`; validate an interrupted checkpoint before resume or clean retry. |
| Import validation fails before source publication | Request/type/source-size/integrity error | Remove unique staging; do not publish a source/story revision; prior active revision remains. |
| Process stops after pending extraction commit but before job commit | Startup finds a pending extraction with no extraction-job target | Create exactly one deterministic queued recovery job; preserve the immutable source/revision |
| Import extraction fails, is cancelled, or crosses its parent-enforced spawned-process deadline | The parent terminates and confirms exit of the exact owned parser process tree; the durable parser attempt ends `failed`/`cancelled`; no review or analyzable story is published | Remove owned staging, preserve the immutable source/prior revision, then require explicit retry or re-extraction |
| Import Review is pending/rejected/stale | Analysis creation fails closed with typed gate/revision conflict | Review current source/extraction evidence; append a local-human decision |
| Schema-v1 backup/migration fails | Startup remains unavailable/read-only with a redacted recovery code | Preserve database and verified backup; diagnose or restore a copy, never edit the sole copy |
| Stale concurrent write | Expected revision mismatch | Return `409` current revision; preserve proposed user input for compare/reapply. |
| Provider unavailable/timeout | Typed adapter error/ambiguous request | Bound retry only when idempotent; reconcile provider request; otherwise require user decision to avoid duplicate cost. |
| Kokoro/Docker absent | Health `unavailable` | Local project work remains functional; offer development guidance only. |
| FFmpeg absent/incompatible/crashes | Capability/preflight/exit/probe failure | Block audio job, preserve manifest/checkpoints, repair managed tool/install then retry. |
| Cancel requested | Durable flag plus worker signal | Stop at safe boundary, terminate only owned subprocess if needed, remove partial staging, end `cancelled`. |
| Disk full/permission/locked file | Preflight or I/O error | Stop before publication, report sanitized root category and space/action; free space/change export target then retry. |
| Cache corrupt/missing | Hash/probe mismatch | Quarantine/remove unleased cache entry and regenerate; immutable source/published artifact is not replaced. |
| QC fails | Typed blocking findings | Keep artifact in staging/private failed-attempt scope; adjust inputs/policy or explicitly waive allowed finding, then new manifest/attempt. |
| DB busy | Finite timeout | Retry short safe transaction with bound; never hold transaction during external work. |
| DB migration/integrity fails | Migration/check result | Close DB, preserve original plus backup, prevent mutation, offer restore/copy/diagnostics. |
| Contract/service mismatch | Startup version negotiation | Fail closed before migration; reinstall matching application unit. |
| Phase 2 input becomes stale | Run creation and publication recheck source, extraction, Import Review, profile, and protected-correction fingerprints | Mark work stale/failed with a redacted conflict; retain the prior current snapshot. |
| Story-analysis stage fails or reaches a profile limit | Durable agent execution and job attempt record the bounded error; no final snapshot is published | Correct input/policy or explicitly retry; reuse only fingerprint-compatible checkpoints. |
| Phase 2 correction or decision loses a race | Target/snapshot revision or previous-value/evidence fingerprint mismatch | Return a conflict and preserve the proposed local edit for compare/reapply. |
| Schema-v2 backup/migration fails | Startup remains unavailable/read-only with a redacted recovery code | Preserve the v2 database and verified backup; recover from a copy under migration 0003. |
| Phase 3A prerequisite becomes stale | Run creation/publication rechecks Import Review, four Phase 2 decisions, snapshot, correction set, registry, catalog, and profile fingerprints | Reject or fail the run with a typed redacted conflict; keep the prior effective cast |
| Voice catalog/profile validation fails | Descriptor, rights, identity, limit, canonical fingerprint, or version check rejects the input | Publish no run/snapshot; repair and publish a new immutable catalog/profile revision |
| Casting job is cancelled, fails, or is interrupted | Durable attempt/event/checkpoint evidence remains; no partial role/candidate result becomes effective | Resume only from a compatible casting checkpoint or explicitly retry; never auto-approve |
| Casting correction or decision loses a race | Role/run/catalog/snapshot/correction/review fingerprint or revision mismatch | Return a conflict and preserve the proposed local edit for compare/reapply |
| Selected catalog/provider/model/profile/rights evidence changes | Append-only invalidation latches the affected assignment, system-invalidates only its role gate and dependent Complete gate, and preserves immutable history; catalog reversion does not restore authority | Explicitly select current eligible evidence and append human approvals; do not silently substitute |
| Schema-v3 backup/migration fails | Startup remains unavailable/read-only with a redacted recovery code | Preserve the v3 database and verified backup; recover from a separate copy under migration 0004 |
| Speech worker identity/handshake mismatches or times out | Runtime instance fails before synthesis/publication; no process is trusted by name or PID alone | Close only the exact owned Windows Job Object when ownership was established; otherwise fail safely without inspecting or terminating unrelated processes |
| Model package is missing, changed, unsafe, or removed while leased | Exact inventory/size/hash/link/reparse verification or active-job lease fails | Deactivate/repair/reinstall exact allow-listed bytes; never substitute a model or delete historical audition evidence |
| Private audition-script publication is interrupted around database commit | Exact same-directory `.utf8.pending.<record-id>.tmp` ownership, final storage key, text hash, and code-point count are reconciled under a fixed startup scan bound | Delete only exact uncommitted owned staging/final orphans; restore exact committed staging bytes to their opaque final name; fail startup without touching uncertain/reparse content |
| Audition job is cancelled, fails, or is interrupted | Hash-only checkpoint/attempt history remains; staging is not published as a clip | Resume only a compatible checkpoint or retry after revalidating every prerequisite and acquiring a fresh runtime identity |
| Audition cache bytes are missing or corrupt | Owned path, file identity, byte size/hash, and WAV metadata fail verification | Mark a typed miss/corruption finding and regenerate; preserve artifact metadata and human history |
| Exact Phase 3A authority, assignment, rights, runtime/model, or applicable pronunciation evidence changes | A newer current cast snapshot suppresses older-snapshot Phase 3B projections; a new same-snapshot Phase 3A decision tuple or changed selected assignment/leaf/temporal-rights evidence appends targeted invalidation for dependent clips/reviews/readiness | Revalidate the latest succeeded run and exact immutable snapshot manifest, then generate and review new evidence; never mix snapshots or silently reuse an older decision; unrelated pronunciation entries preserve unaffected same-snapshot authority |
| Schema-v4 backup/migration fails | Startup remains unavailable with a redacted recovery code and no v5 mutation | Preserve the exact v4 database and verified backup; recover only from a separate copy under migration 0005 |

## Startup reconciliation

Under the single-instance/migration lock:

1. verify data-root ownership/scope and available space;
2. finish or roll back recorded migration state using the backup;
3. open SQLite with foreign keys and run lightweight integrity checks;
4. reconcile expired job leases and mark abandoned attempts `interrupted`;
5. create one missing job for any committed pending extraction with no job target;
6. remove only the recognized empty or single-`source.upload` UUID staging
   directories for durable projects after validating each resolved path; leave
   unknown entries untouched;
7. reconcile at most 100,000 exact owned private audition-script records/files:
   discard uncommitted pending/final orphans, restore committed pending bytes only
   after exact hash/length/path verification, retain verified committed finals, and
   stop safely on unknown ownership or reparse state;
8. inventory at most 100,000 combined audio database rows and inspected
   audio/staging directory entries, then reconcile only exact owned UUID WAV,
   pending, atomic-temporary, and purge-tombstone identities; preserve
   noncanonical/unknown files and fail before partial cleanup when the bound is
   exceeded;
9. verify last-project reference, then start workers and report ready/degraded.

Cleanup is retention-bounded, reference/lease aware, and repeatable. Unknown files are quarantined/reported, not recursively deleted.

## Job checkpoints and external effects

Each job type defines safe checkpoints and compatibility keys: input revision/hash, job/checkpoint schema, producer/agent/tool version, and referenced artifact hashes. Resume continues after the last verified checkpoint. Incompatibility returns a stable reason and keeps evidence; clean retry creates another attempt.

Before calling a paid/non-idempotent provider, persist intent and idempotency key. After response, persist provider request ID/result hash. An ambiguous crash is reconciled through provider status when possible; otherwise the job is blocked for user review rather than resubmitted automatically.

## Atomic publication

Write generated bytes to a unique same-volume staging path, flush/close, verify hash/probe/QC, then atomically rename into content-addressed artifact storage and commit its database record/result. Reconciliation handles either side of an interrupted file/transaction boundary. Exports use a temporary sibling plus atomic replacement when supported; an existing user export is not destroyed until the new file is complete.

Private audition scripts use a deliberately recoverable database boundary. The
service writes and fsyncs an exclusive opaque sibling pending file, verifies its
UTF-8 hash and code-point count, commits the row that owns the corresponding
final storage key, and then atomically renames the pending file. A commit failure
removes only that exact pending identity. A process stop before commit leaves no
owner and startup removes the exact orphan; a stop after commit leaves an owner
and startup restores only matching exact bytes. This avoids putting manuscript-
derived text in filenames or logs and never recursively deletes a scripts tree.

Phase 3B recovery never treats persisted session or clip rows as authority on
their own. It revalidates the latest succeeded Phase 3A run, the exact current
immutable cast-snapshot manifest, only its selected assignment revisions and
lock state, exact leaf and temporally applicable rights evidence, and the latest
eligible Phase 3A reviews and effective human decisions including provenance,
supersession, and warning acknowledgements. A cross-snapshot combination or an
unreconstructable ownership/authority chain fails closed without deleting
historical evidence.

## Database backup and recovery

Use SQLite's supported backup mechanism while connections are coordinated,
not raw copying of a live WAL database. Before destructive migrations, create
and verify the migration's stable versioned sibling backup; an explicit manual
repair may instead use a separately named timestamped recovery copy. Full
integrity failure puts the project in read-only recovery state.

Recovery options are: retry open after environmental remediation; open a verified backup as a new recovery copy; export any readable source/manifest metadata; or retain files for support. The original is never overwritten. Recovery logs remain redacted.

The schema-v1 to v2 path uses SQLite's online backup API, verifies source and
backup logical digests and schema ledger before migration, retains the verified
version-1 backup after success, and runs a post-transaction foreign-key check.
See [database migration 0002](../migrations/0002-phase-1-secure-ingest.md).

The schema-v2 to v3 path applies the same non-destructive principles with an
exact frozen-v2 structural signature and separately verified v2 backup. See
[database migration 0003](../migrations/0003-phase-2-story-intelligence.md).

The schema-v3 to v4 path validates the exact issued v3 ledger and frozen
structure, creates and logically verifies a `v3-backup`, applies all Phase 3A
tables/indexes/ledger state in one immediate transaction, preserves every
Phase 0-2 value, and creates no catalog, assignment, correction, snapshot, or
approval. There is no in-place v4-to-v3 downgrade. Recovery opens only a
separately named copy of the verified v3 backup with the matching Phase 2
application. See
[database migration 0004](../migrations/0004-phase-3a-voice-casting.md).

The schema-v4 to v5 path validates the exact issued v4 structure and contiguous
ledger, creates and logically verifies a `v4-backup`, and adds only Phase 3B
runtime/model/pronunciation/audition/cache/QC/review tables plus ledger version
5 in one immediate transaction. It does not install a model, start a worker,
generate audio, or fabricate an approval. Recovery retains the v5 database and
opens only a separately named copy of the verified v4 backup with the matching
Phase 3A application. See
[database migration 0005](../migrations/0005-phase-3b-local-speech-auditions.md).

## Data and cache cleanup

Project deletion, cache cleanup, staging cleanup, and export cleanup are distinct commands with previewed scopes. They resolve exact application-managed roots, refuse broad/unresolved/reparse-point escape paths, skip leased/referenced content, and report partial failure. Repeating them is safe. Deleting application data cannot guarantee removal from backups, sync, restore points, or SSD remanence.

## Recovery tests

Tests inject failures at startup, transaction/file publication boundaries, import extraction, checkpoint, provider request/reconciliation, FFmpeg process, cancellation, disk/permission, migration, and shutdown. They assert:

- exact durable job/project state after restart;
- no published partial result or orphan owned process;
- source and correction preservation;
- retry/resume attempt/event history;
- no duplicate provider work where reconciliation exists;
- path-scoped cleanup and redacted errors;
- usable verified backup on migration failure.

Phase 3A additionally injects stale prerequisite/catalog/profile evidence,
casting checkpoint incompatibility, cancellation before publication, failed
attempt and retry, interruption and resume, correction/decision races,
selected catalog/provider/model/profile/rights invalidation, and v3-to-v4
migration/backup failures. Each
case asserts that the prior effective cast remains unchanged and no runtime
path silently reapproves, recasts, or publishes partial evidence. The detailed
casting matrix and scale bounds are in
[casting jobs, recovery, and scale](casting-jobs-and-recovery.md).
