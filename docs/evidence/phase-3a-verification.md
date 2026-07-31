# Phase 3A verification record

## Evidence policy and closure model

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

The canonical implementation artifact below was produced and exercised from
code/artifact source commit
`e46137d5b0b5761b3b0954474899d8fc00c68a3b`. The evidence-closure commit
containing this record changes only this document. Its own push and
pull-request workflows must also pass before handoff; those final-head run
identities belong in the handoff report because writing them back here would
create another untested head.

Generated evidence manifests, screenshots, unpacked applications, service
binaries, databases, and temporary app-data directories remain ignored
workflow/local artifacts and are never committed.

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

## Evidence closure

### Revision and evidence layers

| Field | Exact observation |
| --- | --- |
| Branch | `codex/phase-3a-voice-casting` |
| Base | `main` at `6ac75d9ba9b3c486e410d620888b1521c6b18105` |
| Code/artifact source commit | `e46137d5b0b5761b3b0954474899d8fc00c68a3b` |
| Draft pull request | #5, `Phase 3A: add governed voice profiles and casting`, open, draft, unmerged |
| Application version | `0.1.0` |
| Verification date | 2026-07-31 UTC |
| Canonical push Windows run/job | `30626073328` / `91141552276`, attempt 1, successful |
| Code-head PR Windows run/job | `30626076163` / `91141560705`, attempt 1, successful |
| Push Security run/job | `30626073097` / `91141531900`, successful |
| PR Security run/job | `30626076105` / `91141542196`, successful |
| Dependency Review run/job | `30626076109` / `91141541962`, successful |

The canonical artifact is the push-run artifact because its workflow head and
tested checkout both equal the code/artifact source commit and its
`pullRequestHeadSha` is correctly null. The pull-request run checked out merge
commit `fe6af9509a81ff074bfcbc542d15f5a07d2853d9` and separately bound
`pullRequestHeadSha` to the exact code/artifact source commit. Both exact
artifacts passed their own packaged E2E.

### Local final-head verification

The documentation-only closure tree was verified on Microsoft Windows
`10.0.26100` with Node.js `24.14.0`, pnpm `11.9.0`, CPython `3.14.6`, and
FFmpeg `8.1.2`. FFmpeg was present locally, so the local and hosted backend
counts are intentionally different.

| Local gate | Exact closure-tree result |
| --- | --- |
| Frozen installation | `pnpm install --frozen-lockfile` passed; the hash-locked Python environment was already exact; editable install reconstruction and `pip check` passed with no broken requirement |
| Lint and policy | `pnpm lint` passed TypeScript/Python lint plus tracked-file and diff policy checks |
| Type checks | Both TypeScript projects passed; strict mypy passed all 22 service source files |
| Full repository tests | 51 schema/tooling tests passed in 9.294 seconds; 301 backend tests passed, 0 skipped, and 1 warning in 982.08 seconds; 14 desktop files and 199 tests passed in 31.13 seconds |
| Focused Phase 2 suite | 90 passed with the same warning in 360.20 seconds; repeated subset, not additive |
| Focused Phase 3A suite | 64 passed with the same warning in 399.05 seconds; repeated subset, not additive |
| Development Electron E2E | 1 passed in 5.7 minutes, including real service restart and restoration |
| Build | `pnpm build` passed contracts, service build/smoke/staging, desktop build, and unpacked Electron packaging |
| Exact local packaged E2E | 1 passed in 8.3 minutes against `apps/desktop/release/0.1.0/win-unpacked/Cinematic Story Studio.exe`; both launches, Phase 0-3A restoration, and owned-process exit passed |
| Repository safeguards | Final `pnpm lint`, 319-file tracked scan, one-file staged scan, `pnpm precommit`, and staged diff check all passed without findings |

The full backend warning was the same upstream
`StarletteDeprecationWarning` repeated by each focused subset. No local test
was skipped. The first packaged-harness invocation failed closed before
application launch because the required evidence-path environment variables
were absent; after those variables were bound to the exact local release
paths, the committed harness passed. This configuration failure created no
application or service process and is not counted as an executed packaged
scenario.

The successful local packaged run regenerated these ignored artifacts:

