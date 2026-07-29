# ADR-0001: Use a Boundary-Driven Monorepo

- **Status:** Accepted
- **Date:** 2026-07-29

## Context

The desktop, Python service, contracts, import/analysis, jobs, providers, agents, audio/render code, tests, installer, and documentation must evolve together. Separate repositories would make cross-runtime contract changes and a single Windows release harder to verify. An unstructured repository would encourage direct imports and disconnected scripts.

## Decision

Use one repository and release train, with pnpm workspace tooling for TypeScript and a pinned Python project/environment for the local service. Organize by deployable application and domain/infrastructure boundary:

- `apps/desktop` and `apps/local-service`;
- shared/versioned contracts with generated/validated TypeScript artifacts;
- domain modules for storage, import/analysis, jobs, agents, providers, audio, and rendering;
- synthetic fixtures and unit/integration/audio/end-to-end tests;
- installer, scripts, schemas, documentation, and sanitized prototype reference.

Dependency direction follows the system overview. Being in one repository does not authorize one package to access another subsystem's database, process, secret, or implementation directly. Root commands orchestrate development, lint, type-check, test, and build on Windows.

## Consequences

- Atomic changes can update schemas, both runtimes, tests, and docs.
- CI can detect contract drift and build one compatible desktop/service unit.
- Toolchains and dependency locks coexist and require disciplined caching/versioning.
- Repository size grows, so generated binaries, models, dependencies, private data, audio, and build output remain ignored.
- Ownership rules and architecture tests are needed to preserve boundaries.

## Rejected alternatives

- **Separate repositories now:** premature operational overhead and unsafe version skew.
- **One flat application/scripts folder:** no enforceable subsystem boundaries or release composition.
- **All TypeScript or all Python solely for repository uniformity:** sacrifices the selected desktop or media/analysis ecosystem without reducing the cross-process boundary.
