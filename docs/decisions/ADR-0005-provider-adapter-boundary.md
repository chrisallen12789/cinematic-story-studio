# ADR-0005: Isolate External and Local Capabilities Behind Provider Adapters

- **Status:** Accepted
- **Date:** 2026-07-29

## Context

Speech, analysis, and audio capabilities may come from bundled local runtimes, development containers, or optional cloud vendors with different APIs, costs, privacy, failure, and determinism. Coupling project/agent/render code to one SDK would make cloud required and provenance/fallback behavior inconsistent.

## Decision

Define capability-specific typed ports for health, descriptors, estimation, invocation, cancellation, and reconciliation. Register adapters through a provider registry. Every result reports adapter/provider/model/voice versions, input/config hashes, warnings, determinism, usage/cost, and artifact/output validation metadata.

Core launch/project/import/review work has no cloud dependency. Cloud adapters are disabled by default and require OS-protected credentials plus a durable provider/content/purpose disclosure decision before transmission. Retry, idempotency, cost limits, health, and fallback are explicit policy; fallback is never silent.

## Consequences

- Local, development, and cloud implementations remain interchangeable at the contract level.
- Adapters absorb vendor-specific behavior and require contract/redaction tests.
- The common contract exposes honest capability differences rather than a lowest-common-denominator success flag.
- Frozen non-deterministic outputs become hashed provenance inputs for reproducible downstream rendering.

## Rejected alternatives

- **Call provider SDKs from agents/routes:** leaks credentials/policy and makes errors/provenance inconsistent.
- **One generic prompt/string interface:** cannot safely validate capabilities, content scope, outputs, cancellation, or cost.
- **Automatic provider fallback:** can change voice/quality/privacy/cost without approval.
- **Require a cloud account to launch:** violates local-first and offline goals.
