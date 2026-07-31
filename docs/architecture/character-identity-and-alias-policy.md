# Character identity and alias policy

## Stable registry identity

Phase 2 assigns an opaque project-scoped registry ID to each proposed
character. The ID is independent of the display label and remains stable for a
compatible rerun of the exact same project story. Its deterministic identity
inputs are the frozen project/story scope, first evidence anchor, and
duplicate ordinal rather than the canonical or normalized display name. A human
display-label correction therefore does not change the ID. Equal normalized
names are not sufficient evidence to merge two identities, and the same alias
may remain ambiguous between multiple identities.

Each registry view exposes the immutable machine proposal, canonical and
normalized display names, aliases, honorifics, explicit pronoun evidence,
named and ambiguous mentions, first and last evidence, confidence, warnings,
and provenance. It does not infer protected or personal attributes without
direct text evidence.

## Resolution rules

Deterministic identity rules use bounded exact-name, alias, honorific, speech
tag, and nearby mention evidence. Candidate lookup is indexed by normalized
surface form; it does not compare every mention with every character. A
duplicate-name marker creates separate candidates. Conflicting or insufficient
evidence remains `ambiguous` or `unresolved`.

Aliases may change across source ranges. A display-name match alone does not
merge locations, characters, or relationship endpoints. Merge and split
recommendations remain proposals until a human correction supplies authority.

## Human authority

The correction overlay supports display-label changes, alias addition/removal,
merge, split, mention resolution, unresolved status, and locking. Corrections
are append-only and preserve prior machine proposals. Compatible reruns carry
the correction forward by stable registry/evidence identity; an incompatible
target is reported for remapping rather than silently applied.

An identity whose effective mentions are all transferred by a merge, split, or
structure correction remains in the effective registry for traceability. Its
effective mention count is zero, its first and last mention IDs are `null`, and
its first and last effective-evidence collections are empty; the immutable
machine evidence and causal human correction remain available.

The deterministic Phase 2 baseline does not automatically reconcile registry
IDs after source or extraction revisions. That cross-revision remap remains an
explicit human-controlled operation; the service fails closed instead of
guessing that similarly named proposals are the same person.
