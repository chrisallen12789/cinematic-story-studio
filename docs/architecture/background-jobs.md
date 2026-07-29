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

Cancellation never rolls back previously approved project state. New temporary work is removed, leases are released, and no partial file is registered as a finished artifact. A non-cooperative owned subprocess receives graceful termination, then bounded process-group termination. The application never kills by process name.

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

On startup, after the previous instance's leases expire (or its verified ownership is known gone), all abandoned `running`/`cancel_requested` attempts become `interrupted`. A queued job remains queued. The service never assumes an external provider request did not complete: it reconciles through provider idempotency/status when supported or requires review before possible paid duplication.

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

Phase 0 requires `analyze_story`. Future job types include import extraction, provider synthesis, scene render, chapter render, master render, cache verification, and quality-control analysis.