| Local file | Bytes | SHA-256 |
| --- | ---: | --- |
| `release/0.1.0/win-unpacked/Cinematic Story Studio.exe` | 225,613,824 | `b6756f1d6cd5041def6d737422971205b22e366b0755373330aaca3750da4912` |
| `build-resources/service/cinematic-story-service.exe` | 28,669,659 | `27f2db80cf2416cb7acf353e434046144f8f37352344fea9fd57aa2da64ff30e` |
| `release/0.1.0/win-unpacked/resources/service/cinematic-story-service.exe` | 28,669,659 | `27f2db80cf2416cb7acf353e434046144f8f37352344fea9fd57aa2da64ff30e` |
| `release/0.1.0/packaged-e2e-result.json` | 22,940 | `05108c11f0b4ad511ef0bd7940a8fb76eadee3617304e4ffca5534a28541a290` |
| `release/0.1.0/phase-3-packaged-e2e-result.json` | 9,002 | `b4349be690365fe0acd2af19e113f1db333837c46e333092d062184523f5cd98` |
| `release/0.1.0/phase-3-voice-casting-evidence.json` | 17,208 | `8c49eb5eb718e90144a25000e8b20da69a2ae257e452312f6e4eaba33eb8998d` |
| `release/0.1.0/packaged-e2e.png` | 200,276 | `e7b2cb44de62054255bc54c3f09c3c4358be9f8e38c23388624f225e36e60a3a` |

The local staged and embedded services matched exactly. No local
cross-environment reproducibility is claimed, and the locally present
`build-evidence.json` was not regenerated by this run and is not cited.

The successful local packaged proof recorded no preexisting relevant process
for either launch. Launch 1 bound launcher/root `12048/17928` to owned
Electron PIDs `1964,5440,8016,17928,19672` and owned service PIDs
`7992,14076,21392`; Electron exited `0` without force and direct service
`21392` exited `0` by `stdin_eof`, without signal or force. Launch 2 bound
launcher/root `15912/2948` to owned Electron PIDs
`2948,16164,17480,20204` and owned service PIDs `5152,13668`; Electron
exited `0` without force and direct service `13668` exited `0` by
`stdin_eof`, without signal or force. Aggregate
`forcedPids=[]`, `remainingOwnedPids=[]`, and
`unrelatedProcessesTerminated=false`; no process was killed or adopted by
name.

### Canonical code-head GitHub Actions

The canonical push job used runner `2.336.0`, hosted-agent provisioner
`20260707.563`, Microsoft Windows Server 2025 Datacenter `10.0.26100`, image
`windows-2025-vs2026` version `20260728.188.1`, Node.js `24.18.0`, pnpm
`11.9.0`, and CPython `3.12.10`.

Every positive step succeeded: credential-free checkout; Python, pnpm, and
Node setup; frozen Node and hash-locked Python installation with `pip check`;
exact parser/catalog/fixture verification; lint; type checks; full tests;
focused Phase 2 and Phase 3A suites; development assets and mandatory Electron
E2E; service/application build; exact packaged E2E; manifest generation,
validation, and final exact-byte revalidation; tracked rescan; clean-tree
verification; and the short-lived upload. Failure diagnostics and the five
failure-enforcement steps were skipped because their failure predicates were
false.

| GitHub check | Push result | PR result |
| --- | --- | --- |
| Locked parser/catalog/fixture verification | 44 passed | 44 passed |
| Schema/tooling tests | 51 passed | 51 passed |
| Full backend | 300 passed, 1 skipped, 1 warning in 621.48 seconds | 300 passed, 1 skipped, 1 warning in 691.68 seconds |
| Desktop | 14 files, 199 passed | 14 files, 199 passed |
| Focused Phase 2 | 90 passed, 1 warning in 206.01 seconds | 90 passed, 1 warning |
| Focused Phase 3A | 64 passed, 1 warning in 214.48 seconds | 64 passed, 1 warning |
| Development Electron E2E | 1 passed in 3.7 minutes | 1 passed in 4.2 minutes |
| Exact-artifact packaged E2E | 1 passed in 4.0 minutes | 1 passed in 4.7 minutes |
| Manifest/scan/clean tree/upload | All successful | All successful |

Hosted FFmpeg was absent. Exactly
`apps/local-service/tests/test_audio_qc.py:254` skipped for that reason; the
other 300 backend tests passed. The warning was the upstream
`StarletteDeprecationWarning` for the current `httpx`/TestClient path and was
repeated by each focused subset rather than representing additional findings.

