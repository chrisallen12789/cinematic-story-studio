# Database migration 0003: Phase 2 story intelligence

## Scope

Schema version 3 adds immutable whole-book analysis runs, agent executions,
snapshots, typed story-graph entities, human correction overlays, and four
analysis review gates. The v2-to-v3 step upgrades only the exact issued schema
version 2. Startup retains the supported v1-to-v2 path, then verifies and
backs up that exact v2 intermediate before applying v3. The migration is
forward-only; rollback uses a verified version-2 backup copy with a matching
Phase 1 application.

## Frozen version-2 precondition

Before any mutation, journal-mode change, or backup creation, startup inspects
the candidate schema-v2 database against the frozen v2 structural signature,
which the committed fixture exercises, and requires:

- `PRAGMA user_version = 2`;
- migration ledger `[1,2]`;
- the exact table/index allow-list;
- complete `table_xinfo` metadata;
- named and generated index semantics;
- normalized table and named-index SQL;
- all critical foreign keys, delete actions, uniqueness and checks; and
- no trigger, view, extra object, or same-version drift.

Failure returns `DATABASE_SCHEMA_UNSUPPORTED` and leaves the database
read-only/unmodified.

## Verified backup

The coordinated SQLite backup API creates
`<database-stem>.v2-backup<database-suffix>` before version-2 mutation. The
source and backup must both pass `quick_check`, version/ledger validation, and
the frozen structural signature, and must have matching canonical logical
SHA-256 digests. A temporary backup is permission-restricted and atomically
renamed only after verification.

An existing backup is reused only when it is a valid exact logical copy of the
current v2 database. Missing, corrupt, mismatched, or unwritable backup state
fails as `DATABASE_BACKUP_FAILED`; migration does not start.

## Version-3 records

Schema v3 adds records equivalent to:

- analysis profiles, runs, agent executions, and snapshots;
- evidence spans, confidence assessments, and analysis warnings;
- structural units, chapters, scenes, and story beats;
- character identities, alias claims, and mentions;
- dialogue lines, attribution candidates, and effective attributions;
- POV segments and locations;
- timeline events and temporal constraints;
- relationship edges;
- emotional states and dramatic intents;
- continuity findings and human dispositions;
- immutable human analysis corrections; and
- analysis gates and append-only decisions.

Every project-owned table is project-scoped; run-owned tables also carry run
lineage, while migrated legacy corrections may have a null run until compatible
projection. Records carry their applicable revision, fingerprint, provenance,
constraints, and paginated-query indexes. The migration appends ledger row 3
and sets `PRAGMA user_version = 3`.

## Version-2 preservation

The migration preserves every project, source document and revision,
extraction, parser execution, Import Review decision, imported story,
chapter/scene/beat, character, dialogue record, job/attempt/event/checkpoint,
idempotency record, and Phase 0/1 human speaker correction.

Existing Phase 0/1 story-graph rows and their known producer lineage remain in
their legacy tables. Migration creates no Phase 2 analysis run or snapshot.
Phase 0 speaker corrections are generalized into compatible Phase 2
correction/effective-attribution projections while the original correction
history remains intact.

No Phase 2 gate is auto-approved. New gates begin pending. Import Review
history remains the prerequisite for a new Phase 2 run.

## Transaction and verification

DDL, preservation/mapping, indexes, migration ledger, and `user_version` are
one immediate transaction. Foreign-key enforcement is disabled only when
SQLite table rebuild semantics require it and is restored before
post-transaction `foreign_key_check`. Before commit, the newly created v3
structure must match the exact v3 signature. After commit, quick and foreign
key checks run again.

An injected failure rolls back the working database to exact version 2 and
retains the separately verified v2 backup. Startup never repairs the only
copy.

## Compatibility tests

Executable tests use a genuine populated frozen-v2 fixture and cover:

- representative preservation of Phase 0/1 projects, sources, extractions,
  reviews, graph rows, jobs, and speaker-correction history;
- canonical legacy correction projection without creating a Phase 2 run,
  snapshot, or approval;
- verified backup creation plus rejection of corrupt backup state and backup
  publication failure;
- an injected post-DDL migration failure with transaction rollback and backup
  retention;
- rejection without mutation of same-version v2/v3 structural drift, ledger
  gaps, and future versions;
- the controlling analysis-entity pagination query plan; and
- quick, foreign-key, structural, reason-column, and restart validation after
  migration.

Those cases do not claim a separate empty-v2 fixture, exhaustive fault
injection at every data/foreign-key statement, or a dedicated index for every
optional entity filter.

## Downgrade and rollback

There is no in-place v3→v2 downgrade. Stop the application, retain the current
database and verified v2 backup, and restore only a copy of the backup with the
matching Phase 1 application. Never open v3 data with older code, copy a live
WAL database with generic file-copy commands, or delete the newer database
until recovery has been independently verified.
