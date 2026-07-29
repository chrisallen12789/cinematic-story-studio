# Project Data Model

## Aggregate and storage model

A `Project` is the ownership, revision, deletion, and concurrency boundary. The API/domain model is independent of whether Phase 0 stores all projects in one SQLite catalog or the packaged target uses a catalog plus one database per project. Every project-owned record carries/enforces `project_id`; repositories reject cross-project references.

The application-managed project directory contains only private data:

```text
project/
  project.db
  sources/       immutable imported bytes
  artifacts/     immutable published generated assets
  cache/         regenerable content-addressed data
  staging/       untrusted/incomplete work
  backups/       bounded pre-migration/recovery backups
```

None of these paths are committed. Exports are user-owned copies outside this tree.

## Shared conventions

- IDs are opaque UUID-compatible strings. Foreign keys use IDs plus project scope where needed.
- `revision` is a monotonically increasing integer used for optimistic concurrency; it is not a timestamp.
- Timestamps are UTC RFC 3339 in contracts and integer UTC values in storage.
- Enum values are closed and versioned.
- Story source spans are half-open UTF-8 byte offsets into the exact `ImportedStory.text`; optional line/column values are derived and 1-based.
- Ordering fields are explicit integers. Queries include stable ID tie-breakers.
- JSON columns contain schema-versioned typed values serialized canonically where identity matters. Arbitrary unvalidated blobs are not domain records.
- Content-addressed files are named by an algorithm-prefixed digest and published by atomic rename after verification.

## Core entities

| Entity | Required identity/state | Invariants |
| --- | --- | --- |
| `Project` | ID, name, schema version, revision, lifecycle status, created/updated time | Name is metadata, not a path; deletion is project-scoped. |
| `SourceDocument` | ID, project, media format, original display name, byte size/hash, encoding/extractor/version, immutable file reference | Raw bytes and digest never change; no absolute import path. |
| `ImportedStory` | ID, source ID, revision lineage, exact text/hash, extraction warnings, provenance | Text is immutable; rewriting creates another explicit artifact type/revision. |
| `Chapter` | ID, story revision, ordinal, title span/value, content span, confidence/warnings/provenance, revision | Ordered, non-overlapping at the same hierarchy level unless explicitly warned. |
| `Scene` | ID, chapter, ordinal, content span, heading/location/time/mood projections, confidence/warnings/provenance, revision | Contained within chapter; stable source order. |
| `StoryBeat` | ID, scene, ordinal, span, kind, summary/projection, provenance | Derived summary never replaces source. |
| `Character` | ID, project, canonical display name, aliases, evidence spans, confidence/warnings/provenance, revision | Merge/split is an explicit provenance event; IDs are not inferred solely from names. |
| `DialogueLine` | ID, scene, ordinal, exact content span, quote/content spans, revision | Display text is sliced from immutable story, not separately rewritten. |
| `DialogueAttribution` | ID, line, character ID or null, source `automated|human`, confidence/warnings, provenance, revision | Human value is protected; uncertainty permits null. |
| `VoiceProfile` | ID, project/character scope, style/range/constraints, revision/provenance | Contains no secret or unverified provider availability. |
| `CastingAssignment` | ID, character, voice profile/provider/voice refs, status, approval revision, provenance | `unassigned` is valid; provider/voice IDs are not fabricated. |
| `PerformanceDirection` | ID, dialogue/narration/scene target, typed parameters/notes, approval/provenance | References exact target revision. |
| `AmbienceCue`, `FoleyCue`, `MusicCue` | ID, scene/timeline range, asset/generation intent, gain/fade/priority, provenance/approval | Timing uses declared integer unit; music use is rights/policy reviewable. |
| `ProductionTimeline` | ID, project input revision, `milliseconds` timebase, sample rate/channel layout, ordered tracks/cues, manifest/provenance | Canonical order and non-negative integer-millisecond positions; the render compiler records its one-time conversion to samples. |
| `ContinuityRecord` | ID, scoped subject, finding/fact, evidence spans, severity/status, provenance | A finding is not silently converted into source fact. |
| `RenderManifest` | ID/hash, schema version, frozen input refs/hashes, tool/adapter/policy versions, timeline, outputs | Immutable canonical document; volatile timestamps excluded from identity. |
| `QualityControlFinding` | ID, artifact/manifest, rule/version, severity, location, measured/expected values, disposition | Waivers are explicit approval decisions. |
| `ApprovalDecision` | ID, gate, artifact type/ID/revision/hash, `approved|rejected|changes_requested|revoked`, actor, reason, time | Append-only; a new revision requires a new decision. |

