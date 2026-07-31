# Phase 3A verification record

## Evidence policy and current status

This record defines the Phase 3A evidence boundary. Dated run IDs, test counts,
artifact identities, hashes, process IDs, timestamps, and observed scale
measurements are recorded only after the corresponding local or GitHub Actions
gate has completed. An unexecuted check is “not yet recorded,” never an
implicit pass.

Local checks and GitHub Actions checks are separate evidence. A local build
cannot prove the exact CI artifact, and a CI count is not reported as a local
count. FFmpeg-dependent results are separately labeled because hosted and local
availability may differ. Development Electron E2E, locally packaged E2E, and
exact-CI-artifact packaged E2E are three distinct checks.

Only the repository-owned synthetic fixture family and deterministic synthetic
voice catalog may be used. Evidence may contain opaque IDs, versions, states,
counts, hashes, stable codes, runner identity, and exact owned process
identities. It must not contain manuscript excerpts, private/original source
filenames, request/response bodies, correction payloads containing story
content, bearer tokens, credentials, personal paths, databases, audio, models,
license or identity documents, provider payloads, or real-person voice data.
Fixed repository-relative labels for the public synthetic fixture are allowed.

Phase 3A evidence is not closed until the final branch head passes all required
local gates and all final-head GitHub workflows. Generated evidence manifests,
screenshots, unpacked applications, service binaries, databases, and temporary
app-data directories remain ignored workflow/local artifacts and are never
committed.

## Canonical controls under test

| Control | Exact expected value |
| --- | --- |
| Contract | `3.0.0` |
| Profile | `governed-voice-casting-v1@1.0.0` |
| Profile fingerprint | `3eaa6b4d1333b49e55707b1e9aa20606f262e1315a043bff2912a0fe77f97fa6` |
| Producer | `voice-casting-orchestrator@1.0.0` |
| Rights policy | `voice-rights-policy-v1` |
| Catalog | `synthetic-voice-catalog-v1@1.0.0`, revision `1`, semantic version `1.0.0` |
| Catalog fingerprint | `68d116d1f66e4ea4bcceabfd0520fd889cf9da3074ee1b9186c43c285575c25f` |
| Catalog counts | 2 providers; 5 models; 14 profiles; 14 rights records |
| Role/profile bounds | 300 / 5,000 |
| Custom-role authority | authenticated `POST` to one succeeded run; explicit content-free definition; exact evidence preconditions and idempotency |
| Candidate bounds | 50 per role before reduction; 12 per role after reduction |
| Page bounds | 50 default; 200 maximum |
| Explanation bound | 2,000 Unicode code points |
| Review blocker/warning bounds | 32 / 32; overflow is represented and warning overflow blocks approval |
| Voice-reuse threshold | 2 roles per profile |
| Reusable conflict categories | `incompatible_voice_reuse`, `narrator_major_character_reuse`, `metadata_similarity_risk`, `voice_reuse_threshold_exceeded` |
| Correction lineage | semantic domains; exact rejection override and unlock-lock link; one successor per correction |
| Casting gates | `narrator_casting_review`, `character_casting_review`, `complete_cast_review` |
| Windows workflow | `Phase 3A Windows CI` |
| Artifact-name prefix | `cinematic-story-studio-phase-3a-windows-unpacked-` |
| Build-manifest schema | `4.0.0` |
| Retained Phase 2 packaged result | top-level `packagedE2e`, schema `4.0.0` |
| Phase 3A packaged result | `voiceCastingContract.packagedE2e`, schema `5.0.0` |

The canonical profile, catalog JSON, TypeScript contract, Python service, tests,
desktop, and build evidence must reproduce these literals and fingerprints.

## Required locally executed checks

Record the environment and exact output for each command at the final branch
head. A focused suite repeats tests contained in a larger suite and is not
added to the larger suite's total.

| Check | Required command or evidence | Result field |
| --- | --- | --- |
| Frozen Node/Python installation | `pnpm install --frozen-lockfile` plus repository hash-locked Python installation | Record exact result |
| Lint and policy | `pnpm lint` | Record exact result and warnings |
| Type checks | `pnpm typecheck` | Record exact TypeScript/Python result |
| Full repository tests | `pnpm test` | Record schema/tooling, backend, desktop, skip, and warning counts separately |
| Focused casting/custom-role/correction/rights/migration/scale | Exact Phase 3A Pytest and tooling files used by CI | Record repeated subset count |
| Development Electron E2E | Committed development persistence test with isolated app data | Record exact result/duration |
| Build | `pnpm build` | Record exact result and diagnostics |
| Locally packaged E2E | Committed packaged test against the exact local unpacked executable | Record exact result separately |
| Repository safeguards | `pnpm scan:tracked`, `pnpm scan:staged`, `pnpm precommit` as applicable | Record file count and findings |
| Clean tree | `node scripts/git-check.mjs --clean` after published work | Record exact result |

