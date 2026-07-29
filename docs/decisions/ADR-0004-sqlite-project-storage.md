# ADR-0004: Use SQLite Behind a Project Repository Boundary

- **Status:** Accepted
- **Date:** 2026-07-29

## Context

Projects need relational integrity, durable corrections/approvals/jobs, restart recovery, transactions, migrations, and local backup without asking users to administer a database server. Large source/audio files should not become database blobs.

## Decision

Use SQLite for application/project metadata and state, accessed only through typed repository/unit-of-work interfaces in the local service. Store immutable source/audio bytes in project-scoped managed files with relative references and verified hashes.

The packaged target uses a small application catalog plus a self-contained database/directory per project. Phase 0 may use one application-level SQLite database for all projects if project scope is enforced on every owned table/query and physical topology is hidden behind `ProjectRepository`. This preserves a tested migration path without delaying the slice.

Enable foreign keys, integrity constraints, explicit migrations/backups, short transactions, optimistic revisions, and WAL only on supported local filesystems. Jobs/events/corrections/approvals/manifests retain history. Secrets are OS credential-store references, never plaintext columns.

## Consequences

- Installation and backup are simple; offline transactions support the required slice.
- SQLite has one-writer constraints; CPU/provider work must occur outside transactions and publication is brief.
- Per-project isolation/deletion/portability improve with target topology but require catalog coordination.
- Schema evolution, WAL-safe backup, corruption recovery, and topology migration need executable tests.

## Rejected alternatives

- **PostgreSQL/server database:** excessive end-user administration and process footprint.
- **JSON files as primary storage:** weak concurrency, relational integrity, querying, and crash-safe job transitions.
- **Store large audio/source BLOBs:** increases write amplification, backup/corruption scope, and streaming complexity.
- **Allow every module direct SQL access:** bypasses scope, invariants, migrations, and testable boundaries.
