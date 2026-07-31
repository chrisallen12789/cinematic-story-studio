# Phase 3A governed voice casting

## Product boundary

Phase 3A converts one current, fully approved Phase 2 analysis snapshot into a
stable, inspectable cast proposal and a human-reviewable cast snapshot. It
answers which production roles need voices, which catalog voices are eligible,
why each candidate passed or failed declared constraints, what metadata-based
conflicts remain, which assignments a human selected or locked, and whether
the current cast has completed all three casting reviews.

This is not a generic name-to-voice dropdown. Machine candidates, human
selections, human locks, rights evidence, conflicts, corrections, and gate
decisions remain separate, versioned records. A candidate rank never becomes
an assignment automatically.

The Phase 3A baseline is local, deterministic, provider-neutral, and
evidence-governed. It uses the repository-owned synthetic catalog. It makes no
cloud request, downloads no model, requests no credential, transmits no story
content, and generates no speech or audio.

## Required Phase 2 authority

A casting run may be created only for the current effective evidence set. The
request and persisted run bind:

- project, source-document, and source revision;
- extraction ID and revision plus extracted-text SHA-256;
- the effective Import Review decision;
- Phase 2 analysis-run ID;
- Phase 2 snapshot ID, revision, and fingerprint;
- Phase 2 correction-set and character-registry fingerprints;
- the effective `story_structure_review`,
  `character_registry_review`, `dialogue_attribution_review`, and
  `whole_book_analysis_review` decision IDs;
- voice-catalog revision and fingerprint;
- casting profile ID and fingerprint; and
- casting producer identity and version.

Every prerequisite must still be current and approved when the run is created
and when its result is published. Missing, rejected, cross-project,
wrong-revision, superseded, or fingerprint-mismatched evidence fails closed.
A changed Phase 2 snapshot, effective correction set, or character registry is
new casting input; the system does not silently apply an old cast to it.

## Product requirements

1. Derive stable narrator, character, unresolved, group, quoted-material, and
   internal-thought production-role records only from approved Phase 2
   evidence. Unresolved material receives a role only when the unresolved state
   is explicitly represented. After a run succeeds, allow a human to append a
   run-scoped, content-free custom role using a durable definition ID, complete
   typed requirement, reason, idempotency key, and exact current evidence
   preconditions.
   A named role's `characterId` is its stable effective Character Registry
   identity, not its analysis-row ID or label. Role importance is
   evidence-derived and defaults conservatively to `supporting`; missing or
   conflicting language and performance evidence remains `und`, empty, or null
   without inference.
2. Publish an immutable catalog revision containing versioned provider and
   model descriptors, project-independent voice profiles, and one rights
   record for every voice.
3. Generate a deterministic, bounded candidate set for every role. Hard
   constraints, soft preferences, unknown states, availability, rights,
   confidence, explanations, warnings, and fingerprints remain separately
   inspectable. Evaluate candidate-generation rights time at the immutable
   catalog revision `createdAt`; evaluate selection and current review against
   actual current rights, failing closed and invalidating affected selected
   evidence when those rights change or cease to be current.
4. Never interpret a score as artistic correctness, an age/accent/presentation
   label as a fact about a person, or shared metadata as acoustic similarity.
5. Keep machine proposals separate from append-only human selections and
   locks. A later assignment supersedes rather than overwrites an earlier one.
6. Preserve immutable correction overlays for selection, clearing, locking,
   unlocking, intentionally uncast status, role labels and requirements,
   restricted-rights acknowledgement, planned reuse approval, candidate
   rejection, and custom rationale. Enforce semantic supersession domains and
   one successor per correction; candidate rejection requires an exact
   selection override, and unlock must follow the exact current lock lineage.
7. Detect declared-metadata conflicts and keep their disposition reviewable.
   Planned-reuse approval applies only to reuse/similarity conflict categories,
   never rights, availability, locale, expressive-range, deprecation,
   role-length, or unresolved-assignment conflicts. Phase 3A does not calculate
   audio embeddings or listen to voices.
8. Reject final approval for unknown or prohibited rights. Restricted rights
   require an explicit, evidence-bound acknowledgement and still do not imply
   legal certainty.
9. Require current Narrator Casting Review and Character Casting Review before
   Complete Cast Review can be approved. Human approval decisions and
   system-authored invalidation decisions are append-only; the system cannot
   approve and a human cannot author `invalidated`. Reruns never reapprove
   themselves.
10. Restore the catalog revision, roles, run, candidates, conflicts,
    assignments, corrections, reviewable snapshot, and three gate histories
    after application restart.
11. Keep casting work observable through a durable job with bounded progress,
    cancellation, retry, interruption recovery, stage checkpoints,
    idempotency, typed redacted failures, and atomic result publication.
12. Preserve renderer isolation, authenticated loopback APIs, strict IPC
    validation, project scope, pagination, payload bounds, revision and
    fingerprint preconditions, and content-free logs and job events.