If local FFmpeg is present, record the FFmpeg-dependent backend test as local
evidence only. If it is absent, record the skip. Do not alter the GitHub
backend count to match local availability.

## Required GitHub Actions evidence

The final-head Windows job must successfully execute:

1. credential-free checkout;
2. frozen Node and hash-locked Python installation;
3. exact parser and fixture verification;
4. lint and type checks;
5. schema/tooling, full backend, desktop, migration, casting,
   rights-governance, and scale tests;
6. development Electron E2E;
7. exact service and unpacked application build;
8. packaged E2E against that exact application and its embedded service;
9. build-evidence generation and initial validation;
10. tracked-content rescan and clean-tree verification;
11. final exact-byte manifest revalidation; and
12. version-scoped short-lived artifact upload.

The Security safeguards workflow must report the exact checksum-pinned
Gitleaks and repository policy results. The pull-request Dependency review
workflow must report its configured severity threshold and dependency-delta
scope. Frozen installation and `pip check` are dependency-integrity evidence;
they are not represented as a comprehensive live-registry vulnerability audit.

Record:

- workflow, run, job, head SHA, attempt, conclusion, and exact step results;
- hosted runner, image, Node, pnpm, and Python identities;
- schema/tooling, backend, desktop, focused Phase 3A, skip, and warning counts;
- development and packaged E2E durations;
- Security and Dependency review run/job identities and scope;
- artifact ID, byte size, digest, creation/expiry; and
- generated manifest schema/result and every assertion.

The packaged runner writes `phase-3-packaged-e2e-result.json` at the
`CSS_PHASE3_PACKAGED_E2E_RESULT_PATH` supplied by CI and writes
`phase-3-voice-casting-evidence.json` at
`CSS_PHASE3_VOICE_CASTING_EVIDENCE_PATH`. The build-evidence generator
validates both files before embedding their sanitized proof. These are
generated release/evidence files, never tracked source.

## Exact-artifact packaged E2E

The committed test must use only isolated temporary `APPDATA`,
`LOCALAPPDATA`, `TEMP`, and `TMP`, the repository-owned generated synthetic
DOCX, and the repository-owned synthetic catalog. It launches the exact
CI-produced:

`apps/desktop/release/<version>/win-unpacked/Cinematic Story Studio.exe`

The combined machine-readable result and generated build manifest must prove:

1. launch from the exact unpacked application path, with the application and
   embedded-service sizes and hashes bound by the generated manifest;
2. project creation and secure DOCX import;
3. current Import Review approval;
4. complete Phase 2 analysis, retained corrections/disposition, and all four
   Phase 2 approvals;
5. Casting workspace/catalog/profile identity;
6. narrator and character role creation;
7. succeeded nine-stage casting run;
8. narrator candidate inspection, human selection, and lock;
9. at least two character candidate inspections, selections, and locks;
10. one metadata-based differentiation/reuse conflict and its disposition;
11. a restricted or ineligible rights state, with proof that an ineligible
    voice cannot receive final approval;
12. Narrator, Character, and Complete Cast Review decisions;
13. graceful close;
14. second launch of the same exact application;
15. restored source/extraction/Import Review, Phase 2 run/snapshot,
    corrections, dispositions, and approvals;
16. restored catalog, roles, run, assignments, conflict disposition, casting
    corrections, cast snapshot, and three casting decisions; and
17. a synthetic-only screenshot and bounded machine-readable result.

For each launch, record preexisting relevant process state without reading or
terminating unrelated user processes. Ownership must be established using the
launched application identity, process creation identity, ancestry, and the
owned service handshake. Record only the Electron and
`cinematic-story-service` PIDs created by the test. After shutdown, prove those
exact PIDs are absent, with `forcedPids=[]` and `remainingOwnedPids=[]`. If
ownership cannot be established, fail safely and do not terminate by process
name. Each launch proof must also bind the Playwright Electron launcher PID
and owned Electron root PID to launcher exit code `0` and
`forceKillUsed=false`. Electron main writes a bounded shutdown sidecar beneath
the isolated `userData` directory; it must identify the exact direct-child
service PID and prove ServiceManager used stdin EOF, observed exit code `0`,
observed no signal, and did not invoke its force-kill fallback. The process
inventory sampler remains fail-closed for every descendant it observes, but
the gate does not require a short-lived parser helper to survive long enough
for a polling sample.

