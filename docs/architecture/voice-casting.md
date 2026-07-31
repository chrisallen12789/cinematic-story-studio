# Voice catalog and casting architecture

## Scope and authority

Phase 3A is the provider-independent planning boundary between approved story
intelligence and any later audition or synthesis phase. The local service owns
canonical catalog, role, run, candidate, conflict, assignment, correction,
snapshot, and review records. Electron main owns authenticated HTTP access;
preload exposes only allow-listed typed casting commands; the renderer receives
validated projections and never receives a filesystem, process, network,
credential, or generic IPC capability.

Machine output is proposal evidence. Only append-only human corrections select
or lock a voice, and only append-only human review decisions approve a current
cast snapshot. Neither the runtime job nor the highest candidate score has
approval authority.

The controlling TypeScript contract is
`packages/contracts/src/voice-casting.ts`. Service/Pydantic, persistence,
desktop, fixture, tooling, and evidence projections must agree with it.

## Canonical identities

| Item | Exact value |
| --- | --- |
| Casting contract | `3.0.0` |
| Profile | `governed-voice-casting-v1@1.0.0` |
| Profile fingerprint | `3eaa6b4d1333b49e55707b1e9aa20606f262e1315a043bff2912a0fe77f97fa6` |
| Producer | `voice-casting-orchestrator@1.0.0` |
| Rights policy | `voice-rights-policy-v1` |
| Synthetic catalog revision | `synthetic-voice-catalog-v1@1.0.0`, revision `1`, semantic version `1.0.0` |
| Synthetic catalog fingerprint | `68d116d1f66e4ea4bcceabfd0520fd889cf9da3074ee1b9186c43c285575c25f` |

Canonical JSON uses lexicographically ordered object keys and policy-defined
array order. The SHA-256 profile fingerprint binds every rule and limit below;
changing one creates a new profile identity.

## Provider descriptor boundary

`VoiceProviderDescriptor` describes catalog and possible execution
capabilities without providing an invocation interface in Phase 3A. It records:

- stable provider ID/version and
  `local|cloud_capable_disabled|development_fixture` type;
- runtime and catalog availability plus
  `healthy|degraded|unavailable|disabled` health;
- whether synthesis is implemented, network is required, and credentials are
  required;
- supported operating systems and languages;
- declared formats and sample rates;
- which licensing, commercial-use, attribution, distribution, consent,
  effective-date, and evidence-reference metadata the provider can supply; and
- content-free provenance.

It contains no credential value, endpoint secret, manuscript content, or
unbounded provider response. Catalog availability does not imply synthesis
availability.

The synthetic revision has:

| Provider/version | Type | Runtime / catalog | Network / credentials | Synthesis |
| --- | --- | --- | --- | --- |
| `synthetic-local-fixture@1.0.0` | `development_fixture` | `available` / `available` | no / no | not implemented |
| `synthetic-disabled-cloud@1.0.0` | `cloud_capable_disabled` | `disabled` / `available` | declared required / declared required | not implemented |

The local fixture declares Windows/macOS/Linux and `en|es`; the disabled
fixture declares Windows and `en`. Both report output format `unknown` and no
sample rate because no synthesis exists. The local descriptor can represent
all seven rights metadata capabilities; the disabled descriptor declares only
license identifier, commercial-use, and attribution metadata.

The disabled descriptor exists only to exercise governance. It cannot make a
network request or request a credential.

## Model descriptor boundary

`VoiceModelDescriptor` records stable model/provider identity, display name,
semantic version, supported languages/locales, expressive controls,
speaking-rate range, pitch/style control classification, declared output
metadata, local/remote execution classification, license classification,
availability, deprecation, and provenance. Undeclared capabilities remain
unknown; the application does not infer them from a label.

The synthetic revision contains:

