# Phase 3B verification record

## Evidence policy and current status

This is the in-progress Phase 3B evidence record. It records completed focused
local checks and deterministic source/fixture facts. A fresh private ignored
real-provider run, final aggregate local gates, the draft pull-request head,
GitHub Actions runs, and the exact hosted artifact have not yet completed and
are explicitly marked pending. A pending field is not an implicit pass. Prior
private-provider run values are not carried forward when the retained JSON no
longer matches the recorded size/hash/process tuple.

Local, hosted-CI, and artifact evidence are separate:

- a local result does not attest to a GitHub-produced application or service;
- the deterministic fixture provider proves lifecycle, cache, governance, and
  process behavior, not natural speech;
- the private real-provider command is classified
  `component_only_unbound_real_provider_verification`: a fresh closure run is
  pending, and even a pass can prove only one bounded local component synthesis
  and signal-integrity result, not a governed Phase 3A voice profile, cast
  assignment, rights-record binding, exact CI artifact, or production-cleared
  voice;
- development Electron E2E, locally packaged E2E, and exact-CI-artifact
  packaged E2E are three different checks; and
- FFmpeg- and frozen-service-dependent checks are reported separately from
  checks that run in every environment.

Only repository-owned synthetic input may be used. Public evidence may contain
opaque IDs, versions, counts, hashes, typed states, runner identity, and exact
owned-process identities. It must not contain manuscript excerpts, raw audition
scripts, pronunciation values, cache keys, personal or absolute paths, tokens,
credentials, databases, models, audio bytes, private license documents, or
real-person voice data. Generated manifests, screenshots, application/service
binaries, model packages, and audition audio remain ignored local or short-lived
workflow artifacts and are never committed.

## Revision under verification

| Field | Exact current value |
| --- | --- |
| Branch | `codex/phase-3b-local-speech-auditions` |
| Verified base | `main` at merge commit `e32f315adf2f612d4ac967a732e74947f96b8238` |
| Starting branch point | `e32f315adf2f612d4ac967a732e74947f96b8238`; this is the verified base, not a Phase 3B implementation commit |
| Phase 3B code/artifact source commit | Pending focused commits and push |
| Draft pull request | Pending; required title is `Phase 3B: add managed local speech auditions and pronunciation` |
| Application version | `0.1.0` |
| Database schema | Version 5, direct upgrade from the exact issued version 4 |
| Verification date | 2026-07-31 UTC |
| Canonical Windows CI run/job | Pending |
| Pull-request Security safeguards run/job | Pending |
| Pull-request Dependency review run/job | Pending |
| Artifact ID/size/digest/expiry | Pending |

The Phase 3B pull request must remain draft, open, and unmerged. Phase 3C,
Phase 4, full-book rendering, mixing, mastering, export, release, signing, and
automatic update work are outside this evidence boundary.

## Canonical controls under test

| Control | Exact value |
| --- | --- |
| Speech-audition contract | `1.0.0` |
| Producer | `local-speech-audition-orchestrator@1.0.0` |
| Worker protocol | `1.0.0` |
| Audition integrity profile | `1.0.0`; fingerprint `5ef6e9420ae049643e93faaa540c2834ef81258b1967fb3f06dc0b5e80ec0bab` |
| Pronunciation / normalization profiles | `1.0.0` / `1.0.0` |
| Fixture provider | `deterministic-pcm-wav-fixture@1.0.0`; adapter `1.0.0`; deterministic, local, fixture-only, export-ineligible |
| Fixture model/package | `deterministic-square-wave@1.0.0` / `deterministic-pcm-wav-fixture-package`; manifest fingerprint `e0352282af67ff3675fe6067a63feca5d9d4fcaeef5a3f12b80a5e4d2c9635d6` |
| Fixture runtime profile | Current `deterministic-pcm-wav-fixture-windows-v1-0-1@1.0.1`; record `793f36e7-e118-52c4-b605-6f60f23e1656`; fingerprint `f3e5801f836ab4061eb76e52f4a3e1b4c7ba162238e0c70857e74fff705f75d6`; 30,000 ms startup deadline |
| Real local provider component | `kokoro-local-onnx@1.0.0`; adapter `1.0.0`; local, non-deterministic, restricted, export-ineligible; only the unbound component command can verify it, and the fresh closure run is pending |
| Real component runtime | `onnxruntime-cpu@1.28.0`; current profile `kokoro-local-onnx-windows-v1-0-1@1.0.1`; record `190de0bf-aa0d-5620-97c6-584d2997c3dc`; fingerprint `1736106e267f8e4ed695e71bb28b39477f658bf4cdfe4b4da716c471f82c80ce`; 30,000 ms startup deadline |
| Real G2P | `kokorog2p==0.6.7`, dictionary-only English path; remote, eSpeak, spaCy, Goruut, and implicit fallbacks disabled |
| Real model package | `kokoro-82m-v1.0-onnx-q8-af-heart@1.0.0+1939ad2a8e416c0acfeecc08a694d14ef25f2231`; 92,887,010 expanded bytes; manifest fingerprint `03702762c09a71ee54b7ea3bfa4939d1c622b01d68709e2180a39ca62ec264b0` |
| Governed Kokoro product binding | None. No governed Phase 3A voice-profile ID/version, cast-assignment ID/revision, or rights-record ID/revision maps to provider voice `af_heart`; the product path must reject this incompatibility rather than substitute silently. |
| Audio format | RIFF/WAVE, signed little-endian PCM16, 24 kHz, mono; 100 ms minimum, 30 s and 24 MiB maximum |
| Generation job | `generate_audition`; 13 ordered stages; hash-/ID-only checkpoint schema 1 |
| Human gates | `per_role_audition_review`, `narrator_audition_review`, `character_audition_review`, `pronunciation_review`, `voice_readiness_review` |
| Page and scale bounds | 50 default / 200 maximum page; 300 roles; 1,000 pronunciation entries; 2,000 audition metadata records; 10,000 cache records |
| Build manifest / Phase 3B result | Schema `6.0.0` / schema `7.0.0` |
| Hosted Phase 3B evidence classification | `deterministic_fixture_lifecycle_only` |

