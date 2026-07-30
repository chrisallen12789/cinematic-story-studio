# Database migration 0002: Phase 1 secure ingest

## Scope

Schema version 2 adds durable extraction, parser execution, and Import Review
history while preserving every Phase 0 project, source/story revision, analysis
job, dialogue attribution, and human correction. The migration is forward-only;
rollback restores the verified version-1 backup as a separate recovery copy
rather than attempting a reverse in-place migration.

## Pre-migration backup

Before any version-1 mutation, the service uses SQLite's backup API to create
`<database-stem>.v1-backup<database-suffix>` beside the database. It validates
the source and backup with `PRAGMA quick_check`, requires `user_version = 1` and
ledger `[1]`, and compares canonical logical SHA-256 digests. A temporary backup
is permission-restricted and atomically renamed only after verification.

Before that backup or any journal-mode change, startup also compares the
version-1 database with the frozen Phase 0 structural contract. The exact table
and index object allow-list, complete `table_xinfo` metadata, generated and
named index semantics, and normalized schema SQL must match the committed
Phase 0 shape. Triggers, views, missing objects, and same-version column,
constraint, or index changes fail read-only as `DATABASE_SCHEMA_UNSUPPORTED`.

An existing backup is reused only when it passes the same checks and matches
the current version-1 logical digest. A missing, corrupt, mismatched, or
unwritable backup fails startup with `DATABASE_BACKUP_FAILED`; migration does
not begin.

## Version-2 changes

- Rebuild `source_documents` to add logical source revision lineage,
  supersession, extraction status, and project/revision uniqueness while
  replacing the Phase 0 project/content-hash uniqueness rule.
- Add immutable `document_extractions`, including adapter/version, exact text
  and digest, evidence/limits metadata, sections, source mappings, warnings, and
  revision lineage.
- Rebuild `imported_stories` to reference one exact extraction and extraction
  revision.
- Add append-only `parser_executions` for attempt, dependency/limit, outcome,
  duration, warnings, and redacted failure evidence.
- Add append-only `import_reviews`, including review lineage, source/extraction
  revisions, preview/warnings, evidence fingerprint, human decision, actor,
  reason, idempotency key, and invalidation.
- Extend `jobs` with typed target and payload fields so extraction and analysis
  jobs bind to explicit durable inputs.
- Add the project/source/target indexes and foreign-key/check/uniqueness
  constraints declared by the schema-v2 models.
- Append ledger row 2 and set `PRAGMA user_version = 2`.

## Same-version compatibility validation

A version-2 ledger of `[1, 2]` is necessary but not sufficient for startup. For
an existing version-2 database, before changing journal mode or running any DDL
or data operation, the service read-only inspects an explicit version-2
structural allow-list:

- the exact set of required tables and indexes, with no triggers or views;
- complete `table_xinfo` column order, declared types, nullability, SQL
  defaults, primary-key ordinals, and hidden/generated state;
- every named and generated index, including uniqueness, origin/partial flags,
  key order, descending flags, and collations;
- normalized schema SQL for all tables and named indexes;
- the critical source, extraction, imported-story, Import Review, parser
  execution, and job lineage foreign keys and their delete actions;
- the critical source/extraction/review/parser uniqueness constraints; and
- the named status, revision, attempt, sequence, progress, and non-negative
  `CHECK` constraints that enforce ingest, review, and job invariants.

A missing, extra, or altered allow-listed structure fails closed with the
redacted `DATABASE_SCHEMA_UNSUPPORTED` response. Startup does not auto-create,
auto-repair, migrate, change WAL mode, or otherwise mutate that database.
Recovery therefore starts from a preserved copy rather than accepting a
same-version database whose provenance or compatibility cannot be established.

Fresh version-2 creation and version-1-to-version-2 migration run the same
read-only signature inspection after constructing the intended schema but
before committing its schema-producing transaction. A mismatch rolls back
fresh creation completely. A migration mismatch restores the working database
to version 1 and retains its separately verified version-1 backup.

## Phase 0 data synthesis

Existing sources receive deterministic project-local source revision numbers
in import order and retain their original IDs, hashes, storage keys, timestamps,
and provenance. Existing imported stories reference a synthesized
`legacy_phase0_import` extraction revision. A matching parser execution record
states that the Phase 0 limits profile was not recorded; it does not fabricate
modern parser evidence. A structurally valid legacy source with no matching
imported story is retained with matching `failed` source/extraction state and a
redacted `LEGACY_STORY_MISSING` parser record.

Each existing imported story receives a pending Import Review. Migration never
silently approves legacy content. Existing analysis results and human speaker
corrections remain inspectable; starting new analysis against an extraction
requires the current Phase 1 gate.

## Transaction and verification

The single data-directory lock excludes another service. Table rebuilds,
additive tables, data synthesis, indexes, ledger row, and `user_version` change
run inside one immediate transaction. Foreign-key enforcement is disabled only
for the SQLite table-rebuild transaction, restored afterward, and followed by
`PRAGMA foreign_key_check`. Any exception rolls the transaction back.

Migration tests must cover:

- representative empty and populated version-1 databases;
- preservation of source bytes/hashes, exact text, analysis, corrections,
  revision order, job attempts/events, and idempotency;
- deterministic synthesized extraction/review records;
- backup creation, reuse, mismatch, corruption, and permission failure;
- read-only rejection of a forged or structurally incomplete version-1 file
  before backup creation or WAL activation;
- representative injected DDL and foreign-key failures with full rollback;
- rejection of missing, noncontiguous, future, or inconsistent schema ledgers;
- rejection without mutation of same-version version-2 databases whose valid
  `[1, 2]` ledger is paired with a trigger/view, missing table/column/index,
  changed type/nullability/default, or altered critical foreign key, unique
  index, or `CHECK` constraint;
- consistent failed source/extraction/parser state for a valid legacy source
  without an imported-story row;
- foreign-key and quick-check success after migration and after restart.

## Recovery and rollback

Do not overwrite the failed/current database or delete the verified backup.
Stop the application, preserve both files, and open a copy of the verified
version-1 backup with the matching Phase 0 application for rollback. To retry
Phase 1, first diagnose the redacted failure and operate on another verified
copy. The application must never copy a live WAL database with generic file
copy commands or “repair” the only copy in place.