| Model | Provider | Language/locale | Expressive controls | Execution / license / availability |
| --- | --- | --- | --- | --- |
| `synthetic-narrative-model@1.0.0` | `synthetic-local-fixture` | `en`; `en-US,en-GB` | energy, emotion, pace, style | local / `fixture_only` / available |
| `synthetic-character-model@1.0.0` | `synthetic-local-fixture` | `en`; `en-US,en-GB,en-CA` | energy, emotion, pace, pitch, style | local / `fixture_only` / available |
| `synthetic-multilingual-model@1.0.0` | `synthetic-local-fixture` | `en,es`; `en-US,es-MX` | energy, emotion, pace | local / `fixture_only` / available |
| `synthetic-unavailable-model@1.0.0` | `synthetic-local-fixture` | `en`; `en-US` | pace | local / `fixture_only` / unavailable |
| `synthetic-disabled-cloud-model@1.0.0` | `synthetic-disabled-cloud` | `en`; `en-US` | energy, pace | remote / `unknown` / disabled |

Speaking-rate bounds are declared per model. Pitch/style controls are
categorical where present, except the disabled cloud model's continuous pitch;
missing controls are `none`. Every model reports output format `unknown` and no
sample rate. These are metadata fixtures, not executable or downloaded models.

## Catalog revisions and voice profiles

A `VoiceCatalogRevision` is immutable and fingerprints the ordered provider,
model, voice-profile, and rights-record content together with its revision,
semantic version, rights-policy ID, creation time, and provenance. A catalog
publication never mutates an earlier revision.

`CastingVoiceProfile` is project-independent and identifies one catalog voice
by stable profile ID, provider ID, model ID, provider voice key, catalog
revision, display label, and profile version. Declared casting attributes are:

- language, locale, accent/dialect;
- age-presentation range and vocal presentation;
- vocal texture and pitch-range classification;
- speaking-rate and energy ranges;
- expressive range;
- narration, dialogue, long-form, and character-role suitability;
- maximum recommended words and known limitations;
- rights-record ID/state, license scope, commercial-use state, and attribution;
- cloning classification and consent status;
- metadata-similarity and reuse-risk groups;
- `active|unavailable|deprecated|blocked` state; and
- content-free provenance.

These fields are fictional declared metadata in the repository fixture. They
are not measured audio properties or facts about a real person. Celebrity,
impersonation, likeness, biometric, and cloned-voice labels are prohibited.

The synthetic revision contains 14 profiles: 11 active, one unavailable, one
deprecated, and one blocked. Its 14 corresponding rights records are described
in [voice rights and consent](../security/voice-rights-and-consent.md).

Temporal rights eligibility during deterministic candidate generation is
evaluated at the immutable catalog revision's `createdAt`, which is part of the
validated catalog evidence. Frozen inputs therefore cannot change a candidate
merely because the wall clock crossed an effective or expiration date. That
historical candidate assessment is not current authorization: selection and
current gate review evaluate the selected rights against actual current time
and fail closed when the record is not yet effective or has expired. A rights
change or loss of current applicability for selected evidence appends
assignment-invalidation evidence and invalidates only the affected role gate
plus dependent Complete Cast Review.

## Production-role model

Most roles are deterministic projections from the current approved Phase 2
snapshot. Stable role IDs do not depend on display labels. Supported types are:

- `primary_narrator`;
- `secondary_narrator`;
- `named_character`;
- `unresolved_speaker`;
- `group_or_crowd`;
- `quoted_document_or_announcement`;
- `internal_thought`; and
- `custom`.

Every role binds the project, applicable Phase 2 entity, analysis run,
snapshot ID/revision/fingerprint, effective display label, dialogue-line
count, narration-span count, approximate word count, chapter/scene range,
language and declared performance requirements, warnings, provenance, status,
and revision. Character roles also retain character identity, importance, and
whether unresolved material was explicitly represented. Narrator roles retain
primary/secondary kind.

For a named-character role, `characterId` is the stable effective Character
Registry identity from approved Phase 2 evidence. It is distinct from
`phase2EntityId`, which identifies the analysis entity row, and is never
reconstructed from a display label. `roleImportance` is copied only from an
approved `major|supporting|minor` declaration; an absent or unsupported
declaration falls back conservatively to `supporting`. Explicit unresolved
roles use `unresolved`, while custom roles have no character identity and use
the conservative `supporting` value.