`RenderJob` is the render-specific typed input/result associated with the generic persisted `Job` and `JobAttempt` described in [background-jobs.md](background-jobs.md).

## Human correction provenance

A speaker change executes one transaction:

1. load the dialogue line/current attribution within the project;
2. compare `expectedRevision`;
3. verify the new character belongs to the same project;
4. append `HumanCorrection {target, fieldPath, previousValueFingerprint, correctedValue, reason, authority, recordedAt, supersedesCorrectionId?}`;
5. write a new/current attribution projection with `source=human`;
6. increment target and project revisions;
7. append an outbox/domain event.

Correction and approval events are append-only. Deleting a character with protected references is blocked until an explicit merge/reassignment operation records provenance. Automated analysis receives protected corrections as constraints and may emit a conflict warning, not overwrite them.

## Provenance envelope

Every derived artifact carries:

```text
producerType: importer | parser | runtime_agent | human | provider | render_tool
producerId / producerVersion
inputRevisions: ordered entity references and hashes
configurationHash
providerId / modelId / modelVersion (when applicable)
seed / determinism declaration (when applicable)
createdAt
cost: currency, estimated/actual amount, metered units (when applicable)
```

Cost state is explicit: known-zero local/deterministic monetary cost is recorded as zero, while `not_applicable`, `unknown`, and `not_reported` remain distinct. Provider request/response content is not embedded in general logs; retained provenance artifacts follow project privacy and deletion rules.

## Relational persistence

SQLite uses foreign keys, uniqueness constraints, check constraints for revisions/ranges/progress, and indexes beginning with `project_id` for project-scoped access. Important tables include:

- `projects`, `source_documents`, `imported_stories`;
- `chapters`, `scenes`, `story_beats`, `characters`, `dialogue_lines`, `dialogue_attributions`;
- `human_corrections`, `approval_decisions`, `provenance_records`;
- `voice_profiles`, `casting_assignments`, direction/cue/timeline tables;
- `jobs`, `job_attempts`, `job_events`, `job_checkpoints`, `job_leases`;
- `render_manifests`, `artifacts`, `qc_findings`;
- `idempotency_records`, `schema_migrations`, and transactional `outbox_events`.

Large source/audio bytes are files, not SQLite BLOBs; SQLite stores verified relative references and hashes. Any resolved path must remain beneath its typed root. Secret values are never columns: provider settings contain an opaque OS credential reference and non-secret configuration only.

## Transactions, migrations, and integrity

- Enable foreign keys on every connection, use WAL where filesystem support is known, and set a finite busy timeout.
- Do not hold a transaction while parsing, calling a provider, or executing FFmpeg.
- Publish an analysis/render result only if its input revision still matches or the use case explicitly creates a branched revision.
- Use numbered, forward-only migrations with application/contract compatibility checks. Back up before destructive migration; keep the original until post-migration integrity checks succeed.
- On startup run lightweight checks; use explicit recovery flow for full integrity checking. Never "repair" the only copy in place.
- Database, source, and manifest backups remain private local data and follow bounded retention/deletion policy.

## Deletion and retention

Project deletion first blocks new work, cancels/interrupts active jobs, closes leases, and records the exact managed roots. It then removes catalog/project references and application-managed source, artifact, cache, staging, backup, and project credential references. User-selected exports and shared credentials are excluded unless separately selected.

SQLite secure-delete/VACUUM and file deletion reduce ordinary exposure but cannot guarantee forensic erasure on SSDs, backups, sync tools, or OS restore points; the UI discloses that limitation. Cache cleanup is reference/lease aware and idempotent.
