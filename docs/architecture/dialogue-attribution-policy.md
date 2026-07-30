# Dialogue attribution policy

## Exact-text boundary

Dialogue and narration are half-open Unicode code-point spans into the exact
approved extraction. Authenticated project/run-scoped entity pages may return
a bounded verbatim projection and its hash, but no list response returns the
complete manuscript. The analyzer distinguishes spoken dialogue, narration,
internal thought, quoted material, epigraph/document text, and unresolved
speech without rewriting any passage.

## Candidate evidence

Speaker candidates may use explicit speech tags, adjacent action beats,
paragraph structure, bounded turn-taking, nearby named mentions, and established
scene participants. Every candidate records its rules, evidence, confidence,
warnings, and rank. Candidate fan-out is bounded by the analysis profile.

An effective machine speaker is selected only when a deterministic rule has
sufficient non-conflicting support. Low-confidence and contradictory results
remain reviewable and may have no effective speaker. Coverage is never used as
a reason to invent attribution.

## Corrections and compatibility

A human speaker correction is an immutable overlay with an expected entity
revision, run fingerprint, previous-value fingerprint, reason, actor, and
provenance. It remains effective across restart and compatible reruns and
cannot be silently overwritten. Phase 0 speaker corrections remain preserved
as legacy provenance and are mapped only when their exact line identity is
compatible.
