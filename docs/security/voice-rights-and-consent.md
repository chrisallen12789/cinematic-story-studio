# Voice rights, consent, and casting threat model

## Policy boundary

Phase 3A records declared rights evidence so an author can make a governed
casting decision. It does not give legal advice, verify a real person's
identity, obtain consent, store a license document, or guarantee that a
distribution is lawful. “Verified” means a human verified the recorded
synthetic or provider-supplied evidence under `voice-rights-policy-v1`; it is
not a legal-certainty claim.

Only the fictional repository-owned catalog is enabled. No voice sample,
biometric data, real-person identity, likeness, cloned voice, credential,
provider request, model, or license/identity document is committed or required.

## Rights record

Every voice profile has one revisioned `VoiceRightsRecord` with:

- rights-record, voice-profile, and provider identities;
- `verified|restricted|unknown|prohibited` state;
- license identifier and stated rights basis;
- commercial-use permission;
- attribution requirement;
- geographic and distribution limitations;
- cloning and consent states;
- effective and expiration dates when known;
- a non-sensitive evidence reference;
- human-verification status;
- revision and content-free provenance.

The rights record is distinct from descriptive voice metadata. A catalog
profile copies only the current projection needed for filtering; the assignment
pins the selected record's ID, revision, and evidence fingerprint. Historical
rights evidence is not overwritten.

The synthetic fixture has 14 rights records: 10 verified, two restricted, one
unknown, and one prohibited. Its evidence references are fixture identifiers,
not private documents.

## Eligibility rules

| Rights state | Candidate visibility | Selection | Final casting approval |
| --- | --- | --- | --- |
| `verified` | Eligible when all other hard constraints pass | Allowed | Allowed against the current evidence |
| `restricted` | Visible with `restricted_requires_acknowledgement` | Allowed only as a reviewable human choice | Blocked until the exact warning/rights revision is explicitly acknowledged; other restrictions may still block |
| `unknown` | Visible as `ineligible_unknown` for governance testing | Must not become an effective approved assignment | Prohibited |
| `prohibited` | Visible as `ineligible_prohibited` for governance testing | Rejected | Prohibited |

The canonical rules are `verified_eligible`,
`restricted_requires_acknowledgement`, `unknown_ineligible`, and
`prohibited_ineligible`. A soft compatibility score cannot override them.
Missing required consent, prohibited commercial use, blocked profile state, or
unknown rights is a hard failure.

`acknowledge_restricted_rights` is an append-only human correction tied to the
exact role, voice, rights record/revision, prior effective fingerprint, actor,
reason, and time. It records that a warning was reviewed; it does not alter the
rights state, remove a limitation, or assert legal clearance.

## Governed human mutations

The authenticated custom-role POST is available only after a casting run
succeeds. It accepts a durable definition ID, bounded label, complete typed
performance requirement, bounded reason, idempotency key, and exact
run/catalog/Phase 2 snapshot/correction-set/casting-profile preconditions. It
has no manuscript text, excerpt, source-range, filesystem, provider, network,
or credential field. Labels and reasons remain subject to the content-free
policy and must not be used to copy manuscript material.

The role is scoped to that succeeded run and records no Phase 2 entity or
implicit source workload. A successful append atomically publishes its bounded
candidates/conflicts/proposal, a new immutable cast snapshot, and new review
revisions. Older decisions remain auditable but are non-current. Stale
evidence, a changed idempotency replay, a conflicting definition, or a limit
violation produces no partial mutation.

Casting correction supersession is separated by semantic domain. Assignment
state, label, requirement, and restricted-rights acknowledgement lineages
cannot silently cancel one another. Selecting a rejected candidate must
explicitly supersede that exact active rejection. Unlocking must follow the
current assignment lineage and supersede the correction that created the
current lock. The transaction derives the semantic leaf, and the schema permits
only one successor per correction.

Only `incompatible_voice_reuse`, `narrator_major_character_reuse`,
`metadata_similarity_risk`, and `voice_reuse_threshold_exceeded` accept an
`approve_voice_reuse` disposition. Other conflict categories cannot use reuse
approval to bypass their rights, eligibility, availability, or suitability
control.

## Change and invalidation

A selected assignment freezes the voice-profile version/fingerprint and
rights-record ID/revision/fingerprint. If that selected voice is removed,
blocked, made unavailable or deprecated, or its applicable rights evidence or
provider/model descriptor changes, the service appends immutable
`cast_assignment_invalidations` evidence for that exact assignment. It also
appends system-authored `invalidated` decisions for only the affected
Narrator/Character Casting Review and the dependent Complete Cast Review. The
historical assignment and decisions remain immutable, while the assignment's
effective projection stays invalidated until a human explicitly selects current
eligible evidence and reapproves the affected gates.

An unrelated catalog addition or a rights change for an unselected voice does
not by itself invalidate an unaffected assignment. The service compares the
selected evidence, not only a global “catalog changed” flag, and preserves
the unaffected role-gate approval.

Invalidation is append-only and remains effective even if catalog bytes later
revert to their prior value. Reversion cannot silently restore authority.
System actors can append only `invalidated` decisions and cannot approve;
human actors can approve, request changes, or reject but cannot author
`invalidated`.