Role requirements may declare language/locales, age-presentation range, vocal
presentations, preferred textures, speaking-rate range, expressive range, and
whether long-form suitability is required. Phase 2-derived requirements
project only approved Phase 2 evidence and explicit human corrections. Missing
or conflicting language evidence remains `und`, locales remain empty, and
nullable performance requirements remain null rather than being inferred from
names, manuscript text, or catalog labels. Empty preference arrays likewise
mean no approved declaration. Workload-derived long-form need is a bounded
planning rule, not inferred performance direction, biography, or artistic
intent.

### Explicit custom roles

A human may append a `custom` role only to a succeeded casting run. The
authenticated request supplies a client-owned durable definition ID, a bounded
label, a complete typed `ProductionRoleRequirement`, a nonblank reason, and an
idempotency key. It also supplies the exact current run, catalog revision and
fingerprint, Phase 2 snapshot ID/revision/fingerprint, Phase 2 correction-set
fingerprint, and casting-profile fingerprint. Any stale, cross-run, mismatched,
or over-limit request fails without mutation.

The stable role ID is derived from project, succeeded run, and definition ID.
Replaying one idempotency key with the identical request returns the same role;
reusing it with different material conflicts. Reusing a definition ID with
different role material also conflicts.

A custom role is explicitly content-free: it has no Phase 2 entity, no
manuscript source, zero dialogue/narration/word workload, null chapter/scene
ranges, and a review-required warning. Human provenance retains only the
definition ID, bounded label/requirements/reason, and fingerprints. The service
generates the same bounded candidate, conflict, and machine-proposal evidence
used for other roles, but never converts the proposal into a human selection.
Creation appends a new immutable cast snapshot and new revisions of all three
casting reviews. Earlier decisions remain historical but become non-current
because they bind the prior snapshot and evidence fingerprints.

## Governed casting profile

The profile applies these compatibility rules in canonical order:

1. `hard_constraints_fail_closed`
2. `soft_preferences_score_separately`
3. `unknown_remains_unknown`
4. `no_automatic_assignment`
5. `declared_metadata_only`

Rights eligibility rules are:

1. `verified_eligible`
2. `restricted_requires_acknowledgement`
3. `unknown_ineligible`
4. `prohibited_ineligible`

Hard constraints are:

1. `language_support`
2. `provider_available`
3. `model_available`
4. `rights_not_prohibited`
5. `rights_known`
6. `required_consent`
7. `voice_not_blocked`
8. `declared_capabilities`
9. `role_length_suitability`

Soft preferences are:

1. `locale_match`
2. `narration_suitability`
3. `dialogue_suitability`
4. `expressive_range`
5. `age_presentation`
6. `vocal_presentation`
7. `vocal_texture`
8. `speaking_rate`
9. `emotional_range`
10. `long_form_preference`

Hard results are `pass|fail|unknown`; soft results carry a separate bounded
score and explanation. Compatibility is
`compatible|compatible_with_warnings|incompatible|unknown`. A failed hard
constraint cannot be offset by soft points. Unknown evidence remains unknown.

## Candidate explanation policy

For every role/voice pair admitted to the bounded pass, the system records:

- stable role, voice, assessment, and candidate identities;
- compatibility status and normalized score;
- confidence classification and basis;
- each hard-constraint result and explanation;
- each soft-preference score and explanation;
- rights and language eligibility;
- provider/model availability and long-form suitability;
- conflict IDs and warnings;
- a bounded summary explanation;
- provenance plus input and output fingerprints; and
- deterministic pre-reduction rank and final rank.

Explanations use declared catalog and role metadata only. They contain no
manuscript excerpt, provider prompt/response, real-person inference, or
unbounded free text. They explain why a rule produced a state; they do not
claim that a voice is artistically correct, emotionally authentic, culturally
appropriate, legally cleared, or acoustically distinct.