### Artifact identities and independently rehashed files

| Run | Artifact ID | Compressed bytes | Digest | Expiry |
| --- | ---: | ---: | --- | --- |
| Push `30626073328` | `8792069223` | 427,673,146 | `sha256:2bf89250017d22d5a16090c066ad1f1b6c7e84c9c4f1684de145ca8d55b89c9d` | `2026-08-07T11:39:30Z` |
| PR `30626076163` | `8792186169` | 427,667,392 | `sha256:0a5be03a053517fbac99324e56dd6b2d420534f98f19cb2a1669b4af73c6b97e` | `2026-08-07T11:44:29Z` |

Both artifact names are
`cinematic-story-studio-phase-3a-windows-unpacked-e46137d5b0b5761b3b0954474899d8fc00c68a3b`
and have seven-day retention. No failure-diagnostics artifact was created.
The canonical push artifact was downloaded outside the repository and these
exact files were independently rehashed:

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `release/0.1.0/win-unpacked/Cinematic Story Studio.exe` | 225,613,824 | `b6756f1d6cd5041def6d737422971205b22e366b0755373330aaca3750da4912` |
| `build-resources/service/cinematic-story-service.exe` | 27,315,238 | `64a3b91f421f931db7e50832df4d0064ec7aaae7fb791ff2ce82a241093245bf` |
| `release/0.1.0/win-unpacked/resources/service/cinematic-story-service.exe` | 27,315,238 | `64a3b91f421f931db7e50832df4d0064ec7aaae7fb791ff2ce82a241093245bf` |
| `release/0.1.0/packaged-e2e-result.json` | 22,912 | `cb3d417ec7b8c30c6412f15735532a742f71d9bcf04f08df9958fd310c8c87e7` |
| `release/0.1.0/phase-3-packaged-e2e-result.json` | 8,989 | `f97133931badd69b95efc380dd4932efcaf4f157dc519e3c28cf78691a903dc2` |
| `release/0.1.0/phase-3-voice-casting-evidence.json` | 17,195 | `098d653ca610dba0601f6e0cc335bb9ed3b2ab77ad7e457909919573d4b5067e` |
| `release/0.1.0/packaged-e2e.png` | 157,432 | `826de05fac636712c85c8c6ca731be8ef223d1c7d7cbdfb5ffb4a0ed8a2bf712` |
| `release/0.1.0/build-evidence.json` | 53,193 | `89870c04d8d4bd2d9dbaec3a94dd198623615e51444242cd01871968c0131506` |

The concurrently produced PR service was 27,312,706 bytes with SHA-256
`03562cb2f0563d3cc7d2c93b5defd2e0119c7ba1d8e28e1bd37b113e63451d3f`.
Its staged and embedded copies matched exactly and that exact embedded service
passed the PR packaged E2E. The differing push and PR service bytes are
PyInstaller run/environment nondeterminism; no cross-run byte reproducibility
is claimed.

### Canonical manifest and casting proof

The canonical manifest is schema `4.0.0`, uses repository-root-relative paths,
records application version `0.1.0`, timestamp
`2026-07-31T11:39:24.758Z`, and runner `GitHub Actions 1000001530`, Windows
X64, GitHub-hosted, run `30626073328`, attempt 1, job
`verify-and-build`. Its retained top-level Phase 2 packaged proof is schema
`4.0.0`; `voiceCastingContract.packagedE2e` is schema `5.0.0` under casting
contract `3.0.0`.

All 14 top-level manifest assertions were true:
`stagedServiceMatchesEmbeddedService`,
`packagedE2eHarnessResultMatchesStepOutcome`,
`packagedE2eOwnershipExitProven`, `phase1DocxImportReviewProven`,
`phase2ProfileAndAgentsProven`, `phase2ApprovedInputProven`,
`phase2RunSnapshotAndStagesProven`, `phase2StoryAssertionsProven`,
`phase2CorrectionsProven`, `phase2FourGateDecisionsProven`,
`phase2DecisionRecordsPersisted`, `phase2RestartDurabilityProven`,
`phase2WholeBookAnalysisProven`, and `packagedE2eEvidenceComplete`.