Runtime-profile evolution is append-only. If an upgraded schema-v5 database already contains
the issued fixture or Kokoro `1.0.0` row, startup validates that exact row with its original
10,000 ms deadline and fingerprint (`2d52bca32766fac6d2744cf877cb4f5d927f59af369706a3eb2741e330d00cc4`
for the fixture and `007101c57a95e7d6cde66747cd96e3413d672a84d3acca49b28c5e4f36593ef4`
for Kokoro) and never rewrites it. New requests and runtime health bind only to the exact current
`1.0.1` record; legacy rows remain exposed solely so historical evidence stays resolvable.

The fixture product provider and the real-provider component share the same
bounded, typed adapter request boundary. Neither accepts a URL, shell command,
arbitrary executable, unmanaged output path, raw SSML, or unbounded text. The
real component is not product-selectable without an exact governed binding;
the product path fails closed instead of silently substituting a provider,
model, voice, runtime, or package.

## Locally executed checks

The current workstation tools are Node.js `24.14.0`, pnpm `11.9.0`, and the
service environment's CPython `3.14.6`. CPython `3.12.10` is separately
installed and completed the supported lock-reproduction/install check. The current
`apps/local-service/requirements.lock` is 61,104 bytes with SHA-256
`8e4a886fa63e86e4a1f8512a9b448a01fbaa16efa8f7f2eddd471893c4ddccc0`.
The CI-equivalent compile over the existing committed lock reproduced those
exact bytes at `2026-08-01T10:23:04.717Z`; a fresh `--require-hashes` install,
editable `--no-build-isolation --no-deps` service install, and `pip check`
completed at `2026-08-01T10:26:35.009Z`. A blank-output live-index compile is
not byte-identical because the index now exposes 128 additional hashes for the
same pinned `lxml==6.1.1`; the workflow's intentional `pip-tools` hash-reuse
check verifies the committed dependency resolution and hash set, not an
immutable package-index snapshot.

These are completed focused checks, not an additive full-suite total. Some
tests are repeated across focused files.

| Local check | Exact current result |
| --- | --- |
| Provider and managed-runtime aggregate | 27 passed, 2 skipped, 1 warning in 53.41 seconds |
| Real-provider product-path refusal without a governed voice binding | 1 passed, 0 skipped, 1 warning in 30.15 seconds |
| Phase 3B repository and authenticated API focus | 11 passed, 1 symlink-permission skip, 1 warning in 12.59 seconds |
| Review-integrity regression | 1 passed, 0 skipped, 1 warning in 53.52 seconds |
| Invalidation-reconciliation regression | 1 passed, 0 skipped, 1 warning in 71.56 seconds |
| Full Phase 3B workflow file | 3 passed, 0 skipped, 1 warning in 166.99 seconds |
| Atomic startup-reconciliation case | 1 passed, 0 skipped, 1 warning in 1.19 seconds |
| Atomic cancellation/publication-boundary case | 1 passed, 0 skipped, 1 warning in 59.03 seconds |
| Phase 3B scale file | 1 passed, 0 skipped, 1 warning in 356.97 seconds of Pytest-reported elapsed time; 367.645 seconds outer command wall time |
| Python lint/type focus | Ruff formatting/lint passed on the exercised Phase 3B modules; configured strict mypy passed all 32 service source files |
| Current documentation policy/link checks | Pending rerun after evidence closure edits |

