# Whole-book story intelligence

## Scope

Phase 2 converts one current, approved document extraction into an immutable,
reviewable story-analysis run and snapshot. The baseline is deterministic and
local. It does not call a cloud language model, download a model, rewrite the
approved text, generate speech, cast a voice, or produce audio.

The analysis is intended to support later casting, stable character voices,
dialogue delivery, pronunciation, scene ambience, emotional performance,
pacing, continuity, and audio quality control. It is not a generic summary.
Every interpretive result remains a proposal until the applicable human gate is
approved.

## Version-1 profile

The analysis contract version is `2.0.0`; the deterministic profile ID/version
is `whole-book-intelligence-v1@1.0.0`. Its canonical profile SHA-256 is
`6ae73e83e89fbcfc0261ff339950407913cd990093fa13cdcc83ce3b1da810ec`.
The canonical values bind the eleven ordered agent versions, Unicode
code-point offsets, exact confidence thresholds, page/excerpt/text/warning
limits, deterministic declaration, and producer
`whole-book-analysis-orchestrator@1.0.0`. Python, TypeScript,
schema/tooling, and CI build evidence must reproduce the same literal and
digest.

## Input authority

An analysis run is accepted only when all of these values still identify the
current effective Import Review approval:

- project and source-document IDs;
- source revision;
- extraction ID and revision;
- exact extracted-text SHA-256;
- Import Review decision ID and evidence fingerprint;
- analysis profile ID, version, canonical JSON, and fingerprint; and
- every runtime-agent ID and version.

The service resolves and rechecks those values transactionally. Missing,
rejected, superseded, stale, or mismatched evidence fails closed. The approved
canonical text is immutable. Every source offset is a half-open Unicode
code-point interval into that exact text and carries its text hash.

## Immutable runs and snapshots

`StoryAnalysisRun` records frozen inputs, profile, lifecycle, current job,
producer versions, and failure/cancellation evidence. Each agent attempt is an
append-only `AnalysisAgentExecution`. A run publishes an `AnalysisSnapshot`
only after every mandatory stage succeeds and all entity, span, fingerprint,
ordering, and referential-integrity checks pass.

A failed, cancelled, interrupted, or stale run cannot replace the current
reviewable snapshot. A deterministic rerun creates another immutable run and
may reproduce stable entity IDs and content fingerprints; it does not erase
the earlier run. Wall-clock timestamps are provenance and are excluded from
content identity.

## Controlled pipeline

The persisted `analyze_whole_book` job executes these explicit stages:

1. validate approved extraction (`validate_approved_input`);
2. initialize analysis run (`initialize_run`);
3. structure (`analyze_structure`);
4. beats (`analyze_beats`);
5. character identity (`analyze_character_identity`);
6. dialogue attribution (`analyze_dialogue_attribution`);
7. point of view (`analyze_point_of_view`);
8. setting and locations (`analyze_locations`);
9. timeline (`analyze_timeline`);
10. relationships (`analyze_relationships`);
11. emotion and dramatic intent (`analyze_emotion_intent`);
12. continuity (`analyze_continuity`);
13. synthesis (`synthesize_analysis`);
14. publish review snapshot (`publish_analysis`).

The 11 analysis stages from `analyze_structure` through `synthesize_analysis`
map one-to-one to registered agents with stable IDs and versions.
`validate_approved_input`, `initialize_run`, and `publish_analysis` are
orchestrator stages without agent identities. The controlled pipeline records
typed inputs and outputs, bounded work, monotonic progress, cancellation
boundaries, checkpoint/output fingerprints, warnings, retry classification,
and redacted failures as applicable. The synthesis stage retains conflicting
and minority proposals. It may select an effective machine proposal only when
the rule is documented; it never converts ambiguity into certainty or replaces
human authority.

## Analysis capabilities

### Structure and beats

Imported section boundaries are retained when their extraction evidence is
reliable. Derived chapters and scenes are source-span projections. Chapter
headings, section headings, explicit scene-break markers, supported setting or
time transitions, and supported POV transitions may contribute evidence.
Punctuation alone is not a sufficient scene boundary.

Each boundary proposal records its rule, evidence, confidence, warnings, and
producer. Human add, remove, move, and relabel corrections are effective
authority and are carried into compatible reruns.

Story beats identify bounded actions, revelations, transitions, and scene
functions without replacing source text. Interpretive labels and bounded notes
are separate from verbatim evidence.

### Character identity, aliases, and mentions

Characters use stable opaque IDs independent of display names. Records include
normalized/display labels, aliases and honorifics, explicit pronoun evidence,
named and ambiguous mentions, first/last evidence, confidence, warnings, and
provenance. Matching display names do not by themselves merge identities.
Aliases can be scoped to evidence ranges or story time.

