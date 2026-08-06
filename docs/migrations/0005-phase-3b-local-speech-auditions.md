# Database migration 0005: Phase 3B local speech auditions

## Scope

Schema version 5 adds the durable managed-runtime, model-package,
pronunciation, audition, audio-integrity, cache, review, readiness, and targeted
invalidation records required by Phase 3B. The direct v4-to-v5 step accepts only
the exact issued Phase 3A schema version 4. The supported v1-to-v2-to-v3-to-v4
chain remains available and creates each verified intermediate backup before the
same v4-to-v5 boundary is entered.

This is a forward-only migration. Recovery to Phase 3A uses a separately verified
v4 backup copy and a matching Phase 3A application. Changing `user_version`,
deleting Phase 3B tables, or editing the migration ledger is not a downgrade.

## Frozen version-4 precondition

Before journal-mode change, v4 backup creation, or schema mutation, startup
requires all of the following:

- `PRAGMA user_version = 4`;
- the exact ledger `[1, 2, 3, 4]`;
- the frozen v4 table, column, primary-key, and index allow-lists;
- exact ordered `table_xinfo` metadata, including declared types, nullability,
  defaults, primary-key ordinals, and generated-column state;
- exact named and SQLite-generated index semantics;
- normalized table and named-index SQL, including foreign keys, delete actions,
  uniqueness, and checks; and
- no trigger, view, extra object, missing object, ledger gap, or same-version
  drift.

The committed `apps/local-service/tests/fixtures/phase3a-v4-schema.sql` fixture is
a 67,675-byte static dump captured from issued `main@e32f315` before the v5 ORM
was added. Its SHA-256 is
`50dc228bb25466571b2600e1eb14a60dadee0539971f78245569a6ca8b201290`.
It opens as schema version 4 with 44 non-SQLite tables including the migration
ledger, passes quick and foreign-key checks, and contains no Phase 3B table.

An invalid precondition returns `DATABASE_SCHEMA_UNSUPPORTED`. Startup does not
create a backup or attempt repair, and the candidate database remains unmodified.

## Verified version-4 backup

Before changing an accepted v4 database, the coordinated SQLite backup API
creates:

`<database-stem>.v4-backup<database-suffix>`

The source, temporary copy, and any existing backup must pass frozen v4 structural
validation, contiguous-ledger validation, `PRAGMA quick_check`,
`PRAGMA foreign_key_check`, `PRAGMA user_version = 4`, and canonical logical
SHA-256 comparison over SQLite `iterdump` statements. The temporary copy receives
private file permissions where supported and is atomically published only after
verification.

An existing backup is reused only when its structure and logical digest exactly
match the current v4 source. A corrupt, stale, mismatched, or unpublishable backup
fails with `DATABASE_BACKUP_FAILED` before migration. The verified backup remains
available after both successful migration and rolled-back migration failure.

## Version-5 records

Schema v5 adds these 22 tables:

1. `speech_runtime_profiles`
2. `speech_runtime_instances`
3. `model_package_manifests`
4. `model_installations`
5. `model_verifications`
6. `voice_runtime_bindings`
7. `pronunciation_dictionaries`
8. `pronunciation_entries`
9. `audition_sessions`
10. `audition_scripts`
11. `text_normalization_plans`
12. `speech_provider_requests`
13. `audition_clips`
14. `audio_artifacts`
15. `audition_cache_records`
16. `audio_quality_records`
17. `audition_review_records`
18. `audition_review_decisions`
19. `voice_readiness_snapshots`
20. `voice_readiness_reviews`
21. `voice_readiness_decisions`
22. `audition_evidence_invalidations`

Runtime profiles freeze offline policy, protocol, provider/runtime identity,
bounds, output formats, and fingerprints. Runtime instances bind the exact profile,
installation, verification, process identity, authenticated handshake, lifecycle,
and health evidence. Model manifests record trusted inventory, source, license,
commercial-use, compatibility, revocation, and provenance. Installation state
transitions and exact-file verifications are immutable revisions; activation must
refer to successful verification evidence.

Pronunciation dictionaries and entries are append-only revisions with exact
`project`, `narrator`, `character_role`, `chapter`, `scene`, or `custom` scope.
Normalization records original and normalized hashes, explicit transformations,
the exact dictionary and entry dependencies, and the compiled pronunciation-plan
fingerprint. Raw SSML and provider control markup are not accepted as
pronunciation evidence.

An audition session records the complete source, Phase 2, and Phase 3A authority
lineage for one role: the latest succeeded casting run, exact validated immutable
cast-snapshot manifest, one assignment ID/revision and lock state selected by
that manifest, exact leaf catalog/provider/model/profile/rights evidence with
temporally applicable rights, and latest eligible Phase 3A review plus exact
human decision identity, provenance, supersession, and warning acknowledgements.
These stored rows are not self-authorizing: current projections revalidate the
chain, never combine two cast snapshots, suppress old-snapshot evidence when a
newer snapshot becomes current, and treat a new same-snapshot Phase 3A decision
tuple as downstream-invalidating evidence.

Script metadata stores a content
hash and an opaque private storage key or synthetic-text identity; raw manuscript
text is not stored in these tables. Provider-request records are likewise
content-free. Audio tables store opaque relative storage keys, hashes, bounded
PCM16 WAV metadata, and machine integrity measurements, never audio bytes or an
unmanaged absolute path.