The warning in these Pytest invocations is the same upstream
`StarletteDeprecationWarning` for the Starlette TestClient/current `httpx`
compatibility path; a focused rerun repeating it is not an additional finding.
The two provider/runtime skips require a completed PyInstaller build and an
exact `CINEMATIC_STORY_TEST_FROZEN_SERVICE` path. They cover frozen-archive
license/metadata inventory and dispatch through the exact frozen product
worker. The repository/API skip is the explicit Windows account symlink-
permission case; link/reparse rejection remains covered by the executable
cases the account can create. These skips must be rerun or remain explicitly
reported after the build.

The workflow regressions exercised current Phase 0-3A prerequisites, corrected
Phase 2 evidence reconstruction, scoped pronunciation, durable generation,
verified cache reuse, two distinct provider-request attempt records across
retry, cancellation, restart, append-only review decisions, targeted
invalidation, cache clearing, and stale-review rejection. The atomic checks
exercise owned staging cleanup and publication boundaries; the complete atomic
file and final aggregate suite remain required.

### Final local gates still pending

| Gate | Status |
| --- | --- |
| CPython 3.12.10 lock reproduction and frozen/hash-locked install | Passed: exact 61,104-byte/SHA-256 reproduction, fresh hash-enforced install, editable no-dependency install, and `pip check` (`No broken requirements found`) |
| `pnpm install --frozen-lockfile` | Pending final-tree record |
| `pnpm lint` | Pending final-tree record |
| `pnpm typecheck` | Pending final-tree record |
| `pnpm test` with exact repository/tooling/backend/desktop totals | Pending |
| Complete focused Phase 2, Phase 3A, and Phase 3B CI-equivalent subsets | Pending final-tree aggregate record |
| Development Electron E2E | Pending |
| `pnpm build` | Pending |
| Frozen-service runtime checks against the exact local build | Pending |
| Locally packaged Electron E2E | Pending |
| Tracked/staged/private-content and clean-tree gates | Pending final commit |

The FFmpeg executable capability test must be reported separately at closure.
Its local availability does not change a hosted backend count if the hosted
runner skips it.

## Private real-provider functional verification

The real-provider evidence is private local, component-only evidence, not a
committed fixture, hosted-CI evidence, or product-path audition. Its exact
classification is `component_only_unbound_real_provider_verification`. Both
the exact model package and generated WAV remain under ignored local storage;
only placeholder files in those roots may be tracked. The currently retained
command JSON does not match the previously recorded JSON size/hash and process
tuple, so no retained run is designated as the closure run. A fresh invocation
and exact capture are pending; the stale run-specific numbers have deliberately
been removed rather than presented as current evidence.

The fresh rerun must use the committed bounded component-verification command:

```text
pnpm service:python apps/local-service/scripts/verify_real_speech_provider.py --acknowledge-restricted-local-use
```

The command accepts no text, model URL, model path, or output path. It verifies
the canonical manifest against the fixed ignored model-package directory,
constructs the component verification's managed-worker boundary directly,
synthesizes its fixed repository-owned synthetic phrase, validates and
atomically publishes the returned WAV under an exact command-owned ignored run
directory, stops the worker, and records the exact exit evidence. It does not
create or consume a governed Phase 3A voice profile, cast assignment, or rights
record and does not enter the product audition workflow. The phrase itself and
pronunciation value are not copied into this public record.

### Pinned model-package manifest