All 11 Phase 3A assertions were also true:
`phase2PrerequisitesCurrent`, `castingProfilePinned`,
`catalogFingerprintVerified`, `rolesCreated`,
`boundedCandidatesCreated`, `metadataConflictProven`,
`rightsGovernanceProven`, `humanAssignmentsLocked`,
`threeGateDecisionsPersisted`, `restartPersistenceProven`, and
`processOwnershipExitProven`.

| Casting evidence | Canonical value |
| --- | --- |
| Phase 2 run/snapshot | `80e15f85-b025-44f2-ac24-de3c291cd4e5`; `0dc2b24f-c76a-4b6d-ae9c-03210be2f5ca` revision 1 |
| Phase 2 snapshot fingerprint | `4466789efda5fcc7278b2e34086436b3694ed6196f9da203400bba7758b0fbeb` |
| Phase 2 correction-set fingerprint | `67c2182c9209984296cd9c2412dc606b18b2cc4a62c8a1f1350c15bb48b043f7` |
| Casting run | `99732ac3-574e-4f63-80aa-614741128e59` |
| Reviewable cast snapshot | `c354db98-5fc6-44a0-90bf-09c52869654f`, revision 22, fingerprint `5fa845da0723b096d786b9d48b6b8bbdaad150db4b34c44b554edd875b02acc7` |
| Counts | 7 roles; 1 narrator; 6 characters; 98 pre-reduction; 84 final candidates; 36 active conflicts; 4 assignments; 21 corrections |
| Narrator assignment | `b6a78226-7444-4703-8087-47ce49933e86`, `synthetic-narrator-02`, human-locked, restricted rights acknowledged |
| Character assignments | `d47b9fd3-ce68-4256-ae26-9ef40abc8a54`, `5b1a432d-5912-49ef-95af-d54b3dd71534`, `e38db189-c96f-4ee5-8d1b-7a4a0f9fa22b`, all human-locked |
| Conflict/disposition | `6e37a649-e33c-5bd8-82c0-b8a4115b949b` / `51d12ba7-e796-4b2e-9638-76485fa24d61` |
| Casting decisions | `7904610b-2310-4adf-819e-cfdfae0d92ce`, `9e5e6542-0529-4c8f-b0e9-1a93e74c0dc2`, `58412ce8-38bd-4e20-bb27-b72e7262a663` |
| Rights result | `eligible_after_required_acknowledgements`; ineligible final approval was rejected |
| Restart | corrections, conflict disposition, assignments, snapshot, and all three decisions restored |

The exact Phase 3A flow was: create project; import the synthetic DOCX; wait
for extraction; approve Import Review; complete Phase 2 and verify four
approvals; open Casting; load the synthetic catalog; create roles; run
casting; inspect, select, and lock narrator and character voices; surface and
disposition a metadata-only conflict; surface restricted/ineligible rights;
reject ineligible final approval; approve all three casting gates; close and
prove owned-process exit; restart the same application; restore Phase 0-3A
evidence; close; and prove final owned-process exit.

### Canonical owned-process termination

Both launch inventories recorded no preexisting relevant process. Ownership
was established only from the exact executable identity, creation identity,
ancestry rooted at the test launcher, and the owned service handshake.

| Launch | Launcher/root | Owned app PIDs | Owned service PIDs | Direct shutdown proof |
| --- | --- | --- | --- | --- |
| 1 | 4352 / 8828 | 1820, 2128, 4988, 8092, 8828 | 6560, 7000, 7948 | Electron exit 0, no force; service 7948, `stdin_eof`, exit 0, no signal, no force |
| 2 | 1484 / 4976 | 1932, 3588, 4976, 6236 | 2640, 8572 | Electron exit 0, no force; service 2640, `stdin_eof`, exit 0, no signal, no force |

The aggregate proof recorded Electron PIDs
`[1820,1932,2128,3588,4976,4988,6236,8092,8828]`, service PIDs
`[2640,6560,7000,7948,8572]`, `forcedPids=[]`,
`remainingOwnedPids=[]`, and `unrelatedProcessesTerminated=false`. No process
was killed or adopted by name.

### Governance, migration, and scale regressions

Executable governance tests passed the custom-role succeeded-run and exact
fingerprint preconditions, content-free/zero-workload projection, exact
idempotent replay, changed-material rejection, immutable snapshot/review
append, semantic correction domains, one-successor constraint, exact
rejection and unlock successors, four-category reuse disposition restriction,
candidate-only warning exclusion, and fail-closed 32-warning overflow.

