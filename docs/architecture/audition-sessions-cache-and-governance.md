# Audition sessions, cache, and governance

An audition session freezes exact current Phase 3A authority, the pronunciation dictionary,
provider/runtime/model package, normalization profile, and output profile. A stored session or
an earlier approval is not authority by itself. Before creation, generation, review projection,
and readiness calculation, the service requires the latest succeeded Phase 3A run and validates
its exact current immutable cast-snapshot manifest. Only assignment IDs and revisions selected by
that manifest, including their exact lock state, may participate. Each selected assignment must
still resolve to the same leaf voice profile, provider, model, catalog, and rights-record
ID/revision/fingerprint; availability and the rights record's temporal applicability are checked
at the time of use.

The same validation requires the latest eligible Narrator, Character, and Complete Cast review
for that one snapshot and each exact effective approved human decision, including actor and
provenance identity, supersession chain, and required warning acknowledgements. Evidence from two
cast snapshots is never combined. A newer current snapshot suppresses older-snapshot sessions and
reviews from the current workspace. A new same-snapshot Phase 3A decision tuple changes downstream
authority even when the cast bytes are unchanged, so dependent Phase 3B sessions, clips, reviews,
and readiness become non-current until rebuilt and explicitly reviewed. No catalog, assignment,
rights, or decision fallback is permitted.

Scripts reference exact project-owned extraction spans and hashes. List endpoints return bounded
metadata, never full manuscript text.

Generation is a durable 13-stage job:

1. validate Phase 3A prerequisites;
2. validate rights;
3. freeze assignment and catalog evidence;
4. resolve the model package;
5. acquire the local runtime;
6. compile pronunciation;
7. build normalization;
8. calculate the private cache key;
9. return a verified hit or synthesize;
10. validate audio;
11. atomically publish the clip;
12. publish the reviewable session;
13. release or idle the runtime.

Checkpoints contain fingerprints and IDs, not text, paths, pronunciation values, controls, model
bytes, or cache keys. Cancellation/failed/interrupted jobs publish no successful clip. Retry is
idempotent: the same job/input/artifact maps to the same clip identity.

The cache key binds project privacy scope, provider/adapter/runtime/model/package identities,
voice profile and assignment revision, normalized-text hash, effective pronunciation plan,
provider controls, output profile, and producer version. Every hit revalidates ownership,
containment, file identity, byte size/hash, and WAV metadata. Machine-QC fingerprints bind the
exact artifact/clip, policy identity, peak/RMS/silence/clipping measurements, finding-code arrays,
and their persisted counts; a count, measurement, policy, or finding drift fails closed and
invalidates dependent review authority. Corruption or partial output is a miss and never
overwrites historical evidence.

Five exact human gates govern readiness:

- `per_role_audition_review`
- `narrator_audition_review`
- `character_audition_review`
- `pronunciation_review`
- `voice_readiness_review`

Per-role evidence depends only on its role, exact snapshot-selected assignment, clip/QC,
provider/model/runtime, leaf and temporally current rights evidence, and applicable pronunciation
plan. Narrator and character gates aggregate approved current per-role evidence for the same cast
snapshot. Pronunciation review binds the current dictionary, required preview decisions, the exact
approved cast, and a deterministic aggregate of its rights evidence rather than whichever session
was generated most recently. Voice readiness binds the exact Phase 3A decision tuple, verified
model/runtime, the three downstream audition/pronunciation approvals, and absence of blocking
integrity findings for that same snapshot.

Human decisions are immutable. Automated invalidation appends evidence but cannot approve or edit
a decision. Assignment, rights, model, runtime, or applicable dictionary changes invalidate only
dependent evidence and downstream aggregates. Regeneration never reapproves. Rejection never
changes the cast; casting corrections remain a Phase 3A concern.

The complete authenticated endpoint inventory, cursor binding, request and
response caps, model-upload ceiling, and audio IPC boundary are in
[Phase 3B API pagination and payload limits](phase-3b-api-pagination-and-payload-limits.md).
