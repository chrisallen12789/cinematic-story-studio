# Database migration 0004: Phase 3A voice casting

## Scope

Schema version 4 adds the provider-neutral voice-catalog, rights, production-role,
candidate, assignment, correction, snapshot, and casting-gate records needed by
Phase 3A. The v3-to-v4 step accepts only the exact issued Phase 2 schema version 3.
Startup retains the already governed v1-to-v2-to-v3 chain, creates and verifies each
versioned intermediate backup, and then applies version 4.

The migration is forward-only. Recovery to schema version 3 uses a separately
verified v3 backup copy and a matching Phase 2 application; there is no in-place
downgrade.

## Frozen version-3 precondition

Before journal-mode change, backup creation, or schema mutation, startup requires:

- `PRAGMA user_version = 3`;
- the exact ledger `[1, 2, 3]`;
- the frozen Phase 2 table and index allow-list;
- exact ordered `table_xinfo` metadata, including types, nullability, defaults,
  primary-key ordinals, and generated-column state;
- exact named and SQLite-generated index semantics;
- normalized table and named-index SQL, including foreign keys, delete actions,
  uniqueness, and checks; and
- no trigger, view, extra object, missing object, ledger gap, or same-version drift.

The committed `phase2-v3-schema.sql` fixture is a static SQLite dump produced by the
issued v2-to-v3 migration. It contains synthetic Phase 0/1 history and the migrated
speaker-correction projection. Migration tests add a complete synthetic Phase 2
run, execution, snapshot, checkpoint, agent envelope, entity, evidence span, and
four human gate decisions before upgrading, then compare every pre-v4 table and row.

An invalid precondition returns `DATABASE_SCHEMA_UNSUPPORTED`. No backup or migration
begins, and the candidate database remains unmodified.

## Verified version-3 backup

Before changing an accepted v3 database, the coordinated SQLite backup API creates:

`<database-stem>.v3-backup<database-suffix>`

The source, temporary copy, and any pre-existing backup must pass:

1. frozen v3 structural-signature validation;
2. contiguous ledger validation;
3. `PRAGMA quick_check`;
4. `PRAGMA foreign_key_check`;
5. `PRAGMA user_version = 3`; and
6. canonical logical SHA-256 comparison over SQLite `iterdump` statements.

The temporary copy receives private file permissions where the platform permits and
is atomically renamed only after verification. An existing backup is reused only
when it is structurally valid and its logical digest exactly matches the current v3
source. A corrupt, stale, mismatched, or unpublishable backup fails with
`DATABASE_BACKUP_FAILED` before migration.

The verified v3 backup is retained after both successful and failed migration. It is
never rewritten as part of v4 recovery.

## Version-4 records

Schema v4 adds these 16 tables:

1. `voice_catalog_revisions`
2. `voice_provider_descriptors`
3. `voice_model_descriptors`
4. `voice_profiles`
5. `voice_rights_records`
6. `casting_profiles`
7. `casting_runs`
8. `production_roles`
9. `casting_candidates`
10. `casting_conflicts`
11. `cast_assignments`
12. `cast_assignment_invalidations`
13. `casting_corrections`
14. `approved_cast_snapshots`
15. `casting_gate_reviews`
16. `casting_gate_decisions`

The schema distinguishes stable provider/model/profile identities from immutable
catalog publications. Voice profiles contain declared casting attributes, not
claims about real people. Rights records support `verified`, `restricted`,
`unknown`, and `prohibited` states with commercial-use, attribution, consent,
cloning, limitation, evidence-reference, verification, revision, fingerprint, and
provenance fields.

`voice_profiles.profile_version` preserves the catalog's exact semantic
profile version independently from the integer database evidence revision.

Casting runs freeze all required Phase 2 source, extraction, Import Review, analysis
run/snapshot, correction-set, character-registry, and four-gate evidence together
with the catalog revision, casting profile, producer, job, and deterministic
fingerprints. Production roles support primary narrator, secondary narrator, named
character, unresolved speaker, group/crowd, quoted document or announcement,
internal thought, and custom roles. A custom role uses the same bounded table but
has no Phase 2 entity, records human content-free definition provenance, and retains
zero manuscript-derived workload with null story ranges.

`production_roles.character_id` preserves the stable effective Phase 2
Character Registry identity separately from the analysis entity reference.
`role_importance` stores only `major|supporting|minor|unresolved`; absent
approved importance is represented conservatively as `supporting`. Language,
locale, and nullable performance requirement projections preserve `und`,
empty, and null unknown states rather than fabricating an inference.

Candidate rows separate hard-constraint results from soft preferences and preserve
unknown compatibility. Candidate ordinals are bounded below 50, and
`role_revision` identifies the immutable role-evidence generation. A changed
casting requirement appends a new generation and supersedes affected static
conflicts instead of deleting historical candidate or conflict evidence. Conflicts are
explicitly metadata-based and never claim acoustic similarity. Assignments retain
machine-proposal, human-selection, and human-locked authority as immutable
revisions. Assignment-derived conflicts likewise retain immutable details and
fingerprints: a new assignment-evidence generation supersedes stale active rows
and appends uniquely identified replacements without deleting rows referenced by
corrections. Every human assignment revision carries a unique restrictive
`correction_id` foreign key to the exact correction that created it; machine
proposals require that link to remain null. This direct association remains
unambiguous even when multiple corrections share the same recorded timestamp.
`cast_assignment_invalidations` binds one append-only durable drift record to an
exact assignment, role, run, reason-code set, evidence fingerprint, provenance,
and time. Corrections and all narrator, character, and complete-cast gate decisions
are append-only. Human decisions may approve, request changes, or reject; only a
system-authored decision may use `invalidated`, and the system cannot approve.