Rights time has two intentionally different evaluation points. Deterministic
candidate generation evaluates `effectiveDate` and `expiresAt` at the immutable
catalog revision's `createdAt`; that time is validated catalog evidence, so
identical frozen inputs retain identical candidate results. Selection and every
current review instead evaluate the selected record against actual current
time. A candidate that was eligible at catalog publication is therefore not
grandfathered: a not-yet-effective or expired record fails closed for selection
or review, and a rights change or loss of current applicability for selected
evidence triggers the durable affected-assignment invalidation path.

Expiration is not silently treated as verification. An expired or otherwise
inapplicable record is no longer current evidence. The user must supply or
select a current catalog revision before review can continue.

## Threats and controls

Protected assets are manuscript and project data, catalog and rights evidence,
human assignment/correction/review history, the launch token, credentials,
filesystem/process state, and application availability. The renderer, API
payloads, catalog JSON, provider/model metadata, labels, explanations, stored
historical rows, and cursor values are untrusted until validated.

| Threat | Control | Failure behavior |
| --- | --- | --- |
| Unknown/prohibited voice receives final approval | Fail-closed rights rules, hard constraints, selected-rights fingerprint, gate blockers | Decision rejected; current review remains unapproved |
| Restricted warning is bypassed | Explicit correction and gate warning-acknowledgement IDs bound to exact evidence | Stale/missing acknowledgement rejected |
| Custom role smuggles manuscript content or bypasses reviewed evidence | Content-free typed request with no source-text/range field, bounded human text, exact evidence preconditions, authenticated IPC/API, and repository policy | Request is rejected when structurally invalid or stale; policy scans prohibit committed private material |
| Unrelated correction cancels a rejection or lock | Semantic supersession domains, exact rejection override, exact unlock-lock lineage, and unique single-successor constraint | Wrong-domain, stale, or branched successor is rejected without mutation |
| Non-reuse conflict is waived as planned reuse | Closed reusable-category allow-list plus exact current conflict and role-set validation | Disposition is rejected; conflict and gate evidence remain current |
| Warning volume hides unresolved evidence | Gate-scoped relevance filtering plus a 32-warning bound with an overflow blocker | Approval fails closed when relevant warning evidence exceeds the bounded projection |
| Rights change leaves old approval effective | Selected rights ID/revision/fingerprint included in assignment, cast snapshot, and gate evidence | Affected review becomes non-current; history remains |
| Catalog claims undocumented capability | Strict versioned descriptors and closed enums; unknown remains unknown | Descriptor/catalog rejected or candidate remains unknown/ineligible |
| Metadata is presented as identity or acoustic fact | Fictional identifiers, declared-only policy, content labels, `metadataOnly=true`, `acousticSimilarityClaimed=false` | Contract/policy test fails |
| Malicious explanation leaks story text | Bounded generated rule explanations, no source input, no request bodies or rationale in logs/events/manifests | Output rejected or redacted; no publication |
| Cross-project/stale assignment | Project foreign keys plus role/run/catalog/snapshot/correction fingerprints | `404` or typed conflict without mutation |
| Renderer attempts arbitrary provider/network/credential action | Sandboxed renderer and allow-listed casting IPC; no invocation/credential API in Phase 3A | IPC validation rejects |
| Disabled cloud fixture sends data | Descriptor-only composition, no synthesis implementation, network path, credential flow, or manuscript-bearing request | No outbound operation exists |
| Private rights/identity material enters Git or CI artifact | Synthetic fixtures, ignores, repository policy scan, Gitleaks, manifest allow-list | CI fails |

## Privacy and public-repository rules

Catalog, job-event, log, diagnostic, screenshot, and build-manifest projections
use only fictional labels, opaque IDs, versions, states, counts, hashes, and
bounded content-free explanations. They exclude:

- manuscript text, excerpts, source filenames, and correction payloads that
  contain story content;
- bearer tokens, credentials, provider payloads, or personal filesystem paths;
- voice samples, biometric features, model files, generated audio, and
  embeddings;
- license or identity document contents; and
- real names, celebrity references, impersonation labels, or unauthorized
  likeness claims.

Cloud transmission remains disabled. Catalog health metadata is not consent to
send content. A future cloud or real-provider implementation requires its own
credential, disclosure, destination, retention, licensing, cancellation, cost,
and provenance threat review; the Phase 3A descriptor does not authorize it.

## Residual risk and claim limits

Rights evidence can be incomplete, outdated, jurisdiction-specific, or
incorrectly entered. Human acknowledgement can document a decision but cannot
turn a contractual restriction into permission. The application does not
monitor provider policy changes outside the selected immutable catalog
revision.

Declared age, accent, dialect, vocal presentation, and texture can encode
subjective or culturally biased labels. They are optional casting metadata,
not protected-trait inference or proof about a performer. Candidate scores do
not establish artistic suitability, authenticity, audience response, or
non-discrimination.

No acoustic analysis occurs, so metadata differentiation can miss voices that
sound similar or flag voices that do not. Later use of real performers, cloned
voices, commercial providers, or distributed audio requires separately
reviewed legal, ethical, privacy, and security controls.