Cache entries bind exact request and artifact evidence. A hit is usable only while
the expected bytes and WAV metadata still verify. Historical clip and quality
records remain immutable when a cache entry becomes corrupt, missing, or cleared.

The exact gates are `per_role_audition_review`,
`narrator_audition_review`, `character_audition_review`,
`pronunciation_review`, and `voice_readiness_review`. Human and system actor IDs
are explicit. Human actors may approve, reject, or request changes; only a system
actor may append `invalidated`. Readiness snapshots and decisions bind the exact
cast, model-verification, dictionary, audition, pronunciation, and prior Phase 3A
decision evidence.

`audition_evidence_invalidations` is an append-only targeted dependency ledger.
It identifies the exact clip, session, role, changed source record, prior and
current fingerprints, reason, and affected reviews. Assignment, rights, model,
provider (including session-owned governed activation authority), runtime,
applicable pronunciation, cast-snapshot, and audio-integrity drift can therefore
invalidate dependent evidence without deleting unrelated
clips or historical decisions. A detected mismatch between a review and its
persisted clip binding is recorded as `review_clip_binding`; that typed source is
part of the frozen v5 check constraint and cannot be confused with byte-level
`audio_integrity` drift.

Startup audio reconciliation inventories database artifact rows and every
inspected audio/staging directory entry under one 100,000-entry global bound
before changing storage. It removes only exact owned UUID `.wav`, `.wav.pending`,
atomic temporary, and purge-tombstone identities. Noncanonical WAV names and
other unknown files remain untouched; exceeding the bound fails startup with a
redacted reconciliation error before partial cleanup.

The existing job persistence tables require no structural rewrite: their job type,
event type, and checkpoint type columns were already bounded strings without a SQL
enum check, so the Phase 3B `generate_audition` job type is structurally accepted
while all prior job types remain valid.

## No fabricated speech or approval evidence

The migration creates only tables, constraints, indexes, ledger row 5, and
`PRAGMA user_version = 5`. It does not:

- install, download, verify, or activate a model package;
- start or adopt a speech process;
- seed a provider, runtime profile, dictionary, pronunciation, or audition;
- copy manuscript text into the Phase 3B relational tables;
- generate, import, or publish audio;
- populate a cache entry or claim audio quality;
- create, infer, preserve, or renew a human approval; or
- authorize full-book rendering.

Those are explicit authenticated application operations after all current
prerequisites and fingerprints have been revalidated.

## Preservation and transaction boundary

The v4-to-v5 step does not update or rebuild any Phase 0 through Phase 3A table.
All 22 new tables, their generated and named indexes, ledger row 5,
`user_version`, foreign-key check, and exact v5 signature validation occur in one
`BEGIN IMMEDIATE` transaction. Any exception rolls the working database back to
exact schema version 4 while retaining the verified v4 backup.

Fresh v5 creation writes the contiguous ledger `[1, 2, 3, 4, 5]`. Supported
historical chains preserve the issued ordering and defaults of older tables, so
the exact v5 contract deliberately accepts two physical layouts: fresh or direct
frozen-v4 creation, and the historical migrated layout.

The frozen v5 signatures are:

- table metadata, fresh/direct-v4:
  `53c3d00b4068e81238ce310e24c18a65a743c1081551d4de46fe4c190f9e92a7`;
- table metadata, historical:
  `3217d17fe82e7aeb3486cbd5e8c1347cd92d1cd153a354738af2eeb869ef5d6c`;
- all-index semantics:
  `6bc0dc622ae8c2d408cd6058f864a779af5f04244f48b13d7533144d4ab60754`;
- normalized schema SQL, fresh/direct-v4:
  `0d7b10d66021ec05de61abe653f38c7b5e647b5dd26f0615f335cb70ab2dbc58`;
  and
- normalized schema SQL, historical:
  `873280a52933dfc3804e6a3203aefaa01616c44229acc6b41e3c0a923fb462cc`.

These are literal compatibility signatures independent of runtime ORM reflection.
Same-version v5 drift fails closed and is never silently repaired.

## Executable migration evidence

Focused migration tests cover:

- the independently executable frozen v4 fixture and its issued signature;
- fresh v5 creation, contiguous ledger, empty Phase 3B storage, and restart;
- every supported v1, v2, v3, and v4 upgrade origin;
- value-level preservation of representative Phase 0 and Phase 3A history;
- exact verified v4 backup structure and logical digest;
- canonical provider, model, runtime, pronunciation, session, script, request,
  cache, actor-authority, WAV, and limit constraints;
- restrictive history foreign keys and exact five-gate identities;
- corrupt or unpublishable backup rejection before mutation;
- injected post-DDL failure, complete transaction rollback, and backup retention;
- rejection of v4 ledger, version, object, column, and index drift before backup;
- rejection of same-version v5 object, column, and index drift without repair;
- forged in-place downgrade rejection while the verified v4 backup remains valid;
- quick and foreign-key checks; and
- covering-index query plans for pronunciation lookup, cache verification, and
  per-role clip pagination.

## Recovery

To recover Phase 3A data, stop the application, retain the v5 database and any
WAL/SHM siblings, independently verify the v4 backup, and copy that backup to a
separately named recovery database using a coordinated SQLite-safe operation. Open
only the copy with the matching Phase 3A application and retain the v5 database
until recovery evidence is complete. Never overwrite the sole database or its
verified backup, and never forge a lower ledger or `user_version` in place.