Machine merge/split output is a proposal. Human merge, split, alias,
mention-resolution, label, unresolved, and lock corrections are append-only
and protected from later automation. Protected or personal attributes are not
inferred without direct textual evidence.

### Dialogue and narration

Dialogue records preserve exact source text and offsets. Classification
distinguishes narration, direct dialogue, supported internal thought, quoted
material, epigraph/document text, and unresolved speech. Each dialogue line
retains bounded candidate speakers, candidate confidence, evidence spans,
rules, warnings, effective speaker, and effective authority.

Rules may use explicit speech tags, adjacent action beats, paragraph
structure, bounded turn-taking, nearby named mentions, and established scene
participants. No rule assigns a speaker merely to increase coverage. Human
speaker corrections remain effective, append-only authority, including
corrections migrated from Phase 0.

### Point of view

POV values are `first_person`, `third_person_limited`,
`third_person_omniscient`, `second_person`, `mixed`, `experimental`, and
`unknown`. A segment may reference an evidence-backed POV character and may
record a shift. Name frequency alone is never a POV-character rule. Human
correction and locking use the common overlay.

### Locations and setting

Locations have stable IDs, canonical labels, aliases, optional parent
locations, evidence, provenance, and scene assignments. Identical labels are
not automatically merged. Setting evidence may include environment and
time-of-day terms where explicit, but unsupported details remain unknown.

### Timeline

Narrative order and story-world order are separate. Timeline events and
temporal constraints represent explicit dates/times, relative order,
approximate order, simultaneity, flashback, flash-forward, elapsed-time claims,
unknown ordering, and contradiction. The analyzer stores constraints instead
of inventing exact dates. Human corrections can resolve or annotate a
constraint without deleting the machine proposal.

### Relationships

Relationships are directional, versioned edges with scene/chapter scope,
changes over time, evidence, confidence, warnings, and provenance. Controlled
labels are `family`, `friendship`, `romantic`, `professional`, `adversarial`,
`authority`, `dependency`, `alliance`, `unknown`, and `custom`. Co-occurrence
alone does not establish a relationship.

### Emotion and dramatic intent

Scene tone, character emotional state, change across a scene, dialogue intent,
tension, urgency, restraint, supported subtext warnings, and dramatic
function use controlled vocabularies plus bounded notes. Evidence and
interpretation are separate. The UI and API describe these as proposals, not
objective facts.

### Continuity

Continuity findings are non-destructive records. Categories include possible
duplicate character, identity or alias conflict, dialogue-speaker conflict,
chronology conflict, location or attribute conflict, unexplained object or
character-state change, POV discontinuity, uncertain scene boundary,
unresolved reference, and extraction uncertainty.

Every finding includes severity, confidence, evidence, related entities,
bounded explanation, suggested review action, status, provenance, and an
optional human disposition. Dispositions are `confirmed_issue`, `intentional`,
`false_positive`, `deferred`, `corrected`, and `unresolved`. A rerun preserves
applicable dispositions.

## Human gates

Phase 2 adds four append-only gates after the existing Import Review:

1. `story_structure_review`;
2. `character_registry_review`;
3. `dialogue_attribution_review`;
4. `whole_book_analysis_review`.

Decisions are `approved`, `rejected`, or `changes_requested`. Each pins the
project, run, snapshot revision, evidence fingerprint, actor, warning
acknowledgements, nonblank rationale, timestamp, and provenance. The latest
complete immutable human decision remains available with the effective gate
view. Changed evidence requires a new decision. A correction invalidates only
gates whose enumerated evidence fingerprint changed. A rerun never reapproves
itself.

Later casting eligibility requires all relevant current gates to be approved,
but casting and voice work remain outside Phase 2.

## Detailed governed models

- [Story-analysis data model](story-analysis-data-model.md)
- [Confidence and evidence policy](confidence-and-evidence-policy.md)
- [Character identity and alias policy](character-identity-and-alias-policy.md)
- [Dialogue attribution policy](dialogue-attribution-policy.md)
- [Point-of-view model](point-of-view-model.md)
- [Timeline and temporal-constraint model](timeline-model.md)
- [Character relationship model](relationship-model.md)
- [Emotion and dramatic-intent model](emotion-and-dramatic-intent-model.md)
- [Continuity finding model](continuity-finding-model.md)
- [Human analysis correction overlay](human-analysis-corrections.md)
- [Performance and pagination](analysis-performance-and-pagination.md)
- [Phase 2 known limitations](phase-2-known-limitations.md)

## Privacy and honesty

No manuscript text, evidence passage, source filename, prompt, database,
personal path, or correction payload enters logs, job events, CI evidence, or
general diagnostics. API evidence excerpts are bounded views returned only
through authenticated project-scoped routes. The deterministic baseline is
described as rule-based; it does not claim human-level semantic understanding.