At most 50 voices per role enter pre-reduction and at most 12 candidates per
role are published. Governance edge cases can remain reviewable without
becoming eligible or assigned. Stable status/score/voice ordering breaks ties.
Each candidate's `conflictIds` refers to persisted conflicts for that exact
role/voice evidence. `conflictWarnings` is a deterministic, at-most-32
projection of those actual rows, not a separately fabricated similarity claim;
warning overflow fails closed through the governed bounded-warning behavior.
Each warning carries at most 16 related role IDs; larger role sets retain 15
stable IDs plus the explicit `casting-related-role-overflow` marker.
Candidates, their compatibility assessments, and conflicts expose an immutable
`baseEvidenceFingerprint` for governed machine evidence and an
`outputFingerprint` over the exact current public projection. Consequently,
human rejection or conflict-disposition state changes the projection
fingerprint without rewriting its immutable machine-evidence identity.
Conflict provenance is fail-closed against the deterministic producer,
origin, input fingerprint, and recorded-time envelope reconstructed from the
persisted immutable conflict evidence.

## Metadata-based conflicts

The exact conflict categories are:

1. `incompatible_voice_reuse`
2. `narrator_major_character_reuse`
3. `metadata_similarity_risk`
4. `accent_or_locale_mismatch`
5. `insufficient_expressive_range`
6. `rights_conflict`
7. `provider_or_model_unavailable`
8. `deprecated_voice`
9. `role_length_suitability`
10. `unresolved_role_assignment`
11. `voice_reuse_threshold_exceeded`

A conflict records related roles/profiles, severity, bounded explanation,
`open|acknowledged|approved_reuse|resolved|superseded` disposition, correction
reference, provenance, and input/output fingerprints. The profile's default
reuse threshold is two roles per voice profile.

`approve_voice_reuse` is valid only for
`incompatible_voice_reuse`, `narrator_major_character_reuse`,
`metadata_similarity_risk`, and `voice_reuse_threshold_exceeded`. The request
must identify the current open conflict and its exact role set; the service
stores that set in canonical order, and a conflict can receive only one such
disposition. Locale, expressive-range,
rights, provider/model, deprecation, role-length, and unresolved-assignment
conflicts cannot be relabeled as approved reuse; they require the correction or
evidence change appropriate to that category.

Similarity means shared declared metadata or a configured differentiation
risk. `metadataOnly` is true and `acousticSimilarityClaimed` is false. Phase 3A
does not compare samples, create embeddings, or claim how voices sound.

Selection, clearing, locking, unlocking, and intentionally-uncast mutations
recompute assignment-derived conflicts against the current effective
assignments. Each assignment generation has a deterministic input fingerprint;
its conflict IDs bind that fingerprint and the exact assignment IDs. Refreshing
the same generation retains the immutable conflict details and fingerprints.
A changed assignment generation supersedes stale active rows and appends new
rows, so an earlier reuse-approval correction always retains a resolvable
conflict reference. Historical rows count toward the profile's 10,000-conflict
run cap. If the exact bounded set cannot be retained, the run records a blocking
overflow warning rather than silently truncating current review evidence.

## Assignments and human corrections

`CastAssignment` freezes role/voice/catalog/run identity, profile and Phase 2
fingerprints, the effective correction-set fingerprint, exact voice and rights
evidence revisions/fingerprints, rationale, warnings, rights state, provenance,
revision, effective state, and supersession. Authority is exactly
`machine_proposal`, `human_selection`, or `human_locked`. These histories are
separate; a machine proposal cannot overwrite a human selection or lock.
Every human assignment revision has a unique restrictive `correction_id`
reference to the exact correction that created it; machine proposals require
that field to remain null. Assignment replay therefore never infers ownership
from a role and timestamp, including when distinct corrections share a timestamp.

The exact immutable human correction categories are:

1. `select_voice`
2. `clear_assignment`
3. `lock_assignment`
4. `unlock_assignment`
5. `mark_intentionally_uncast`
6. `change_role_label`
7. `change_casting_requirement`
8. `acknowledge_restricted_rights`
9. `approve_voice_reuse`
10. `reject_candidate`
11. `record_custom_rationale`

| Category | Canonical corrected value |
| --- | --- |
| `select_voice` | `voiceProfileId` |
| `clear_assignment` | `expectedAssignmentId` |
| `lock_assignment` | `assignmentId` |
| `unlock_assignment` | `lockedAssignmentId`; the old lock remains historical |
| `mark_intentionally_uncast` | `intentionallyUncast: true` |
| `change_role_label` | bounded `effectiveDisplayLabel` |
| `change_casting_requirement` | complete typed `ProductionRoleRequirement` |
| `acknowledge_restricted_rights` | exact `rightsRecordId` and `rightsRecordRevision` |
| `approve_voice_reuse` | exact `conflictId` and ordered `approvedRoleIds` |
| `reject_candidate` | exact `candidateId` |
| `record_custom_rationale` | bounded rationale |

