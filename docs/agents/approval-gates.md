# Approval gates

## Policy

Approval gates are durable production controls. They are not UI checkboxes.
Each decision is an immutable `ApprovalDecision` tied to an entity revision and
an evidence fingerprint. The decision log is append-only; a later human action
supersedes an earlier decision rather than mutating it.

Only a human may approve, request changes, reject, or revoke. The system may
create a pending decision. Runtime agents cannot approve their own output.
Approval never authorizes rewriting source text or silently replacing a human
correction.

## Gate sequence

| Gate | Evidence reviewed | Entry criteria | Unlocks | Automatic invalidation |
|---|---|---|---|---|
| Import review | File identity, declared/detected type, byte size, source/extracted digests, parser/version, evidence fingerprint binding the limits profile, bounded preview, structure/source mappings, warnings, preservation status | Immutable source and terminal extraction revisions exist; no blocking parser error | Story analysis for that extraction only | A new current source or extraction revision/evidence fingerprint |
| Scene segmentation review | Ordered chapter/scene spans and low-confidence warnings | Import approved; all spans resolve to preserved source | Character/dialogue analysis | Chapter, scene, beat, or source-span revision changes |
| Character review | Character names, aliases, merges/splits, evidence and corrections | Segmentation approved | Dialogue-attribution review and casting proposals | Character identity, alias, merge, or source evidence changes |
| Dialogue-attribution review | Every verbatim line, effective speaker, confidence, evidence, warnings | Character review approved; unknown speakers are visible | Casting and performance proposals | Dialogue line, character identity, attribution, or human correction changes |
| Casting approval | Voice identity/version, usage rights, sample, continuity key, provider disclosure | Character and attribution reviews approved | Voice synthesis and performance approval | Character, voice profile/version, usage rights, or assignment changes |
| Performance-direction approval | Delivery, pacing, pauses, pronunciations and continuity conflicts | Casting approved | Sound design and first-scene synthesis | Direction, casting, dialogue, or pronunciation record changes |
| Sound-design approval | Ambience, Foley, music placement/gain, rights, ducking and warnings | Performance approved; restricted/unknown rights resolved | First-scene render | Cue, asset hash/rights, placement, gain, or timeline revision changes |
| First-scene render approval | Representative rendered scene, QC findings and manifest | All production-plan gates approved; QC has no open blocker | Remaining chapter rendering | Any contributing input, provider/model version, manifest, or accepted QC finding changes |
| Chapter approval | Complete chapter render, transitions, QC and continuity | First scene approved; chapter QC has no open blocker | Next chapter/final assembly | Chapter timeline, constituent scene, asset, model, mix, or QC status changes |
| Final-master approval | Full master, manifest, chapter approvals, QC report and export profile | Every chapter approved; zero open blockers; output measured | User export | Any manifest input, chapter approval, export profile, or QC result changes |

## Phase 2 analysis gates

Phase 2 implements these exact post-import gate IDs:

| Gate ID | Evidence | Unlocks in Phase 2 | Invalidation scope |
| --- | --- | --- | --- |
| `story_structure_review` | Current snapshot chapters, scenes, beats, narration spans, POV segments, ordering, evidence, confidence, and warnings | An independently reviewable structure decision and one prerequisite for Whole-Book Analysis Review | Affected chapter, scene, beat, narration, POV, or approved-input evidence changes |
| `character_registry_review` | Current characters, aliases, honorifics, mentions, relationships, merge/split state, evidence, confidence, and warnings | An independently reviewable registry decision and one prerequisite for Whole-Book Analysis Review | Affected identity, alias, mention, relationship, or evidence changes |
| `dialogue_attribution_review` | Every current verbatim line, bounded candidates, effective speaker/authority, confidence, evidence, and unresolved warnings | Effective attribution for later performance planning | Affected line, identity, attribution, correction, or evidence changes |
| `whole_book_analysis_review` | Every current Phase 2 entity collection, warnings, and prerequisite gate identities | Marks current story intelligence review complete only after the other three current gates are approved; does not authorize Phase 2 audio/casting | Any enumerated whole-book evidence change |

The earlier architecture names `scene_segmentation_review` and
`character_review` describe the same product review concerns but are not
separate auto-approved Phase 2 decisions. Persisted Phase 2 decisions use the
exact IDs above.

Each decision includes decision and gate IDs, project and analysis-run IDs,
snapshot revision, evidence fingerprint, `local_user` human actor, acknowledged
warning IDs, nonblank rationale, timestamp, provenance, and optional superseded
decision. Supported values are `approved`, `rejected`, and
`changes_requested`.

The first three Phase 2 gates are independently reviewable. The service does
not impose a structure-to-character-to-dialogue approval sequence. It does
require all three current approvals before Whole-Book Analysis Review can be
approved.

The Import Review remains a separate Phase 1 prerequisite. The Phase 2
analyzers cannot write any gate endpoint. A correction recomputes affected gate
fingerprints only; an unrelated matching approval remains effective.

“Invalidation” is derived when current evidence no longer matches an approval's
fingerprint. The historical decision remains intact. Import or re-extraction
creates a separate pending review for the new current evidence; it never edits
the old decision. No separate invalidation-event record is claimed in Phase 1.

## Decision workflow

1. Enumerate the candidate entity IDs and revisions in a deterministic order.
2. Hash that canonical enumeration, relevant warnings, and configuration to
   obtain the evidence fingerprint stored with the decision.
3. Validate the candidate against the versioned schemas.
4. Present source-linked evidence, warnings, provider/cloud disclosure, cost,
   and downstream impact to the reviewer.
5. Append the reviewer decision with actor, rationale, time, and fingerprint.
6. Re-read the current revisions under the same transaction. If they changed,
   reject the stale decision; the current extraction's review remains
   authoritative.
7. Advance only if the effective decision is approved and no blocking warning
   or unresolved prerequisite remains.

For Import Review, the actor classification is `human` and the service-owned
actor ID is `local_user`. The explicit decision endpoint appends an immutable
human-classified record; background extraction and analysis jobs do not write
that endpoint and cannot update or erase its history. A pending or rejected
review cannot be bypassed by calling the analysis endpoint directly. Replaying
an approval idempotency key returns the same decision; a stale source or
extraction revision fails closed with a revision conflict.

An approval is scoped: approving one scene or chapter does not approve siblings.
Bulk approval is permitted only when each scope and fingerprint is enumerated
and individually inspectable.

## Corrections, supersession, and rollback

Human corrections take precedence over every automated proposal. They are
append-only records with `immutable: true` and `lockedAgainstAutomation: true`.
A changed correction invalidates affected downstream approvals. Automated
reanalysis may retain its proposal for comparison but cannot become effective
over a human value.

Rejection and change requests retain candidate output for diagnosis. Revocation
does not delete renders or provenance; it prevents them from being used as an
approved downstream input. Rollback selects an earlier approved entity revision
and creates new decisions against a new evidence fingerprint.

## Failure rules

- Missing evidence, a schema validation error, an unresolved required warning,
  unknown asset rights, or an open QC blocker fails closed.
- Provider unavailability never converts a gate to approved.
- Cancellation leaves the prior effective approval unchanged and records the
  cancelled work separately.
- Logs and decision rationale are redacted; they must not copy manuscript text,
  credentials, provider payloads, or personal paths.