The selected conversion is
[`onnx-community/Kokoro-82M-v1.0-ONNX`](https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX)
at immutable revision `1939ad2a8e416c0acfeecc08a694d14ef25f2231`.
It is classified `maintainer_referenced_conversion`, not maintainer-authored;
the official upstream is
[`hexgrad/Kokoro-82M`](https://huggingface.co/hexgrad/Kokoro-82M).

| Manifest file | Bytes | SHA-256 |
| --- | ---: | --- |
| `config.json` | 44 | `df34b4f930b23447cd4dc410fabfb42eb3f24e803e6c3f97d618fb359380a36f` |
| `onnx/model_quantized.onnx` | 92,361,116 | `fbae9257e1e05ffc727e951ef9b9c98418e6d79f1c9b6b13bd59f5c9028a1478` |
| `tokenizer.json` | 3,497 | `77a02c8e164413299b4b4c403b14f8e0e1c1b727db4d46a09d6327b861060a34` |
| `tokenizer_config.json` | 113 | `be1cb066d6ef6b074b3f15e6a6dd21ac88ff3cdaedf325f0aaed686c70f75d20` |
| `voices/af_heart.bin` | 522,240 | `d583ccff3cdca2f7fae535cb998ac07e9fcb90f09737b9a41fa2734ec44a8f0b` |

The deterministic manifest describes five exact files totaling 92,887,010
bytes, and the package remains under ignored local storage. The closure run must
reverify that exact inventory, each size/hash, and the total before any run-
specific result is filled in. The package's metadata identifies Apache-2.0, but
its commercial-use classification remains `restricted` and the provider
voice's manifest rights state remains `unknown`. The command-line flag can
acknowledge only the risk of one restricted local component run; it cannot
create or replace a governed voice-rights acknowledgement and is not consent,
commercial-use approval, legal certainty, product availability, or production
clearance.

### Request and generated WAV

| Evidence | Closure value |
| --- | --- |
| Classification | `component_only_unbound_real_provider_verification` |
| Provider / adapter | `kokoro-local-onnx@1.0.0` / `1.0.0` |
| Runtime / model / provider-internal voice | `onnxruntime-cpu@1.28.0`; `onnx-community/Kokoro-82M-v1.0-ONNX@1.0`; `af_heart` |
| Governed Phase 3A binding | `componentOnlyVerification=true`; `governedPhase3aVoiceProfileBound=false`; `governedRightsRecordBound=false`; no governed voice-profile, cast-assignment, or rights-record IDs exist in this evidence |
| Restricted-use acknowledgement scope | Pending fresh command; command-only acknowledgement is not a voice-rights, consent, or commercial-use approval |
| Synthetic input/request/dictionary/plan/configuration fingerprints | Pending fresh command |
| Private evidence JSON bytes / SHA-256 | Pending fresh command |
| WAV bytes / SHA-256 / format / duration | Pending fresh command |
| Signal, silence edges, and QC findings/fingerprint | Pending fresh command |

### Managed worker ownership and shutdown

| Field | Closure value |
| --- | --- |
| Worker / launcher / owner / parent PIDs | Pending fresh command |
| Executable identity and service-computed SHA-256 | Pending fresh command |
| Runtime protocol / Job Object / authenticated ownership | Pending fresh command |
| Python socket-denial count | Pending fresh command |
| Shutdown reason / exit / owned-process exit / force behavior | Pending fresh command |

The fresh result must still be interpreted narrowly. A zero Python socket
counter is not packet-level proof: native code, a pre-patch reference, or an
inherited handle can bypass the policy. Human listening remains a separate
recorded question. Even a valid non-silent WAV and clean managed shutdown will
not establish a usable real-provider product path because there is no governed
Phase 3A voice/profile, cast-assignment, or rights binding. It cannot establish
perceived quality, intelligibility, artistic fit, consent, likeness clearance,
commercial clearance, legal certainty, or production readiness.

## Deterministic fixture and Electron E2E evidence

The deterministic provider creates stable bounded PCM from integer operations.
It exists so CI can prove the complete product lifecycle without downloading a
model or representing synthetic signal as speech quality. It is always marked
fixture-only and production-export-ineligible.

The committed development and packaged flows are required to prove, using
isolated application and temporary directories and repository-owned synthetic
content only:

1. the complete retained Phase 0-3A import, analysis, corrections, casting,
   rights, and approval prerequisites;
2. fixture package verification/activation and an authenticated owned worker;
3. an approved pronunciation entry, one narrator audition, and two character
   auditions;
4. valid bounded WAV properties and authenticated audio loading without
   `file://` or an arbitrary path;
5. an identical-input verified cache hit;
6. a superseding applicable pronunciation entry, changed dictionary/request
   evidence, targeted invalidation, preserved unrelated clips, and a new
   artifact;
7. current decisions for all five gate types;
8. close, exact owned-process exit, relaunch, and restoration of runtime,
   package, pronunciation, session, clip, cache, QC, decision, and readiness
   evidence; and
9. a bounded synthetic-only screenshot and schema-`7.0.0` machine result.

| E2E layer | Current result |
| --- | --- |
| Development Electron E2E | Pending |
| Locally packaged E2E against the local unpacked build | Pending |
| Exact-CI-artifact packaged E2E | Pending GitHub run |

No E2E layer is marked passed until its exact invocation completes.

## Exact owned-process and network-evidence semantics

Before each packaged launch, the harness records relevant preexisting state.
It establishes ownership only from the exact test-launched Electron identity,
creation identity and ancestry, the authenticated service handshake, and the
authenticated runtime-instance/worker identity. The provider worker uses the
frozen service executable's internal worker dispatch, so kind, PID, parent,
executable identity, creation identity, runtime/profile/package fingerprints,
and Job Object membership must agree.

Endpoint observation is restricted to an already-owned exact PID list. Each
PID's executable and creation identity is revalidated immediately before and
after its exact-PID TCP query. Endpoint enumeration treats only the exact
`CmdletizationQuery_NotFound_OwningProcess,Get-NetTCPConnection` no-match as
zero; every other provider, CIM, or access failure fails the gate. After the
background sampler stops, a bounded refresh-bind-observe-refresh loop requires
the append-only ledger to remain stable. A newly adopted Python PID is mandatory
on the next observation, every live owned Python identity is observed again,
and a non-loopback finding from an invalidated attempt is retained. Three
unstable attempts fail closed. The proof accepts zero observed non-loopback
endpoints; it does not claim a packet capture or OS outbound firewall. It never
performs a name-wildcard endpoint query and never reads unrelated process
content.

After each close, the gate must prove every exact owned Electron, service, and
provider-worker PID is gone, with `forcedPids=[]`, `remainingPids=[]`,
`unrelatedProcessesInspected=false`, and
`unrelatedProcessesTerminated=false`. Ambiguous ownership, PID reuse, missing
identity, or unavailable observation fails safely; it never authorizes killing
by process name. The `unrelatedProcessesInspected` field excludes the required
prelaunch relevant-name inventory, which records only bounded identity metadata;
it means that no non-owned PID is adopted or queried for endpoints or process
content. The exact CI PIDs and per-launch exit evidence are pending.

## Build-evidence and artifact semantics

After `pnpm build`, Windows CI resolves and launches exactly:

`apps/desktop/release/<version>/win-unpacked/Cinematic Story Studio.exe`

The schema-`6.0.0` generated manifest retains the Phase 0-3A evidence and adds
the validated schema-`7.0.0` Phase 3B fixture result. It must record:

- workflow head, tested checkout, pull-request head when applicable,
  application version, UTC timestamp, and runner identity;
- repository-relative paths, sizes, and SHA-256 for the exact application,
  staged service, embedded service, screenshot, and bounded result files;
- exact staged/embedded service size/hash equality;
- runtime profile/protocol and fixture/real adapter identities;
- model manifest/package, pronunciation dictionary, session, role, assignment,
  request, normalized-text, pronunciation-plan, clip, artifact, and QC evidence;
- verified cache-hit and targeted-invalidation proof without exposing the raw
  private cache key;
- immutable narrator, character, pronunciation, per-role, and readiness
  decisions plus restart persistence; and
- per-launch exact Electron/service/provider-worker ownership, endpoint
  observation, graceful exit, no forced/remaining PID, and no unrelated
  process inspection or termination.

The generator records cache keys only as opaque SHA-256 values and rejects
cache-key preimages or other unhashed cache material, fixture quality/export
overclaims, manuscript/script text, pronunciation values, personal paths,
secrets, model or audio bytes, incomplete process evidence, and disagreement
between the packaged step outcome and machine result. It revalidates exact
artifact bytes after the tracked-content scan and clean-tree check, before
short-lived upload.

| Hosted artifact field | Current value |
| --- | --- |
| Workflow head / tested checkout / PR head | Pending |
| Application bytes / SHA-256 | Pending |
| Staged service bytes / SHA-256 | Pending |
| Embedded service bytes / SHA-256 | Pending |
| Staged/embedded equality | Pending |
| Phase 3B result bytes / SHA-256 | Pending |
| Screenshot bytes / SHA-256 | Pending |
| Build manifest bytes / SHA-256 | Pending |
| Artifact ID / compressed size / digest / expiry | Pending |

No cross-environment or cross-run byte reproducibility is claimed. The required
proof is equality of the staged and embedded service within one build and E2E
against that exact build.

## GitHub Actions evidence

The Phase 3B Windows workflow is configured to use CPython `3.12.10`, reproduce
the lock with the pinned compiler, install Node dependencies frozen and Python
dependencies with hashes, verify exact parser/catalog/fixture/schema controls,
run lint/type/full and focused Phase 2-3B tests, run mandatory development E2E,
build, run mandatory exact-artifact packaged E2E, generate/validate/revalidate
evidence, rescan tracked content, prove the checkout clean, and upload only when
every gate succeeds. Failure-enforcement steps make `continue-on-error` evidence
capture non-optional.

| GitHub check | Exact result |
| --- | --- |
| Phase 3B Windows CI run/job/head/attempt | Pending |
| Lock reproduction and frozen/hash-locked installation | Pending |
| Schema/tooling tests | Pending exact count |
| Full backend | Pending passed/skipped/warning count |
| Hosted FFmpeg-dependent test | Pending; report separately |
| Desktop tests | Pending exact file/test count |
| Focused Phase 2, Phase 3A, and Phase 3B suites | Pending exact repeated-subset counts |
| Development Electron E2E | Pending |
| Exact-artifact packaged E2E | Pending |
| Manifest validation / byte revalidation / scan / clean tree | Pending |
| Security safeguards | Pending run/job and exact scan result |
| Dependency review | Pending run/job and exact delta result |

## Database-v5 migration and recovery

Migration accepts only the exact issued v4 structure and ledger `[1,2,3,4]`.
The frozen v4 SQL fixture is 44 non-SQLite tables, 67,675 bytes, with SHA-256
`50dc228bb25466571b2600e1eb14a60dadee0539971f78245569a6ca8b201290`.
Before mutation, the coordinated SQLite backup API creates and validates an
exact logical-digest-equal `v4-backup`. Unsupported drift is non-mutating;
backup failure stops before schema change.

The immediate transaction adds 22 Phase 3B tables for runtime profiles and
instances; model manifests, installations, and verifications; voice runtime
bindings; pronunciation dictionaries and entries; sessions, scripts,
normalization plans, provider requests, clips, artifacts, cache and QC records;
five-gate reviews/decisions; readiness snapshots/reviews/decisions; and targeted
invalidations. It then adds
ledger row 5 and `user_version=5`. It does not install or activate a model,
start a worker, copy manuscript text, generate audio, populate a cache, or
create/renew an approval.

The frozen v5 compatibility fingerprints are:

- fresh/direct-v4 table metadata:
  `53c3d00b4068e81238ce310e24c18a65a743c1081551d4de46fe4c190f9e92a7`;
- historical-layout table metadata:
  `3217d17fe82e7aeb3486cbd5e8c1347cd92d1cd153a354738af2eeb869ef5d6c`;
- index semantics:
  `6bc0dc622ae8c2d408cd6058f864a779af5f04244f48b13d7533144d4ab60754`;
- fresh/direct-v4 normalized SQL:
  `0d7b10d66021ec05de61abe653f38c7b5e647b5dd26f0615f335cb70ab2dbc58`;
  and
- historical-layout normalized SQL:
  `873280a52933dfc3804e6a3203aefaa01616c44229acc6b41e3c0a923fb462cc`.

There is no in-place v5-to-v4 downgrade. Recovery retains the v5 database and
opens only a separately named copy of the independently verified v4 backup with
the matching Phase 3A application.

## Pronunciation, cache, publication, and approval integrity

Pronunciation entries are append-only typed IPA, provider-neutral, or provider-
specific revisions. SSML/provider markup, forbidden control characters,
ambiguous equal-precedence matches, and unbounded provider output fail closed.
Scope precedence is exact
scene, exact chapter, explicit custom scope, narrator/character role, then
project; exact locale precedes language fallback and explicit priority. Chapter
and scene resolution uses current corrected Phase 2 structure evidence rather
than stale legacy rows.

The global dictionary fingerprint detects an in-flight stale request. The
effective plan fingerprint contains only entries applied to the exact role and
source span; it controls cache reuse and targeted invalidation. An unrelated
entry may change global dictionary history without revoking an unaffected clip.
An applicable change invalidates dependent current evidence, preserves history,
and requires new generation and human decisions.

Cache identity binds the private project boundary, provider/adapter/runtime/
model/package, voice and assignment revision, normalized-text hash, effective
pronunciation plan, provider controls, output profile, and producer version.
Every hit revalidates ownership, lexical containment, link/reparse safety,
regular-file identity, byte size/hash, and WAV metadata. Missing or changed
bytes become a typed miss/corruption finding. Cache clearing preserves
historical artifact/review/decision metadata while making audio unavailable and
invalidating current dependent readiness.

Audio publication uses an owned `.wav.pending` staging file. The final file,
artifact, cache, clip, QC, review, job, checkpoint, attempt, and terminal event
are published under one coordinated transaction; outer-commit failure removes
the final bytes. Cancellation before publication leaves no successful row or
audio. Once the exact publication claim wins, a concurrent cancel loses rather
than producing half-published state. Startup reconciliation removes only owned
staging/orphan files and preserves referenced artifacts.

Provider retry appends a distinct request row for each durable job attempt. A
failed retryable attempt remains immutable; the successful clip binds the new
attempt. Internal runtime retry is disabled, and each durable attempt can
dispatch the provider at most once. Verified cache hits are separately recorded
as lookup-only with no runtime instance and a provider dispatch count of zero.
Every authenticated synthesize-frame write is preceded by a durable dispatch
count of one, exact runtime identity, and start timestamp. A zero count proves no
dispatch occurred; a count of one records a committed attempt and is not, by
itself, evidence of provider completion. Artifact integrity and QC evidence prove
successful completion.
Phase 3B reconstructs Phase 3A authority rather than trusting a session row. It
requires the latest succeeded casting run, its exact validated immutable current
cast-snapshot manifest, only assignment IDs/revisions and lock state selected by
that manifest, exact current leaf provider/model/profile/rights evidence with
temporally applicable rights, and the latest eligible Narrator, Character, and
Complete Cast reviews with their exact effective approved human decision IDs,
actor/provenance, supersession chain, and required warning acknowledgements.
Cross-snapshot evidence cannot be combined. A newer current snapshot suppresses
older-snapshot Phase 3B projections. A new same-snapshot Phase 3A decision tuple
also changes downstream authority even when assignment bytes are unchanged.

Assignment, leaf/temporal rights, model package, provider/runtime, cast snapshot,
same-snapshot decision tuple, applicable pronunciation, cache-integrity, and
artifact-integrity changes append targeted invalidation and never silently
reapprove. Historical review IDs and changed evidence are rejected with typed
conflicts rather than accepted as current authority.

Voice Readiness requires that exact Phase 3A authority, current verified active
model/runtime evidence, approved narrator and required-character per-role and
aggregate auditions from the same snapshot, current Pronunciation Review, and
no blocking integrity finding. It authorizes later performance-direction work
only, never full-book rendering or export.

## Performance and scale evidence

The completed scale file exercised exactly 300 active roles, 1,000 current
pronunciation entries, 2,000 audition sessions, 2,000 clip metadata records,
10,000 cache records, and only three persisted fixture audio artifacts. The 300
roles required exactly two workspace pages at `roleLimit=200`; cross-project and
stale workspace-role cursors were rejected after the tested bound session/clip
collection generation changed. Exact snapshot-selected assignment and leaf/
temporal rights authority is validated separately: drift removes trusted
audition-action and current session/generation/review authority even though the
bounded metadata page may still render. The cursor does not claim assignment/
rights revision-generation fields. The file
also covered deterministic cache identity, hard count limits, cancellation,
retry, interrupted restart, runtime startup/shutdown, and maximum provider
concurrency 1. It passed with 1 test, 0 skips, and the repeated Starlette warning
in 356.97 seconds of Pytest-reported elapsed time; the outer command wall time
was 367.645 seconds.

Observed workstation intervals were:

| Scale interval | Seconds |
| --- | ---: |
| Candidate/assignment seed | 8.591 |
| Snapshot publication | 12.538 |
| Phase 3A gate refresh and decisions | 14.232 |
| Full Phase 3B fixture helper | 35.676 |
| Phase 3B metadata seed | 11.268 |
| Runtime start / stop, cycle 1 | 1.736 / 0.825 |
| Runtime start / stop, cycle 2 | 1.062 / 0.030 |
| Runtime start / stop, cycle 3 | 1.382 / 0.009 |

The enforced less-than-30-second regression threshold applies only to the Phase
3B metadata seed and each measured runtime startup/shutdown interval. It does
not apply to candidate/assignment seed, snapshot publication, gate setup, the
full fixture helper, Pytest elapsed time, or outer command wall time. These are
workstation observations and regression bounds, not universal latency, memory,
or throughput service-level guarantees.

## Security and dependency-audit scope

Model installation uses an authenticated bounded ZIP upload to a service-
generated private staging path. The renderer supplies and receives no
filesystem path. Exact trusted inventory, sizes, hashes, member-count/size/
ratio bounds, duplicate/case-fold collision checks, traversal/link/reparse/
special-file rejection, atomic placement, verification-before-activation, and
active-job removal leases are enforced. Arbitrary URLs, pickle models, scripts,
executables, shared libraries, dynamic package installation, and implicit
download are rejected.

The worker uses a fixed argv array with `shell=False`; manuscript text,
pronunciation values, secrets, and model paths are absent from argv. Bootstrap
and result frames are bounded and HMAC-authenticated. Ownership binds the
launched handle, PID/parent topology, resolved executable identity and service-
computed executable SHA-256, nonce, protocol, package/runtime identity, and
Windows Job Object. The hello does not provide the executable hash, and the
service does not independently query an OS process-creation timestamp.
Authenticated audio access is by project/session/clip/artifact identity with
`audio/wav`, `no-store`, byte-range bounds, containment, and hash/WAV
revalidation; no renderer path or
`file://` URL is accepted.

Fresh local live-registry checks completed for the current Node lock and the
61,104-byte Python requirements lock. pnpm `11.9.0` ran
`pnpm audit --audit-level high` at `2026-08-01T10:10:39.5019524Z` and reported
`No known vulnerabilities found`. A temporary Python 3.12.10 environment with
top-level `pip==26.1.2` and `pip-audit==2.10.1` ran
`pip-audit --require-hashes --disable-pip -r apps/local-service/requirements.lock`
at `2026-08-01T10:11:54.6220803Z` and
reported `No known vulnerabilities found`. The exact lock install separately
completed `pip check` with `No broken requirements found`. An earlier Python
advisory result for different lock bytes is superseded and is not carried
forward.

These live advisory queries are point-in-time local evidence, not immutable
advisory-database snapshots. The audited Python target is hash-locked; the
temporary audit tool environment pins its two top-level tools but resolves their
transitive tool dependencies from the live index. The Windows build workflow adds no
`pnpm audit` or `pip-audit` gate. Its dependency evidence is the pinned Python
3.12.10 lock reproduction, hash-locked install, frozen pnpm install, and
`pip check`. The pull-request Dependency review workflow, still pending for
this branch, applies `fail-on-severity: moderate` to the PR dependency delta
only; it is not a full-lock vulnerability audit. Security safeguards separately
run repository policy and checksum-pinned Gitleaks `8.30.1`; secret scanning is
not described as a dependency audit.

## Known limitations and unverified behavior

- The Kokoro adapter has prior component-only short-preview evidence; the fresh
  closure rerun is pending. No governed
  Phase 3A voice profile, cast assignment, or rights record binds its
  provider-internal `af_heart` voice, so the product path rejects it rather
  than silently substituting it. Performer consent, identity/likeness, and the
  complete dataset chain have not been independently established; human
  legal/rights review and an explicit governed binding remain blockers to
  product use.
- `kokorog2p` and its English lexicon have Apache-2.0 metadata, but
  redistribution provenance and NOTICE obligations still require human review.
- The component-only Kokoro command supports only English. Unknown terms fail
  into pronunciation review; no implicit engine or network fallback is used.
  This is not governed product availability.
- Model bytes are neither bundled nor downloaded. Activation verifies exact
  bytes/inventory but does not statically audit the ONNX graph or run inference;
  backend/schema/voice-tensor checks occur on first component initialization.
  Activation does not create the missing Phase 3A binding.
- Python-level socket denial is defense in depth, not an OS firewall or packet
  capture. The exact-CI owned-PID endpoint observation remains pending.
- Packaged ownership uses a 100 ms bounded process-table sampler plus stable
  pre/post-observation and shutdown reconciliation, not a kernel process-event
  ledger. It fails on observed churn or ambiguity and proves exit for every
  adopted exact identity, but a relevant child created and exited wholly
  between inventory samples is not individually recorded. The provider's
  authenticated Windows Job Object proof separately covers its owned worker
  tree; neither mechanism is continuous packet or process telemetry.
- ZIP and source-tree reparse/special entries are rejected, but a regular source
  file is not proven to have a single hard link, and the package manager is not
  a generic Windows device-name or ONNX static-analysis sandbox.
- ONNX CPU output is not promised byte-identical across CPUs or runtime builds.
  Every produced artifact is instead hashed and bound to exact provenance.
- Signal checks prove bounded file/signal integrity and non-silence, not
  intelligibility, naturalness, artistic quality, likeness, consent, clearance,
  or production fitness. Human listening did not occur for the recorded private
  real-provider clip.
- The desktop currently authors only the repository-owned synthetic script;
  exact source-span script kinds remain available through authenticated typed
  service contracts until a safe general desktop source-span selector exists.
- Auditions are short, project-private PCM clips. There is no cross-project
  cache, full-book synthesis, chapter assembly, performance-direction system,
  ambience, Foley, music, mixing, mastering, export, installer/release, signing,
  update, Phase 3C, or Phase 4 claim.
- Existing decisions remain immutable history after invalidation; regeneration
  never restores approval automatically.
- Final aggregate local tests, both Electron E2E layers, hosted Windows tests,
  exact hosted process proof, artifact hashes, Security safeguards, and
  Dependency review remain pending and must be recorded before evidence closure.

## Documentation coverage

| Required topic | Controlling document |
| --- | --- |
| Product outcomes and phase boundary | [Phase 3B local speech auditions and pronunciation](../product/phase-3b-local-speech-auditions.md) |
| Provider-neutral and real/fixture adapter boundary | [Local speech provider architecture](../architecture/local-speech-provider.md) |
| Worker startup, handshake, ownership, and shutdown | [Speech worker lifecycle](../architecture/speech-worker-lifecycle.md) |
| Model package installation, verification, and removal | [Managed speech model packages](../architecture/model-packages.md) |
| Kokoro decision, source, licenses, and restrictions | [ADR-0011](../decisions/ADR-0011-managed-local-kokoro-onnx-auditions.md) |
| Pronunciation scopes, precedence, and normalization | [Pronunciation and normalization](../architecture/pronunciation-and-normalization.md) |
| Sessions, cache, targeted invalidation, and readiness | [Audition sessions, cache, and governance](../architecture/audition-sessions-cache-and-governance.md) |
| Five approval gates | [Approval gates](../agents/approval-gates.md#phase-3b-audition-and-voice-readiness-gates) |
| Persistent jobs and redacted checkpoints | [Background jobs](../architecture/background-jobs.md) |
| Audio integrity/QC | [Audio quality standards](../audio/audio-quality-standards.md#phase-3b-audition-integrity-profile) |
| Local speech/model/audio threat model | [Local speech threat model](../security/local-speech-threat-model.md) |
| Schema-v5 migration, backup, and recovery | [Migration 0005](../migrations/0005-phase-3b-local-speech-auditions.md) |
| Failure and restart recovery | [Failure recovery](../architecture/failure-recovery.md) |
| Windows exact-artifact packaging evidence | [Windows packaging](../architecture/windows-packaging.md#phase-3b-exact-artifact-extension) |
| Desktop workflow | [User workflows](../product/user-workflows.md#4c-audition-the-governed-cast-locally) |
| Known limitations | [Phase 3B known limitations](../architecture/phase-3b-known-limitations.md) |
| Verification evidence | This record |