## Build-evidence manifest

The generated schema-`4.0.0` manifest retains the Phase 0-2 evidence fields,
including the unchanged top-level schema-`4.0.0` `packagedE2e` proof. It adds
`voiceCastingContract`, whose `packagedE2e` field is the schema-`5.0.0` Phase
3A result. The manifest envelope records:

- workflow head SHA, application version, test timestamp, and runner identity;
- relative artifact paths, byte sizes, and SHA-256 for the application,
  staged service, embedded service, retained Phase 2 packaged result, and
  screenshot;
- staged/embedded service size and hash equality;

`voiceCastingContract` records:

- Phase 2 source, extraction, snapshot, correction, and approval fingerprints;
- Phase 3A contract/profile/producer/rights-policy identities;
- provider/model identities and versions;
- catalog revision and fingerprint;
- production/narrator/character role counts;
- pre-reduction/final candidate and conflict counts;
- casting run and reviewable cast snapshot IDs/revision/fingerprints;
- narrator and character assignment evidence, including exact voice
  version/fingerprint, rights record revision/fingerprint, catalog/profile/
  Phase 2/correction-set bindings, assignment revision, and supersession;
- rights eligibility and ineligible-approval rejection;
- correction and conflict-disposition persistence;
- the three casting gate decisions;
- restart persistence; and
- exact per-launch Electron launcher/root identities, Electron exit code `0`,
  exact direct-child service PID, ServiceManager stdin-EOF exit code `0`, no
  service signal, no ServiceManager or harness force kill, no unrelated
  termination, and no remaining owned PID.

The generator validates the exact Phase 3A result and voice-casting evidence
files before embedding their sanitized contract content. Every expected Phase
0-3A assertion must be explicitly true. The manifest uses only relative
repository/artifact labels and allow-listed synthetic metadata.

## Required governance regression evidence

Before evidence closure, executable tests must prove:

- the custom-role POST rejects a non-succeeded run and every stale run,
  catalog, Phase 2 snapshot, correction-set, or casting-profile precondition;
- an exact idempotency replay returns the same stable role, while changed
  material under the same key and conflicting reuse of its definition ID fail
  without partial records;
- the role has no Phase 2 entity or manuscript source, zero workload, null story
  ranges, bounded warnings/provenance, bounded candidates/conflicts, and no
  automatic human assignment;
- successful creation appends a new immutable cast snapshot and three new
  review revisions, making prior snapshot-bound decisions non-current;
- assignment, label, requirement, and restricted-rights correction domains do
  not cross-supersede; one correction cannot gain two successors;
- selecting a rejected candidate must name that exact active rejection, and
  unlocking must follow the exact current lock correction;
- only the four reusable conflict categories accept
  `approve_voice_reuse`, and only once for the exact current role set; and
- candidate-only conflicts for unselected voices do not become gate warnings,
  while more than 32 relevant warning codes yields the overflow sentinel and a
  blocker.

## Scale and migration evidence

The focused evidence records:

- a genuine frozen schema-v3 fixture size/hash;
- v3 signature/ledger validation and verified logical-digest-equal backup;
- atomic v3-to-v4 preservation across every Phase 0-2 table;
- empty Phase 3A tables immediately after migration;
- rollback and backup retention after injected failure;
- same-version drift/future-version/downgrade rejection;
- quick/foreign-key checks and separately labeled structural
  catalog/role/candidate index/query-plan coverage;
- 300 roles, 5,000 profiles, and 50 pre-reduction assessments per role;
- no more than 12 final candidates per role;
- deterministic rerun IDs/order/fingerprints;
- conflict evaluation and pagination;
- cancellation, failed-attempt retry, interrupted-checkpoint restart, and
  atomic publication behavior; and
- observed elapsed time and peak memory, labeled as regression evidence rather
  than a universal SLA.

## Manual verification procedure

Manual verification supplements but does not replace executable E2E:

1. Start from a clean final-head checkout with no cloud credential or model.
2. Install frozen dependencies and build the unpacked Windows application.
3. Use a newly created isolated application-data root.
4. Launch normally, record application version/commit and the loopback address
   without recording the launch token.
5. Create a project, import the public synthetic DOCX, inspect and approve
   Import Review, complete Phase 2 analysis/corrections/disposition, and approve
   all four Phase 2 reviews.
6. Open Casting. Verify exact profile/catalog identities, role workload,
   paginated candidate explanations, rights states, and availability.