Each records project/run/role, prior effective and corrected-value
fingerprints, human actor, nonblank reason, time, provenance, immutable and
automation-lock flags, optional superseded correction, and idempotency
fingerprint. Unlocking is a superseding human decision, never mutation of the
old lock.

Supersession is semantic rather than “latest correction wins.” Selection,
clearing, locking, unlocking, and intentionally-uncast decisions normally
follow one assignment-state lineage. Role-label changes, requirement changes, and
restricted-rights acknowledgements each form their own per-kind lineage.
Reuse dispositions, custom rationales, and candidate rejections do not
cross-supersede those domains. Selecting a currently rejected candidate is the
special assignment-state case and sole rejection override: it must explicitly
name and supersede that exact active rejection.
Unlocking must follow the current assignment-state leaf and supersede the
`lock_assignment` that created the current lock.

The service derives the current semantic leaf inside the mutation transaction,
rejects a supplied non-null supersession ID that does not match it, and records
the derived exact ID. A database uniqueness constraint permits at most one
successor for each correction. Races and attempts to branch a lineage therefore
fail closed; unrelated corrections cannot silently cancel a candidate
rejection or lock.

Catalog, provider, model, voice-profile, or rights drift affecting a selected
assignment appends one immutable `cast_assignment_invalidations` record bound
to that exact assignment and evidence fingerprint. The assignment remains
historical but its effective projection becomes invalidated. Discovery also
appends system-authored `invalidated` decisions that supersede only the
affected narrator or character gate and the dependent Complete Cast Review;
the unrelated role gate and its approval remain current.

This invalidation is a durable latch, not a comparison that disappears when a
catalog later reverts. Restoring the earlier descriptor or rights bytes does
not reactivate the assignment or approval. A human must explicitly select
current eligible evidence and reapprove the affected gates. System actors may
author only `invalidated`; they cannot approve, request changes, or reject.
Human actors may author `approved|changes_requested|rejected`, never
`invalidated`.

## Reviewable cast snapshots

Successful publication creates an immutable `ApprovedCastSnapshot` name-space
artifact containing the exact Phase 2, catalog, profile, correction-set,
assignment, intentionally-uncast, and unresolved-conflict evidence plus counts
and fingerprint. The name means “eligible to be presented for approval,” not
“already approved.” Review state is derived only from the append-only
human review decisions and system invalidation decisions for
`narrator_casting_review`, `character_casting_review`, and
`complete_cast_review` documented in
[approval gates](../agents/approval-gates.md).

Each gate considers only roles in its scope. An open candidate-derived conflict
with voice IDs becomes a gate warning only when the current human assignment
selects one of those voices for a related in-scope role. Conflicts derived from
current human assignments, and conflicts without a voice ID, remain relevant
when their related role is in scope. Candidate-only conflicts for unselected
voices do not consume review-warning capacity.

Blocker and warning lists are each capped at 32 stable codes. Overflow is
represented by a bounded sentinel; warning overflow also inserts a blocker, so
approval fails closed instead of silently dropping unreviewed evidence.

## Authenticated API and IPC

All routes are under authenticated, loopback-only `/api/v1`; the launch token
remains in Electron main. The Phase 3A service surface is:

