# ADR-0007: Use Controlled, Persisted Runtime Agent Orchestration

- **Status:** Accepted
- **Date:** 2026-07-29

## Context

Cinematic production needs specialized ingest, structure, dialogue, casting, direction, sound, music, continuity, mix/master, and QC stages. An open-ended autonomous loop would be difficult to inspect, resume, reproduce, secure, or constrain around human corrections, provider cost, and approvals.

## Decision

Model each production agent as a versioned typed transformation registered with declared inputs/outputs, confidence/warnings, review requirement, retry/failure/checkpoint policy, provenance, provider/model identity, and cost metadata. A versioned persisted DAG orchestrator runs agents as background-job steps against frozen artifact revisions.

Required approval gates durably pin exact artifact revisions/hashes. Agents cannot approve work, mutate source, directly access credentials/storage/tools, or silently overwrite human corrections. Protected corrections are input constraints; conflicts become proposals/warnings. Provider/tool access uses scoped adapters and policy gates.

Orchestration records attempts, checkpoints, decisions, and outputs. Non-deterministic output is explicitly identified and frozen as a hashed artifact. Resume requires compatible input/schema/producer versions.

## Consequences

- Work is inspectable, cancellable, resumable, and traceable through human decisions.
- Agent schemas/versions and orchestration migrations become long-lived compatibility concerns.
- Gates can slow fully automatic processing, intentionally prioritizing control and quality.
- Parallelism is allowed only for independent frozen inputs and bounded resources.
- Phase 0 can implement a deterministic subset without faking later agent/provider capabilities.

## Rejected alternatives

- **One monolithic "make audiobook" agent:** no safe checkpoints, provenance, focused review, or fault isolation.
- **Agents write SQLite/call SDKs directly:** bypasses storage, credential, privacy, cost, and provider boundaries.
- **In-memory orchestration:** loses state on restart and cannot audit approvals/attempts.
- **Automation wins conflicts:** destroys durable human provenance and author control.