`casting_corrections.supersedes_correction_id` is a restrictive self-reference with
the named unique constraint `uq_casting_correction_single_successor`. The service
assigns that link only to the current semantic leaf: assignment-state operations
chain together, label/requirement/restricted-rights changes chain within their own
kind, an explicit selection can override its exact active candidate rejection, and
an unlock must supersede the correction that created the current lock. The unique
constraint rejects competing successors even if application-level race detection
is bypassed.

The `approved_cast_snapshots` name denotes the immutable artifact eligible for gate
review; inserting a snapshot does not approve it.

## No fabricated Phase 3A evidence

The migration creates only tables, constraints, indexes, ledger row 4, and
`PRAGMA user_version = 4`. It does not insert:

- a provider or model descriptor;
- a catalog revision or voice profile;
- a rights assertion;
- a casting profile or run;
- a production role or candidate;
- a conflict or assignment;
- an assignment invalidation;
- a casting correction;
- a cast snapshot; or
- a casting review or approval.

Catalog publication and casting remain explicit application operations after all
Phase 2 preconditions have been revalidated. Migration never invents a voice
assignment, never converts a Phase 2 approval into casting approval, and never
enables synthesis, network use, credentials, model download, or cloud transmission.
Custom-role creation is likewise an authenticated post-migration application
mutation against a succeeded run; it is not migration seed data.

## Preservation

The v3-to-v4 transaction does not update or rebuild any Phase 0, Phase 1, or Phase 2
table. It preserves all projects, sources, extractions, Import Review decisions,
stories, story-graph rows, jobs and checkpoints, parser executions, human
corrections, analysis runs and executions, snapshots, agent evidence, analysis
entities and evidence spans, Phase 2 correction overlays, and four Phase 2 gate
histories byte-for-byte at the SQLite value level.

Foreign keys from new casting evidence use restrictive deletion for frozen catalog,
rights, Phase 2, job, and supersession lineage. Project- or run-owned derived rows
use scoped cascades only where deleting the owning project or unpublished run is
already authoritative.

Appending a custom role does not alter schema or Phase 0-2 rows. It appends the role
and its bounded derived evidence, then creates a new immutable cast snapshot and
review revisions in one application transaction; prior gate decisions remain stored
against the older snapshot.

## Transaction and compatibility signature

All 16 tables, generated and named indexes, ledger row 4, `user_version`, foreign-key
check, and frozen v4 signature validation occur in one `BEGIN IMMEDIATE`
transaction. Any exception rolls the working database back to exact schema version
3 while leaving the verified v3 backup available.

The exact v4 contract accepts two physical layouts:

- fresh creation; and
- the supported historical v1-to-v2-to-v3-to-v4 layout, whose Phase 1 columns retain
  their issued migration ordering/defaults.

The frozen signatures are:

- table metadata, fresh:
  `b6614c36b0eca6e77d8de0c5c120ac5bb05cc054496a52f8c5ea68f64faf0f3b`;
- table metadata, migrated:
  `0e4c66c6fe855e1eedc7d2e1ef687e2e49c9d0c505c58a0e66fff34e5389d122`;
- all-index semantics:
  `fa5c2efb6de3c7c7c6f22dc99572e8e8318663499c22ba6ed294975286460360`;
- normalized schema SQL, fresh:
  `f633a359fb0e1068f6638d3856e04764a8749c64b29d956e73af28fd97f1750d`;
  and
- normalized schema SQL, migrated:
  `5eea446d0c1aefce883468880dd647494cd3ffe5292b713b02375632a907048a`.

These values are literals independent of runtime ORM reflection. Same-version v4
drift therefore fails closed rather than being silently repaired.

Covering indexes support bounded catalog, role, candidate, conflict, assignment,
correction, snapshot, and gate pagination. Executable query-plan tests cover the
controlling voice-profile, production-role, and casting-candidate list queries.

## Executable migration evidence

Migration tests cover:

- fresh v4 creation, contiguous ledger, frozen signature, and restart;
- complete v1, v2, and frozen-v3 upgrade chains;
- preservation of synthetic records across every Phase 0-2 table;
- empty Phase 3A catalog, role, assignment, assignment-invalidation,
  correction, snapshot, and gate tables immediately after migration;
- verified v3 backup structure and logical digest;
- corrupt or unpublishable backup rejection before mutation;
- injected post-DDL failure, transaction rollback, and backup retention;
- rejection of v3 ledger, version, table, and column drift before backup;
- rejection of same-version v4 object and index drift without repair;
- forged in-place downgrade rejection while the v3 recovery backup remains valid;
- quick and foreign-key checks; and
- indexed bounded pagination query plans.

## Downgrade and recovery

There is no in-place v4-to-v3 downgrade and no supported deletion of only the 16
Phase 3A tables. Such a deletion would discard casting history, including the
durable assignment-invalidation latch and its system decision lineage, and
would not restore the exact issued v3 signature safely. Catalog reversion is
not a downgrade mechanism and cannot reactivate an invalidated assignment.

To recover:

1. stop the application;
2. retain the v4 database, WAL/SHM siblings when present, and verified v3 backup;
3. verify the backup independently;
4. copy the verified backup to a separately named recovery database using a
   coordinated SQLite-safe operation;
5. open only that copy with the matching Phase 2 application; and
6. keep the v4 database until recovery evidence is complete.

Never change `user_version` or the ledger in place, never delete v4 tables to mimic a
downgrade, never open v4 data with older code, and never overwrite the sole database
or its verified backup.