The frozen v3 SQL fixture is 41,242 bytes with SHA-256
`13bc84c31745933f14a4b128e502d78d6ad31cd6657b1a4d57fa7fb2cdd359b6`.
Migration tests passed exact v3 signature/ledger validation, verified
logical-digest-equal backup, atomic v4 publication, complete Phase 0-2
preservation, empty initial Phase 3A tables, injected rollback with backup
retention, drift/future/downgrade rejection, integrity checks, and controlling
index/query-plan checks.

The frozen v4 fingerprints are: fresh table metadata
`b6614c36b0eca6e77d8de0c5c120ac5bb05cc054496a52f8c5ea68f64faf0f3b`;
migrated table metadata
`0e4c66c6fe855e1eedc7d2e1ef687e2e49c9d0c505c58a0e66fff34e5389d122`;
indexes
`fa5c2efb6de3c7c7c6f22dc99572e8e8318663499c22ba6ed294975286460360`;
fresh SQL
`f633a359fb0e1068f6638d3856e04764a8749c64b29d956e73af28fd97f1750d`;
and migrated SQL
`5eea446d0c1aefce883468880dd647494cd3ffe5292b713b02375632a907048a`.

The dedicated maximum-scale candidate-reduction invocation passed in 10.13
seconds. An external descendant-only sampler observed 14.106 seconds of wall
time and a peak owned-process-tree working set of 205,303,808 bytes
(195.793 MiB) across the exact owned Python launcher, console host, and Python
worker PIDs `19212,13872,4032`. The sampler neither inspected nor terminated
unrelated process trees.

The executable bound remains 300 roles, 5,000 profiles, 15,000 pre-reduction
assessments, no more than 3,600 published candidates, deterministic rerun
fingerprints, conflict evaluation, indexed pagination, cancellation, retry,
checkpoint recovery, and atomic publication. The time and owned-tree working
set are workstation observations for regression evidence, not universal
latency or memory SLAs.

### Security, dependency scope, warnings, and limitations

Both code-head Security jobs scanned 319 tracked files and 43 commits
(approximately 5.79 MB) with checksum-pinned Gitleaks `8.30.1` archive SHA-256
`551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb`.
Repository policy and full-history secret scanning passed with no leak. The
exact historical synthetic-test fingerprints in `.gitleaksignore` are narrow
audited suppressions; unrelated findings still fail.

Dependency Review used `fail-on-severity: moderate`, found no
moderate-or-higher vulnerable dependency, no denied package, and no dependency
change. This is pull-request delta evidence. Frozen pnpm installation,
hash-locked Python installation, and `pip check` are integrity evidence. Phase
3A does not add or claim comprehensive `pnpm audit` or `pip-audit` CI coverage.

Non-failing hosted notices remain: the single repeated Starlette/TestClient
deprecation; Node `DEP0190` and pnpm-layout messages from the pnpm action
bootstrap; that action's own transient one-tool-package npm audit message;
electron-builder's future implicit-publish behavior notice, missing package
author, and package-manager detection fallback. The successful job's failure
diagnostics and five failure-enforcement controls skipped by design. Earlier
Security failures and Windows cancellations were superseded by the exact-head
fixes and successful runs above.

The verification does not relax the documented limitations: the catalog is
synthetic descriptor metadata; there is no executable provider, synthesis,
audio, cloud, model-download, credential, audition, or export path; metadata
scores do not establish artistic correctness or acoustic similarity; rights
records and acknowledgement do not provide legal certainty; role inference
and workload are bounded approximations; invalidated assignments require
human remapping; checkpoint resume is staged rather than arbitrary per-role;
bounded list paging is not claimed as runtime keyset SQL; the project database
is not application-encrypted; v4 has no in-place downgrade; and the unpacked
artifact is unsigned verification evidence, not a release.

The executable evidence maps VS-301 through VS-326 to the service,
API/tooling, desktop, migration, governance, and scale suites; VS-327 to
development Electron E2E; VS-328 to exact-artifact packaged E2E; VS-329 to
owned-process termination; VS-330 to manifest/artifact/security proof; and
VS-331 to the complete Phase 0-2 regression gate.
