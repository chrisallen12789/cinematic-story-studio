# Human analysis correction overlay

## Policy

Phase 2 corrections are immutable human authority layered over immutable
machine analysis. A correction never edits a machine record or the approved
extraction. Effective views combine both histories and explicitly label
`machine` and `human` authority.

## Supported corrections

The overlay supports:

- add, remove, or move chapter and scene boundaries;
- rename chapter and scene labels;
- merge or split character identities;
- add or remove an alias;
- resolve or mark a mention unresolved;
- correct or clear a dialogue speaker;
- correct or lock POV;
- correct a location or location alias;
- annotate or resolve temporal ordering;
- correct a relationship label/direction/scope;
- set a continuity disposition;
- correct an emotional-state or dramatic-intent interpretation; and
- mark an identity unresolved or lock a supported effective value.

The corrected value is a typed value specific to its target kind. Generic
JSON-pointer writes and arbitrary database field mutation are prohibited.

## Write preconditions

Each request identifies:

- project and analysis run;
- target kind, opaque ID, and target revision;
- field/action from an allow-listed union;
- expected analysis-run fingerprint;
- previous effective-value fingerprint;
- corrected typed value;
- bounded reason;
- idempotency key; and
- optional correction being superseded.

The service re-resolves project scope, the current immutable snapshot and its
revision, and all fingerprints in one transaction. It assigns and persists the
human actor as `local_user`; the client cannot select an actor or snapshot.
For an added or moved structure boundary, the client submits the frozen source
and extraction identity plus half-open Unicode-code-point offsets, but no text
digest. The service validates that selection against the approved canonical
text and derives the exact span SHA-256 in the same transaction before it
fingerprints and persists the full correction.
Stale or cross-project writes return a conflict. An idempotent replay returns
the original correction; reusing the key for another request conflicts.

## Supersession and reruns

A later human action appends a new correction that may supersede an earlier
one. Supersession does not delete the old row. Reruns carry forward a
correction only when its target/evidence compatibility key still resolves.
Otherwise the new run reports it as requiring remap; it never discards or
silently applies it to a different source range.

Merge and split corrections preserve all previous machine identity proposals,
aliases, mentions, and speaker candidates. Dialogue corrections extend the
Phase 0 speaker-correction provenance rather than replacing it.

## Approval impact

Every gate declares its exact evidence set. After a correction, the service
recomputes only affected evidence fingerprints:

- structure label changes affect structure and whole-book review; boundary
  remaps also affect character or dialogue review only when their effective
  mention, relationship, or dialogue evidence changes;
- identity/alias/mention changes affect character and whole-book review, and
  dialogue review only when effective attribution evidence changes;
- dialogue speaker changes affect dialogue and whole-book review;
- POV, location, timeline, relationship, emotion, intent, and continuity
  changes affect whole-book review unless also included in another gate's
  enumerated evidence.

Historical decisions remain append-only. Unrelated approved evidence retains
its effective decision.
