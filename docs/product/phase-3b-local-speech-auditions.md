# Phase 3B: local speech auditions and pronunciation

Phase 3B turns the approved Phase 3A cast into short, reviewable local speech previews. It is a
governance and evidence slice, not a production render system.

## User outcomes

For a project with current Import Review, four Phase 2 approvals, and three Phase 3A casting
approvals, a user can:

1. inspect the local provider/runtime and exact installed model package;
2. create project-, role-, chapter-, scene-, or custom-scoped pronunciation entries;
3. approve, reject, request changes, and supersede pronunciation evidence;
4. use the desktop's repository-owned synthetic audition script, while the authenticated service
   contract supports short narrator, character, and pronunciation-preview scripts from exact
   project-owned source spans for typed clients;
5. inspect every proposed normalization and pronunciation transformation;
6. generate bounded PCM WAV auditions through the deterministic fixture provider; the real
   Kokoro adapter is component-tested only and remains unavailable in the governed product until
   a Phase 3A voice/profile/assignment/rights binding and legal/rights review exist;
7. play an owned artifact through authenticated application IPC, with explicit play, pause, seek,
   replay, and stop controls and no autoplay;
8. observe verified cache hits, typed QC results, provenance, warnings, job progress, retry,
   cancellation, and restart recovery;
9. make append-only decisions for five exact gates; and
10. see targeted invalidation when an assignment, right, model, runtime, or applicable
    pronunciation entry changes.

## Providers

`deterministic-pcm-wav-fixture` is deterministic lifecycle evidence. It is synthetic and not
eligible for production export. `kokoro-local-onnx` is a real offline component adapter with one
bounded private command-level verification path. Installation, verification, and activation of
its exact package do not make it selectable for a governed audition: no Phase 3A voice profile,
snapshot-selected assignment, or rights record currently binds provider-internal voice
`af_heart`. The product path must reject that mismatch. Enabling it requires an explicit governed
binding plus human legal, consent, likeness, provenance, and rights review. Neither adapter uses a
cloud request, account, credential, cloning, enrollment, or real-person likeness claim.

## Governance

The user—not an analyzer, provider, or signal metric—controls:

- pronunciation decisions;
- per-role audition decisions;
- narrator and character aggregate audition decisions; and
- final voice-readiness review.

Approval is never inferred from generation, non-silence, a cache hit, or another gate. Every write
uses an expected revision and fingerprint. Existing decisions and clips remain historical after
invalidation; they cannot represent the new evidence revision.

The authority input is exactly one cast snapshot: the latest succeeded Phase 3A run's validated
immutable current manifest, only its selected assignment revisions and lock state, exact current
leaf provider/model/profile/rights records with temporally applicable rights, and the latest
eligible Narrator, Character, and Complete Cast reviews with their exact approved human decision
provenance, supersession chain, and warning acknowledgements. Evidence cannot be mixed across
snapshots. A newer current snapshot suppresses older evidence from current projections, and a new
same-snapshot Phase 3A decision tuple invalidates dependent Phase 3B authority until it is rebuilt
and explicitly reviewed.

The exact gate IDs are:

- `per_role_audition_review`
- `narrator_audition_review`
- `character_audition_review`
- `pronunciation_review`
- `voice_readiness_review`

## Phase boundary

Phase 3B deliberately excludes full-book speech rendering, queues of chapter renders, mixing,
music/ambience/foley, mastering, loudness delivery, waveform editing, timeline/DAW features,
exports, release packaging, cloud providers, credentials, voice cloning, real-person likeness
workflows, model training, and Phase 3C or Phase 4 work.

Automated audio checks establish format, bounds, integrity, and non-silence. They do not establish
intelligibility, naturalness, artistic fit, consent, commercial clearance, or production
readiness. Human listening and legal review remain explicit.

## Related architecture, recovery, and limits

- [Audition sessions, cache, and governance](../architecture/audition-sessions-cache-and-governance.md)
- [Phase 3B API pagination and payload limits](../architecture/phase-3b-api-pagination-and-payload-limits.md)
- [Failure recovery and rollback](../architecture/failure-recovery.md)
- [Database migration 0005 and verified v4 backup](../migrations/0005-phase-3b-local-speech-auditions.md)
- [Phase 3B known limitations](../architecture/phase-3b-known-limitations.md)