7. Append one content-free custom role to the succeeded run. Verify its durable
   definition, zero workload/null story ranges, new snapshot/review revisions,
   and stable exact-request idempotency replay without adding manuscript text.
8. Select and lock a narrator plus at least two characters. Inspect a
   metadata-only conflict, apply the applicable disposition, and verify
   prohibited/unknown rights cannot be finally approved.
9. Approve the narrator, character, and complete-cast reviews.
10. Close and reopen. Verify every Phase 1, Phase 2, and Phase 3A record listed
   in the packaged flow is restored.
11. Close again and verify only the exact owned Electron/service identities
    exited; do not kill by name.
12. Record observations, warnings, limitations, exact commands, hashes, and
    process evidence without copying private content or personal paths.

## Documentation coverage

| Required topic | Controlling document |
| --- | --- |
| 1. Phase 3A product requirements | [Phase 3A governed voice casting](../product/phase-3a-voice-casting.md) |
| 2. Voice-provider descriptor architecture | [Voice catalog and casting architecture](../architecture/voice-casting.md#provider-descriptor-boundary) |
| 3. Voice-model descriptor architecture | [Voice catalog and casting architecture](../architecture/voice-casting.md#model-descriptor-boundary) |
| 4. Voice-profile data model | [Voice catalog and casting architecture](../architecture/voice-casting.md#catalog-revisions-and-voice-profiles) |
| 5. Voice rights and consent policy | [Voice rights, consent, and casting threat model](../security/voice-rights-and-consent.md) |
| 6. Production-role model and explicit custom-role creation | [Voice catalog and casting architecture](../architecture/voice-casting.md#production-role-model) |
| 7. Casting profile and compatibility rules | [Voice catalog and casting architecture](../architecture/voice-casting.md#governed-casting-profile) |
| 8. Candidate explanation policy | [Voice catalog and casting architecture](../architecture/voice-casting.md#candidate-explanation-policy) |
| 9. Metadata-based cast-conflict model | [Voice catalog and casting architecture](../architecture/voice-casting.md#metadata-based-conflicts) |
| 10. Human casting corrections | [Voice catalog and casting architecture](../architecture/voice-casting.md#assignments-and-human-corrections) |
| 11. Casting approval gates | [Approval gates](../agents/approval-gates.md#phase-3a-casting-gates) |
| 12. Database-v4 migration | [Migration 0004](../migrations/0004-phase-3a-voice-casting.md) |
| 13. Persistent casting jobs | [Casting jobs, recovery, and scale](../architecture/casting-jobs-and-recovery.md#durable-job-boundary) |
| 14. Casting workspace | [Phase 3A governed voice casting](../product/phase-3a-voice-casting.md#casting-workspace) |
| 15. API pagination and limits | [Voice catalog and casting architecture](../architecture/voice-casting.md#authenticated-api-and-ipc) |
| 16. Failure recovery | [Casting jobs, recovery, and scale](../architecture/casting-jobs-and-recovery.md#failure-and-recovery-matrix) |
| 17. Scale and performance evidence | [Casting jobs, recovery, and scale](../architecture/casting-jobs-and-recovery.md#required-scale-evidence) |
| 18. Manual verification | [Manual verification procedure](#manual-verification-procedure) |
| 19. Phase 3A evidence | This verification record |
| 20. Backup and rollback | [Migration 0004 downgrade and recovery](../migrations/0004-phase-3a-voice-casting.md#downgrade-and-recovery) |
| 21. Known limitations | [Phase 3A known limitations](../architecture/phase-3a-known-limitations.md) |

## Evidence-closure fields

Before handoff, replace this section with dated, final-head observations:

- branch, head SHA, draft PR number/title/state;
- local environment and exact results/counts;
- Windows CI, Security, and Dependency review run/job IDs and results;
- exact artifact ID/size/digest/expiry;
- executable and staged/embedded service sizes/SHA-256;
- packaged result/screenshot/manifest sizes/SHA-256;
- exact manifest contents/assertion summary;
- casting run/profile/catalog/role/candidate/conflict/assignment/gate evidence;
- custom-role preconditions/idempotency/content-free projection and snapshot/review invalidation;
- semantic correction lineage, exact rejection/lock successors, reusable-category enforcement, and bounded gate-warning overflow;
- exact owned Electron/service PID creation and exit proof, including each
  Electron exit code and each ServiceManager termination method/exit
  code/signal/force-kill result;
- migration fixture/signatures and scale observations;
- repository and secret-scan file/commit counts; and
- every skip, warning, unverified behavior, and known limitation.

Until those values are populated from successful final-head executions, this
document is a verification specification and does not claim Phase 3A evidence
closure.
