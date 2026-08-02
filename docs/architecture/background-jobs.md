# Background Jobs

## Purpose

Story analysis, provider work, and audio rendering are persisted jobs so UI/process failure cannot erase their state. The local queue is implemented through SQLite repositories and an in-process bounded worker in Phase 0; no broker or cloud service is required.

## Records

```text
Job {
  id, projectId, type, schemaVersion, state, inputRevision,
  inputPayloadHash, priority, progress, stage, cancelRequestedAt?,
  currentAttempt, checkpointAvailable, createdAt, updatedAt, terminalAt?
}
JobAttempt {
  jobId, number, workerInstanceId, leaseExpiresAt?, startedAt?, endedAt?,
  outcome?, redactedError?, producerVersions, resultRef?
}
JobEvent {
  jobId, attempt, sequence, type, state?, stage?, progress?,
  completedUnits?, totalUnits?, warning?, error?, createdAt
}
JobCheckpoint {
  jobId, attempt, sequence, checkpointType, schemaVersion,
  inputRevision, producerVersion, payloadRef/hash, createdAt
}
```

Inputs are validated typed snapshots; no arbitrary callable/module names or shell commands are stored. Large/private checkpoint content is project-scoped and encrypted/protected like other project data, referenced relatively, and excluded from logs.

## State machine

Allowed states are:

```text
queued -> running -> succeeded
                  -> failed
                  -> cancel_requested -> cancelled
                  -> interrupted
queued -> cancel_requested -> cancelled
interrupted -> queued             (resume)
failed -> queued                  (retry)
paused -> queued                  (resume; future user-controlled pause)
```

`succeeded`, `failed`, and `cancelled` are terminal for an attempt, not historical deletion. Commands invalid for the current state return `409 JOB_STATE_CONFLICT`. Cancel is idempotent. A retry/resume increments `currentAttempt` and preserves all earlier attempts/events.

`progress` is in `[0,1]`, monotonic within an attempt, and exactly `1` only for success. Indeterminate work reports a stage and unit counts as null; it does not fake a percentage. Event `sequence` is strictly increasing per job and supports reconnect after a known sequence.

## Claiming and execution

The Phase 0 execution lease is the non-blocking OS lock held on the canonical data directory for
the entire `Database` lifetime. A second service process cannot open or reconcile the same SQLite
store while that lock is owned. In-process repositories still use conditional SQL transitions,
atomic event allocation, and a DB-unique active-job key; the process lock is not a substitute for
those concurrency controls.

1. Creation and idempotency record commit before a worker can claim the job.
2. A worker transaction atomically claims an eligible job with an instance ID and expiring lease.
3. Work runs outside the claim transaction against the frozen input revision.
4. The worker renews its lease and writes bounded progress/event batches at safe points.
5. A checkpoint is written atomically only after referenced files are fully flushed and hashed.
6. Result publication validates inputs, writes domain projections/artifact records plus outbox event in a transaction, then marks success.

One publication lease per project/revision prevents conflicting analysis/render commits. CPU and provider concurrency use separate bounded pools so a slow provider cannot starve health/API requests. Scheduling is deterministic for equal priority: priority, creation time, then job ID.

## Cancellation

The cancel endpoint records `cancel_requested` transactionally and signals the local worker. Each stage receives a cancellation token and checks it:

- before/after each bounded parsing or agent unit;
- before provider submission and while awaiting cancellable operations;
- before checkpoint and result publication;
- while supervising FFmpeg/local processes.

Cancellation never rolls back previously approved project state. New temporary work is removed, leases are released, and no partial file is registered as a finished artifact. Document-parser cancellation terminates the exact parent-owned spawned-process tree through its Windows Job Object and proves that the owned processes exited before returning; other non-cooperative owned subprocesses receive graceful termination, then bounded process-group termination. The application never kills by process name.

## Failure, retry, and resume

Errors are classified:

- `validation` or `policy`: not retryable until input/user decision changes;
- `transient_provider`, `timeout`, or `resource_busy`: retryable with bounded exponential backoff/jitter;
- `resource_exhausted`: retryable after space/memory/concurrency remediation;
- `internal` or `corrupt_output`: preserved for diagnosis and explicit retry after fix;
- `cancelled`: user outcome, not an error.

Automatic retry is allowed only when the job type declares a small maximum, the operation is idempotent, and provider policy/cost limits permit it. Otherwise user retry is required. Every attempt records the exact error class and producer/provider versions. Error messages are redacted and bounded.

Resume verifies job type, checkpoint schema, input revision/hash, agent/tool version compatibility, and referenced artifact hashes. A compatible checkpoint determines the next safe unit. If any check fails, `POST /resume` returns `CHECKPOINT_INCOMPATIBLE`; the user can retain evidence and start a clean retry. Retry may reuse verified content-addressed inputs but not unverified partial output.

