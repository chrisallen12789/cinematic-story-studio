# ADR-0010: Govern deterministic whole-book story intelligence

- **Status:** Accepted for Phase 2
- **Date:** 2026-07-30

## Context

Later casting, performance, ambience, continuity, and audio-QC work needs a
stable whole-book model rather than a generic summary. Story structure,
identity, dialogue, chronology, relationship, emotion, and continuity are
often ambiguous. A monolithic or cloud-required analyzer would weaken local
privacy, evidence traceability, cancellation/recovery, and human authority.

## Decision

Implement Phase 2 as one immutable, versioned `StoryAnalysisRun` with
append-only runtime-agent executions and a validated reviewable snapshot. The
baseline analyzers are deterministic, local, rule-based transformations behind
the existing persisted runtime-agent envelope.

Every interpretive claim carries exact approved-input lineage, evidence spans,
confidence classification, warnings, producer/version, fingerprint,
provenance, and creation time. Unknown, contradictory, and low-confidence
states remain explicit. Synthesis retains conflicts and cannot approve output.

Machine records are immutable. Human changes use a typed append-only
correction overlay with optimistic fingerprint preconditions. Four post-import
human gates govern structure, characters, dialogue attribution, and the
whole-book snapshot. Decisions are bound to exact evidence and are never
automatically recreated after a change or rerun.

The profile `whole-book-intelligence-v1` canonically fingerprints all agent
versions and analysis/resource limits. Same frozen inputs, profile, versions,
and protected corrections produce the same content identities and stable
ordering.

## Consequences

- The result is inspectable and useful for later production without claiming
  unsupported semantic certainty.
- Local deterministic capability remains available without credentials,
  providers, model downloads, or network transmission.
- Analysis persistence and API surface are larger and require schema-v3
  migration, pagination, scale tests, and selective approval invalidation.
- Rule-based results can be incomplete; confidence/evidence policy and human
  gates are product requirements rather than optional UI.
- Future local or explicitly authorized semantic providers may implement the
  same typed agent ports, but provider output remains untrusted and cannot
  bypass evidence, correction, or approval controls.

## Rejected alternatives

- **Generic whole-book summary:** loses exact spans, entities, uncertainty,
  human corrections, and production utility.
- **One monolithic analysis blob:** prevents pagination, selective review,
  correction overlays, provenance, and safe migration.
- **Cloud LLM requirement:** violates the offline/local-first baseline and
  would add disclosure, credential, cost, and non-determinism before they are
  authorized.
- **Automation mutates current projections:** destroys machine/human history
  and permits silent regression of editorial authority.
- **Force complete attribution/chronology:** fabricates certainty where the
  source is ambiguous.
