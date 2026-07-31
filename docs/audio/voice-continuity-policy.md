# Voice continuity policy

## Identity and authority

Every speaking character has one effective, approved `CastingAssignment` to a
versioned `VoiceProfile`. The profile records provider, voice id and version,
local-or-cloud location, non-secret settings, usage-rights status, and a
continuity key. Display names are not stable identity.

Phase 3A is the planning prerequisite for that later production invariant. It
permits explicit unresolved and intentionally-uncast roles during review and
creates no synthesis settings or audio continuity record. A Phase 3A
assignment pins project-independent profile version/fingerprint, provider/
model/catalog identity, exact rights revision/fingerprint, role, Phase 2
snapshot, casting profile, effective correction set, authority, and
supersession. Complete Cast Review can approve only the current eligible cast
evidence; its approval still does not authorize synthesis in Phase 3A.

Human casting, pronunciation, and speaker corrections are authoritative,
append-only, and locked against automation. An agent may flag a conflict or
propose an alternative; it cannot silently recast a character, reassign a line,
or replace a pronunciation.

## Continuity record

Maintain active `ContinuityRecord` entries for:

- voice identity and synthesis settings;
- character-specific delivery range, pace, intensity, and spatial treatment;
- canonical pronunciations, aliases, names, invented words, and place names;
- narrator-versus-character handling, including quoted or internal speech; and
- approved exceptions tied to specific scene or line scope.

The effective record includes its subject, value, authority, provenance,
revision, and human lock. A replacement appends a new record that supersedes
the old one. It does not edit production history.

## Synthesis and cache keys

Before synthesis, resolve the effective dialogue attribution, casting,
performance direction, pronunciation records, provider/model/voice versions,
and approved text span. Hash all of them with the exact verbatim text to form
the synthesis cache key. A cache hit is valid only when every component matches.

Provider fallback is not automatic recasting. If an approved voice is
unavailable, pause the affected work and offer retry or explicit human
recasting. Never substitute a “similar” voice and continue.

Phase 3A differentiation warnings use declared metadata only. They neither
create an acoustic continuity key nor claim that two voices sound alike. A
selected voice/profile/provider/model/rights change appends durable
invalidation evidence for the affected assignment and its dependent reviews;
catalog reversion does not silently reactivate it. An unrelated catalog change
must not invalidate an unchanged pinned assignment or its independent
role-gate approval.

Cloud synthesis requires the project's explicit transmission authorization and
provider disclosure. Credentials do not belong in profiles, manifests, logs, or
SQLite plaintext.

## Performance consistency

Consistency means recognizable identity, not mechanically identical delivery.
Performance direction may vary by scene while remaining inside the approved
character range. Normalize obvious provider-level changes first; do not flatten
intentional whispers, distance, shouting, fatigue, or emotion.

Match adjacent clips for perceived level, timbre, noise/room treatment, pace,
pronunciation, and spatial position. Review lines across scene and chapter
boundaries, not only as isolated clips. Regenerated lines must be auditioned in
context because provider updates can change a voice even when its display id is
unchanged.

## Drift detection and approval

The Continuity Agent compares current production inputs with active records and
emits warnings for provider/model drift, voice-version drift, setting changes,
pronunciation conflict, atypical pace or pitch, and unexpected speaker changes.
It reports evidence and confidence; it does not rewrite effective values.

Any voice identity/version change invalidates casting approval and dependent
performance, render, chapter, and master approvals. Material direction or
pronunciation changes invalidate the affected scope. The first-scene,
chapter, and final-master reviews include continuity listening and must have no
open continuity blocker.

If a voice must change, a human approves a new assignment, records the reason
and boundary, and chooses whether earlier material is regenerated. Mixed
old/new voices within one character require an explicit scoped exception; they
must never result from partial provider failure.