| Method and route | Purpose |
| --- | --- |
| `GET /projects/{projectId}/casting/catalog` | Current immutable catalog and paginated profiles with optional revision/fingerprint preconditions |
| `POST /projects/{projectId}/casting-runs` | Create an idempotent run/job against all Phase 2, catalog, and profile preconditions |
| `GET /projects/{projectId}/casting-runs` | Paginated run history |
| `GET /projects/{projectId}/casting-runs/{runId}` | One run and reviewable snapshot |
| `GET /projects/{projectId}/casting-runs/{runId}/roles` | Paginated roles |
| `POST /projects/{projectId}/casting-runs/{runId}/roles` | Append an idempotent, content-free custom role to a succeeded run using exact run/catalog/snapshot/correction/profile preconditions |
| `GET /projects/{projectId}/casting-runs/{runId}/roles/{roleId}/candidates` | Bounded candidates for one exact role revision |
| `GET /projects/{projectId}/casting-runs/{runId}/conflicts` | Paginated metadata conflicts |
| `GET /projects/{projectId}/casting-runs/{runId}/assignments` | Paginated assignment history/projection |
| `GET /projects/{projectId}/casting-runs/{runId}/corrections` | Paginated immutable correction history |
| `POST /projects/{projectId}/casting-runs/{runId}/corrections` | Append an idempotent correction with role/run/catalog/snapshot/correction fingerprints |
| `GET /projects/{projectId}/casting-runs/{runId}/reviews` | Three current review projections against an exact cast snapshot |
| `POST /projects/{projectId}/casting-runs/{runId}/reviews/{gateId}/decisions` | Append an idempotent human decision with revision/evidence preconditions |

Collection pages default to 50 and cap at 200. Candidate pages additionally
cap at the profile's 12 final candidates per role. Cursors are opaque, at most
512 characters, and bind the collection/query/revision ordering. Mutation
bodies are capped at 64 KiB; identifiers are capped at 128 characters and
idempotency keys at 160. A correction carries at most 32 corrected fields and
a 1,000-code-point reason. A run accepts at most 200 immutable corrections;
an exact idempotent replay does not consume another slot, and any new
correction beyond the bound fails without mutation. A review carries at most 32 warning
acknowledgements and a 4,000-code-point rationale. Candidate/conflict
explanations cap at 2,000 code points.

Every list rechecks run, catalog, and Phase 2 snapshot evidence; role candidates
also recheck role revision. Every correction rechecks the prior effective,
run, catalog, snapshot, role, and correction-set fingerprints. Every decision
rechecks review revision, run, cast-snapshot identity/revision, and evidence
fingerprint. Stale or cross-project evidence fails without mutation.

Custom-role creation additionally requires a succeeded run, the current
correction-set and casting-profile fingerprints, a complete requirement, and
an idempotency key. Its response returns the new role, updated run and review
projections, and the three gate IDs whose prior decisions are now non-current.

Main and preload validate exact request and response shapes, reject unknown or
oversized values, and expose no generic HTTP/IPC bridge. Casting list
responses never return manuscript text. Candidate rationale is not copied to
logs or job events.

## Storage and publication

Schema v4 stores immutable descriptors/catalogs, runs, candidates, conflicts,
assignments, assignment invalidations, corrections, snapshots, and gate
decisions in project-scoped relational tables with foreign keys, checks,
stable ordering, and supporting lookup/order indexes. Current bounded list
operations page an ordered in-service collection and bind every cursor to the
exact row-ID set; they do not claim runtime keyset SQL. The migration and
downgrade boundary are documented in
[migration 0004](../migrations/0004-phase-3a-voice-casting.md).

The voice-profile row keeps its database evidence revision separate from the
catalog's exact semantic profile version. Requirement corrections append a new
role-evidence generation of candidates and supersede affected static
conflicts; they do not delete earlier candidate or conflict rows. Current
candidate queries select only the newest generation, while historical
corrections retain dereferenceable evidence IDs.

Custom roles use the existing `production_roles` boundary with human
content-free provenance; no manuscript or general-purpose custom payload table
is introduced. Correction supersession uses a restrictive self-reference plus
a unique single-successor constraint.

One final transaction validates frozen inputs and the complete candidate/
conflict result, writes the run projections and reviewable cast snapshot, and
marks publication successful. Failure, cancellation, interruption, or stale
evidence leaves the prior effective cast snapshot unchanged.

## Related policy

- [Phase 3A product requirements](../product/phase-3a-voice-casting.md)
- [Voice rights and consent](../security/voice-rights-and-consent.md)
- [Casting jobs and recovery](casting-jobs-and-recovery.md)
- [Phase 3A known limitations](phase-3a-known-limitations.md)
- [Phase 3A verification](../evidence/phase-3a-verification.md)