13. Project gate warnings only from evidence relevant to current human
    assignments in that gate's role scope. Bound blocker and warning
    projections at 32 codes each, and block approval on warning overflow rather
    than silently discarding evidence.
14. Tie each candidate conflict warning to an actual persisted conflict row,
    cap the deterministic candidate projection at 32 warnings, and fail an
    assignment mutation without partial publication if recomputation would
    exceed the 10,000-conflict run cap.

## Governed workflow

1. Complete Import Review and the four current Phase 2 analysis reviews.
2. Open Casting and inspect the prerequisite, profile, and catalog identities.
3. Start casting with every current evidence precondition.
4. Observe the durable job through role creation, constraint evaluation,
   bounded candidate generation, conflict detection, and atomic publication.
5. When the production plan needs a role not represented by Phase 2, append a
   content-free custom role to the succeeded run with its durable definition,
   full typed requirement, reason, and exact current evidence. Review the new
   immutable snapshot and newly pending gate revisions.
6. Review each role's workload and candidate explanations. Ineligible and
   restricted examples remain visible for governance review; they are not
   silently promoted.
7. Select and, when intended, lock narrator and character assignments. Record
   corrections and reasons rather than editing machine rows.
8. Review metadata-based differentiation, reuse, availability, suitability,
   and rights conflicts. Approve reuse only for one of the four reusable
   conflict categories; apply the correction or acknowledgement appropriate to
   every other category.
9. Approve Narrator Casting Review and Character Casting Review for their
   current fingerprints, then approve Complete Cast Review.
10. Reopen the project to verify that the exact evidence, assignments,
   corrections, conflict dispositions, and decisions persist.

## Casting workspace

The Casting workspace presents:

- current Phase 2 prerequisites, casting profile, catalog revision, and run;
- explicit content-free custom-role creation for the succeeded run, with a
  durable definition, full requirement, reason, and stale-evidence protection;
- narrator and character roles with dialogue-line, narration-span,
  approximate-word, chapter, and scene workload;
- paginated candidates with separate hard constraints, soft preferences,
  rights/consent, provider/model availability, confidence, and explanation;
- metadata-only conflict and differentiation warnings;
- machine proposals, human selections, locked assignments, and intentionally
  uncast roles;
- immutable correction history and the three casting reviews; and
- run, cancel, retry, and resume controls when their job states allow them.

The workspace does not load every role or voice at once. It has no playback,
audition, waveform, timeline, synthesis, download, or credential control.

## Determinism and invalidation

The same approved Phase 2 snapshot, catalog revision, profile, and correction
set produce the same machine role/candidate/conflict content and stable
ordering. Wall-clock provenance is not part of content identity.

Custom roles are not inferred from Phase 2. Within one succeeded run, the same
explicit definition ID and content produce the same role identity; an exact
idempotency replay returns the existing role, while changed material conflicts.
Appending one creates a new immutable cast snapshot and makes decisions bound
to the earlier snapshot non-current.

A Phase 2 evidence change invalidates dependent casting evidence. A selected
voice's removal, deprecation, blocked/unavailable state, descriptor change, or
rights change appends durable invalidation evidence for that exact assignment
and system-authored `invalidated` decisions for only its narrator/character
gate and the dependent Complete Cast Review. The invalidation remains effective
if the catalog later reverts; current eligible evidence must be selected and
the affected gates explicitly reapproved by a human. An unrelated catalog
change must not silently invalidate an unaffected assignment or its independent
role-gate approval. Historical runs, assignments, invalidations, corrections,
and decisions remain available; invalidation changes the effective projection
rather than rewriting history.

## Synthetic catalog

The version-1 repository fixture contains fictional identifiers only: two
providers, five models, fourteen voice profiles, and fourteen rights records.
It includes narrator and character choices plus unavailable, deprecated,
blocked, restricted-rights, prohibited-rights, unknown-rights,
provider-disabled, metadata-similarity, language, reuse, and long-form
governance cases. The fixture contains no real identity, likeness, voice
sample, biometric data, license document, model, credential, or audio file.

## Phase boundary and non-goals

Phase 3A does not implement speech synthesis, audition audio, cloning, model
downloads, cloud calls, cloud credentials, Windows Credential Manager, audio
playback, waveforms, clip rendering, pronunciation dictionaries, line-level
performance direction, music, Foley, ambience, mixing, WAV/MP3/M4B export,
installer signing, updates, releases, tags, payments, marketplaces, Phase 3B,
or Phase 4.

An approved Phase 3A cast is only an eligible governed input for a separately
authorized later phase. It is not proof of artistic quality, legal clearance,
acoustic differentiation, speech-provider availability, or production
readiness. Detailed residual limitations are in
[Phase 3A known limitations](../architecture/phase-3a-known-limitations.md).
