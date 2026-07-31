# Story-analysis runtime agents

## Control envelope

Phase 2 runtime agents are deterministic local application components under
the persisted runtime-agent orchestrator. They are not autonomous engineering
agents and have no filesystem, database, credential, provider, shell, or
network authority. The service supplies an immutable typed input snapshot and
scoped cancellation/checkpoint interfaces, validates the typed output, and
publishes only through the analysis-run transaction.

Every execution records:

- stable agent ID and semantic version;
- analysis-run ID, attempt, stage, and durable status;
- typed input references plus input fingerprint;
- analysis profile ID and fingerprint;
- start/end timestamps and bounded progress;
- output schema, count, and fingerprint;
- confidence summary and typed warnings;
- retry/failure classification and redacted failure;
- deterministic local-rules provenance with provider ID
  `local-deterministic-rules` and null model identity;
- known-zero USD cost; and
- provenance and cancellation/checkpoint evidence.

An execution cannot approve its own output, alter approved text, publish a
partial snapshot, erase a competing proposal, or overwrite a human correction.

## Phase 2 registry

All initial versions are `1.0.0`.

| Agent ID | Responsibility | Typed output | Review impact |
| --- | --- | --- | --- |
| `story-structure` | Chapters, scenes, boundary evidence, headings, order | Structural units, chapters, scenes, boundary warnings | Story Structure Review |
| `story-beats` | Actions, revelations, transitions, and scene function | Story beats with bounded interpretive notes | Whole-Book Analysis Review |
| `character-identity` | Identities, aliases, honorifics, mentions, merge/split proposals | Character registry, alias claims, mentions, identity conflicts | Character Registry Review |
| `dialogue-attribution` | Verbatim dialogue classification, candidates, effective machine attribution | Dialogue lines, attribution candidates/evidence, unresolved states | Dialogue Attribution Review |
| `point-of-view` | Narrator mode, supported POV character, shifts, uncertainty | POV segments | Whole-Book Analysis Review |
| `story-setting` | Stable locations, aliases, parent and scene assignments | Location entities and scene-location evidence | Whole-Book Analysis Review |
| `story-timeline` | Narrative order, story order, flashbacks/forwards, relative constraints | Timeline events and temporal constraints | Whole-Book Analysis Review |
| `character-relationships` | Directional relationship evidence and change over time | Versioned relationship edges | Whole-Book Analysis Review |
| `emotion-dramatic-intent` | Scene/character emotion, delivery intent, tension, urgency, restraint | Emotional states and dramatic intents | Whole-Book Analysis Review |
| `story-continuity` | Evidence-backed identity, attribution, chronology, location, state, POV, boundary, and extraction conflicts | Continuity findings | Whole-Book Analysis Review |
| `analysis-synthesis` | Validate and consolidate compatible outputs while preserving conflicts and uncertainty | Immutable reviewable analysis snapshot | All applicable gates |

## Stage and failure policy

The persisted job runs agents in the order declared by the Phase 2 pipeline.
An agent reads only completed, fingerprint-matching upstream outputs.
Independent work may be parallelized in a later profile, but version 1 uses a
stable order for reproducibility and simpler recovery.

Each stage checks cancellation before work, at bounded units, before
checkpointing, and before output publication. Deterministic validation,
policy, stale-input, and limit failures are not automatically retried.
Explicitly classified transient storage/resource failures may be retried
within the existing job-attempt policy. A changed input creates a new run, not
another attempt.

Checkpoints identify the run, profile, exact approved extraction, stage, agent
version, prior output fingerprints, and next bounded unit. Resume rejects any
mismatch. A cancelled, failed, or interrupted run retains its attempts and
warnings but does not become current.

The version-1 implementation durably checkpoints the validated structure-stage
artifact and the complete pre-publication analysis artifact. Stage progress
and output fingerprints remain durable for every other agent, but Phase 2 does
not claim independent partial-output resume inside each of those later stages;
they rerun after the last compatible durable artifact.

## Protected corrections

Applicable corrections are a typed immutable input to every affected agent.
The agent keeps the machine proposal, uses the human value in effective views,
and reports contradictory evidence as a warning. It may propose a remap after
source/evidence change but may not silently remap, reverse, or supersede the
human decision.

## Semantic honesty

The baseline analyzers use documented rules and bounded context. They do not
claim human-level understanding. Unknown, contradictory, or insufficient
evidence produces `unknown`, a low-confidence candidate set, or a continuity
finding. Synthesis cannot coerce a complete answer merely to satisfy coverage.