## Startup and shutdown recovery

On startup, after the previous instance's leases expire (or its verified
ownership is known gone), an abandoned `running` attempt becomes
`interrupted`. A transactionally committed `cancel_requested` intent settles
as `cancelled`; restart never converts that durable human request into
resumable work. A queued job remains queued. If an import or re-extraction
transaction committed its pending extraction but the process stopped before
the extraction-job transaction committed, startup creates the one missing
targeted job with a deterministic recovery key. It never duplicates an
existing extraction target. The service never assumes an external provider
request did not complete: it reconciles through provider idempotency/status
when supported or requires review before possible paid duplication.

Graceful shutdown stops new claims, requests checkpoint at the next safe point, persists events, changes unfinished attempts to `interrupted`, releases leases, and closes storage. Phase 0 workers do not continue after the desktop/service exits.

## Progress delivery

Durable `job_events` are authoritative. `GET /jobs/{id}/events?afterSequence=N` supports bounded polling and may negotiate an authenticated server-sent stream. Streaming is an optimization: reconnect/poll reconstructs state without missed transitions. Main proxies validated events to renderer subscribers and drops/reloads on buffer overflow rather than blocking workers.

## Job-type declaration

Each registered job type declares:

- versioned input, checkpoint, progress, result, and error schemas;
- owning runner/agent graph and producer versions;
- cancellation/checkpoint boundaries;
- retry classification and maximum automatic attempts;
- required approval and provider/content-disclosure gates;
- resource/concurrency class;
- result publication transaction and cleanup behavior.

Phase 0 requires `analyze_story`. Phase 1 adds persisted document extraction
work targeted to one immutable source revision. It records parser attempts and
publishes an extraction plus pending Import Review atomically; analysis remains
blocked until that exact extraction is approved. Future job types include
provider synthesis, scene render, chapter render, master render, cache
verification, and quality-control analysis.

Phase 2 adds the persisted `analyze_whole_book` job alongside the Phase 0/1
`analyze_story` job. The whole-book pipeline is specified in
[whole-book-story-intelligence.md](whole-book-story-intelligence.md). Its input
freezes the exact approved extraction, Import Review decision, analysis
profile/fingerprint, protected-correction set, and agent versions. Stage
progress and output fingerprints are durable. Version 1 stores a resumable
structure artifact and a separately verified complete pre-publication result
checkpoint; other later stages rerun from the last compatible artifact. No
per-entity or per-later-agent partial resume is claimed. Only the final
validated synthesis transaction may publish a reviewable snapshot; failure,
cancellation, interruption, or stale input leaves the prior effective snapshot
unchanged.

Phase 3A adds the persisted `analyze_casting` job. Its frozen input binds the
current approved Import Review and four Phase 2 gate decisions, source/
extraction identity, Phase 2 run/snapshot/correction-set/character-registry
fingerprints, voice-catalog revision/fingerprint, casting profile fingerprint,
producer, and target casting-run ID. Its exact stages are:

`validate_phase_2_approvals`, `freeze_source_analysis_evidence`,
`load_voice_catalog_revision`, `create_production_roles`,
`evaluate_role_constraints`, `generate_bounded_candidates`,
`evaluate_differentiation_conflicts`, `publish_casting_run`, and
`publish_reviewable_cast_snapshot`.

Version 1 writes a bounded `voice_casting` result checkpoint after role,
candidate, and conflict evaluation. Resume rechecks its schema, input revision
and fingerprint, producer/profile, and catalog. Final publication atomically
writes the validated run projections, reviewable cast snapshot, and pending
casting reviews before marking success. Cancellation, failure, interruption,
or stale evidence leaves the prior effective cast unchanged. Job events expose
only states, stages, counts, progress, IDs, and redacted codes—never manuscript
text, candidate rationale, correction values, rights documents, provider
payloads, or credentials. See
[casting jobs, recovery, and scale](casting-jobs-and-recovery.md).

Phase 3B adds `generate_audition`, targeted to one immutable audition-session
revision. Its hash-only payload contains only schema version, session ID,
script ID, and request fingerprint. The durable `speech_audition` checkpoint
advances through prerequisite/rights/assignment validation, model/runtime
resolution, pronunciation/normalization compilation, private cache lookup,
synthesis or verified hit, WAV/QC validation, atomic publication, and runtime
release. Checkpoints and events contain IDs, fingerprints, counts, stages, and
redacted codes only—never text, pronunciation values, model paths, cache keys,
audio bytes, or secrets.

Cancellation is honored before each external or publication boundary. A
failed, cancelled, or interrupted attempt cannot publish a successful clip.
Retry must revalidate all frozen evidence and acquire a fresh authenticated
runtime identity; a prior model or runtime process is never adopted by PID or
name. See
[audition sessions, cache, and governance](audition-sessions-cache-and-governance.md).
