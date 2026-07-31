# Phase 2 verification record

## Evidence policy

This record separates locally executed checks from GitHub Actions checks. A
local result does not stand in for the exact CI-produced application and
service, and a CI result is not reported as a local observation. The canonical
artifact below was produced and exercised by the successful push workflow for
the code commit under verification. This record's evidence-closure commit
changes only this document; its final-head push and pull-request workflows must
also pass before handoff.

Only the public, repository-owned fixture family under
`fixtures/synthetic-story/` was used. The generated evidence records opaque
IDs, hashes, counts, versions, states, runner identity, and exact owned process
identities. It contains no manuscript excerpts, request bodies, tokens,
databases, audio, models, logs, absolute personal paths, or generated
application binaries.

## Revision and canonical artifact

| Field | Value |
| --- | --- |
| Branch | `codex/phase-2-story-intelligence` |
| Code/artifact source commit | `1084cb75875824dbf5f8087d0fcd454b1e369fcf` |
| Application version | `0.1.0` |
| Verification date | 2026-07-31 UTC |
| Canonical Windows CI run/job | `30595242789` / `91046107125`, successful |
| Code-head PR Windows CI run/job | `30595244921` / `91046113733`, successful |
| Artifact ID/size/digest | `8780115193`; 427,010,997 bytes; `sha256:b27f422f877b5fe88f401d2f06e43bdcc4d1e6604fba01f7cfa020b8d865e28c` |
| Artifact expiry | `2026-08-07T01:18:04Z` |

The downloaded artifact was independently rehashed outside the repository.
Its application, staged service, embedded service, machine result, and
screenshot matched the manifest exactly. Generated files remain ignored and
were not committed.

## Locally executed checks

The local environment was Microsoft Windows 11 Home 64-bit, version
`10.0.26100` (build `26100`), with Node.js `24.14.0`, pnpm `11.9.0`, and
Python `3.14.6`. Python 3.14 was a local compatibility observation rather than
the documented Python 3.12 development baseline; the authoritative supported
artifact gate below used CPython `3.12.10`.

| Check | Command | Exact local result |
| --- | --- | --- |
| Frozen dependency install | `pnpm install --frozen-lockfile` | Passed; all three workspace projects were current |
| Lint | `pnpm lint` | Passed helper syntax, ESLint with zero lint warnings, Ruff, tracked-content scanning, and diff safeguards |
| Type checks | `pnpm typecheck` | Passed both TypeScript projects and strict mypy across 20 service source files |
| Full repository tests | `pnpm test` | 38 schema/tooling passed; 236 backend passed; 157 desktop passed across 10 files; 431 total passed, 0 failed, 0 skipped; one backend `StarletteDeprecationWarning` |
| Focused Phase 2 service suites | The seven migration, intelligence, API, correction, graph, and scale test files used by CI | 90 passed, 0 skipped; one identical `StarletteDeprecationWarning`; these 90 repeat tests already present in the full backend count |
| Windows build | `pnpm build` | Passed contracts, PyInstaller service smoke/staging, renderer/main/preload build, and unpacked Electron packaging |
| Development Electron E2E | `CSS_E2E=1`; Playwright `tests/e2e/persistence.spec.ts` | Final stress gate passed 10 consecutive runs with one worker, zero retries, and max-failures 1 in 6.2 minutes |
| Packaged Electron E2E | `pnpm --filter @cinematic-story-studio/desktop run test:e2e:packaged` against the exact local unpacked executable | 1 passed in 1.2 minutes; both launches, restart persistence, ownership, and shutdown checks passed |
| Tracked/staged/precommit safeguards | `pnpm scan:tracked`; `pnpm scan:staged`; `pnpm precommit` | Passed before every Phase 2 commit; the code-head tracked scan covered 264 files |
| Clean checkout | `node scripts/git-check.mjs --clean` | Passed after the code commits and local gates |

FFmpeg was installed locally, so the loudness capability test ran and the local
backend suite reported 236 passed and 0 skipped. The hosted runner did not have
FFmpeg and therefore reported 235 passed and 1 skipped. These counts are not
interchangeable.

The local unpacked artifact was
`apps/desktop/release/0.1.0/win-unpacked/Cinematic Story Studio.exe`.
The application was 225,613,824 bytes with SHA-256
`452db4a9e7186ac22447e2ae82bca097551cb382e4ad0a0274f513f31f66ba4e`.
The local staged and embedded services were each 28,473,852 bytes with SHA-256
`ea5069ff806a157778ca3eb1cb154168ca4bb3d7924c6accdd1a5b47ae5fdc2d`.
That local service differs from the hosted Python 3.12 build; no
cross-environment byte reproducibility is claimed. Each environment tested its
own exact staged/embedded pair.

The local development E2E observed the authenticated service bound only to
`127.0.0.1:64279`; no launch token was recorded. It observed `Backend ready`,
project creation, queued and completed extraction, Import Review pending then
approved, baseline analysis succeeded, whole-book analysis running through all
stages then succeeded, human-authority correction notices, four approved gate
cards, close, second `Backend ready`, restoration, and final close.

## GitHub Actions checks

The canonical job ran on GitHub Actions runner `2.336.0`, hosted agent
`20260707.563`, Microsoft Windows Server 2025 Datacenter `10.0.26100`, image
`windows-2025-vs2026` version `20260714.173.1`, with Node.js `24.18.0`, pnpm
`11.9.0`, and CPython `3.12.10`. Workflow head and tested checkout were both
`1084cb75875824dbf5f8087d0fcd454b1e369fcf`; the push-run pull-request head
was correctly `null`.

Every positive gate step succeeded: credential-free checkout; Python, pnpm,
and Node setup; frozen Node and hash-locked Python installation; exact
`lxml==6.1.1` and `pypdf==6.14.2` verification; deterministic fixture/schema
tests; lint; type checks; full tests; focused Phase 2 tests; development assets
and E2E; exact service/application build; packaged path resolution; exact
packaged E2E; manifest generation and schema validation; sanitized evidence
printing; tracked-content rescan; clean-tree verification; and artifact upload.
The four fail-closed enforcement steps were correctly skipped because the
development E2E, packaged E2E, manifest generation, and manifest validation
outcomes were already successful.

| GitHub check | Exact canonical result |
| --- | --- |
| Locked parser/public-fixture gate | 31 passed, 0 failed, 0 skipped |
| Schema/tooling | 38 passed, 0 failed, 0 skipped |
| Backend | 235 passed, 1 skipped, 1 warning |
| Hosted FFmpeg result | `test_audio_qc.py:254` skipped because FFmpeg was not installed; all 235 other backend tests passed |
| Desktop | 10 files, 157 passed; the same-project refresh regression passed in 1.590 seconds |
| Focused Phase 2 service suites | 90 passed, 1 warning; repeated subset, not additive to the full backend total |
| Development Electron E2E | 1 passed; test 26.1 seconds, total 27.1 seconds |
| Exact-artifact packaged E2E step | Successful; 1 passed; test 46.7 seconds, total 47.6 seconds |
| Manifest generation/validation | Schema `3.0.0` manifest and schema `4.0.0` packaged result both passed |
| Tracked scan / clean checkout | Both passed after build and E2E |

The one Pytest warning, repeated by the focused subset, is the upstream
FastAPI/Starlette TestClient `StarletteDeprecationWarning` about the current
`httpx` compatibility path. The build also emitted non-failing
electron-builder diagnostics for a missing package author and package-manager
environment fallback. During `pnpm/action-setup`, the action's transient npm
bootstrap reported Node `DEP0190`, one high-severity npm audit item, and a pnpm
layout warning. Those setup-action messages are not findings in the
repository's locked graph and are not silently represented as a successful
project vulnerability audit.

## Approved input and fixture proof

The packaged run used only the generated DOCX represented by
`fixtures/synthetic-story/sample-story.docx.base64`, with isolated `APPDATA`,
`LOCALAPPDATA`, `TEMP`, and `TMP`.

| Evidence | Value |
| --- | --- |
| Fixture ID | `phase-2-whole-book-intelligence` |
| Encoded DOCX path/hash | `fixtures/synthetic-story/sample-story.docx.base64`; `a571b76bb846dc81e81a0ccad3d535d3ab479a006e3a31a05501817627f68f5d` |
| Decoded DOCX bytes/hash | 2,758; `ddddd80f6486e22b8a38e66416d1e69f9ccb66cd34bc238e502cd999c6b64ee4` |
| Canonical text bytes/code points/hash | 3,783 / 3,783; `d65b8ce103a3cab7943562629109133bdc850b637068919ff7fecd999e20ee0a` |
| Markdown bytes/code points/hash | 3,832 / 3,832; `a8f2a5958a1706c2cca70711676ce96522d79d4e520aa07c6eb78dc648eaab19` |
| Source document ID/revision | `c65f5953-0a90-42fa-a898-961873a3437c` / 1 |
| Extraction ID/revision | `5c2e8693-f9de-4ce7-8db7-aea0bf27a342` / 1 |
| Extracted text hash | `0df10ce37649dd291e3bb8f3a45a06f46bf2513154d59c04573335a65800ea69` |
| Import Review ID/revision/decision | `29f727d2-1fd1-4b1d-83f8-50868c542d7d` / 2 / `bddef435-1e05-498d-896e-b2d3cebb0575` |
| Story ID/revision/fingerprint | `10a21fd9-4a97-4f20-b9da-085013f76c88` / 1 / `0df10ce37649dd291e3bb8f3a45a06f46bf2513154d59c04573335a65800ea69` |
| Extraction/review result | Extraction revision 1, zero warnings, approved; extraction, approval, baseline analysis, and correction persisted after restart |
| Approved evidence fingerprint | `ad8573553a3ad092a7e0dfbbfa217c8b8ff9b78117a5a5c8f09ef0febf3d1fc1` |

The synthetic expectations prove three chapters, six scenes, ten named
characters, six locations, ten dialogue lines including four ambiguous lines,
one POV shift, at least one continuity anomaly, and 51 expected source spans.
Exact uncertain identity/dialogue states remained explicit until human
correction; no machine claim was promoted merely to reach coverage.

## Analysis contract, agents, and stages

The analysis contract is `2.0.0`. The deterministic profile is
`whole-book-intelligence-v1@1.0.0`, fingerprint
`6ae73e83e89fbcfc0261ff339950407913cd990093fa13cdcc83ce3b1da810ec`,
produced by `whole-book-analysis-orchestrator@1.0.0`. Offsets are Unicode code
points.

All 11 deterministic local-runtime agent executions in the canonical packaged
run succeeded:

| Agent/version | Execution ID | Output fingerprint |
| --- | --- | --- |
| `story-structure@1.0.0` | `57a023e9-9113-4457-8908-9c040417a29c` | `325c1fe80c1bbbf40cfd407741e44eee235bd28ace63dfdd72f451f00c0d0fd3` |
| `story-beats@1.0.0` | `cdf48a93-5ced-4dea-8d02-a70530067aed` | `88cb14c0b1cb3121c2f1dd0399a2722bd1e7c3beefc7ba3f97f7a47030e1a01b` |
| `character-identity@1.0.0` | `72d9b3c8-9ca5-4c6f-99e5-3092728f31d2` | `29e9f6968d65cc460ab59e3e8fb5aa366ff728bd326ada9a6d36e140ed0d349d` |
| `dialogue-attribution@1.0.0` | `fe3e45db-b00a-4e2a-b610-a29fe897f447` | `6edbcb3583915ec366fa850944709f442b4319bea689c406a4d179d3d6774e13` |
| `point-of-view@1.0.0` | `a8b3273f-7a3e-4e91-98b7-74b072e405e3` | `f043db68b58673d37830b1a1a324cbac316ad290a6c08ee0ee302a2fe66796cd` |
| `story-setting@1.0.0` | `d2417f81-ecc2-45c3-a039-5c343840858f` | `b675af06581b2371e56488e3211565cf621dd40d7e073f3d6b9f7d076193cf9c` |
| `story-timeline@1.0.0` | `23605072-8e76-4075-b417-7e3e82ab56d4` | `6499d5b3e78429f61d7b615427c7f77cfc3ee06771df71900e1c77da9e4c8dd4` |
| `character-relationships@1.0.0` | `d4015d0c-a5c0-4c7f-8941-53a545b403e3` | `6c22b81d5981a4e4cfef26a72e466e92c2aae03d742e19a10522d4c7418cb5ab` |
| `emotion-dramatic-intent@1.0.0` | `9ef21ec1-6253-471b-86a0-8e9768fd54b7` | `2994b0df92132e05261148f41aecb06c500d0e2b94f0c00dfadad9732a4ecf63` |
| `story-continuity@1.0.0` | `a8d6e8b9-0677-4025-9776-4a435a13c677` | `9111238b4dd39aba2fb513387bc60fda33233d26f8da7efeef075c713c95fee1` |
| `analysis-synthesis@1.0.0` | `74e988b4-c366-4376-a318-09765b7b3fac` | `e050556883d707b2164d44d9b0af72a19ea37942092a52ee0b7e73e4b9747510` |

The exact observed stage order was:
`validate_approved_input`, `initialize_run`, `analyze_structure`,
`analyze_beats`, `analyze_character_identity`,
`analyze_dialogue_attribution`, `analyze_point_of_view`,
`analyze_locations`, `analyze_timeline`, `analyze_relationships`,
`analyze_emotion_intent`, `analyze_continuity`, `synthesize_analysis`,
`publish_analysis`.

## Run, snapshot, and collection evidence

| Field | Value |
| --- | --- |
| Analysis run/job | `6bf591c5-0280-44f0-b693-1cb97fef3b4f` / `7fcee0aa-cfa6-43ea-b874-4b1378810b67`, succeeded |
| Input fingerprint | `0df10ce37649dd291e3bb8f3a45a06f46bf2513154d59c04573335a65800ea69` |
| Run fingerprint | `a0e8d4a4826bd4f7b80c97a794a9773084b34b7118b0d17b3e2605e71c3968d5` |
| Snapshot ID/revision | `1c27db89-d060-4ab1-8bdc-e888dfdc881e` / 1 |
| Snapshot fingerprint | `32c40fb1ce8eba0de30d1d32a28c1ac6741aac332c1195f530afe22c04e89ba1` |
| Correction-set fingerprint | `cc5b672d7158136f59e9283ad2127da4580c4960b029b3555b4e247a81575c41` |

The exact 16 persisted collection counts were: agent executions 11, chapters
3, scenes 6, beats 25, characters 10, mentions 96, dialogue lines 10,
narration spans 18, POV segments 6, locations 6, timeline events 6, temporal
constraints 5, relationships 10, emotional states 5, dramatic intents 10, and
continuity findings 4. The separate effective correction count was 4.

Structure, character registry, ambiguous identity, ambiguous dialogue,
narration distinction, POV shift, locations, flashback, relationship change,
emotional progression, and continuity anomaly assertions were all true.

## Human corrections and review gates

The supported contract categories are exactly `structure_boundary`,
`structure_label`, `character_identity`, `character_alias`,
`character_merge`, `character_split`, `mention_resolution`,
`dialogue_speaker`, `point_of_view`, `location_identity`,
`location_alias`, `temporal_order`, `relationship`, `emotional_state`,
`dramatic_intent`, and `continuity_disposition`. Their typed operations cover
add/remove/move/relabel structure; identity merge/split/unresolved/lock;
alias add/remove; mention resolve/unresolve; speaker correct/clear; POV
correct/lock; location/alias correction; temporal annotation/resolution;
relationship label/direction/scope; continuity disposition; and
emotion/intent correction. This is the tested contract surface, not a claim
that every category was exercised in the packaged flow.

The packaged flow retained the Phase 0 speaker-correction bridge and added
three direct Phase 2 corrections. The direct corrections were immutable,
locked against automation, effective with `human` authority before and after
restart, and bound to these reason digests:

| Correction | ID / target | Previous fingerprint | Corrected/effective-before/effective-after fingerprint | Reason SHA-256 |
| --- | --- | --- | --- | --- |
| Structure boundary/label | Not exercised in packaged E2E; typed add/move/remove/rename behavior passed API, service, and desktop tests | not applicable | not applicable | not applicable |
| Character identity | `5ec0bb14-3901-472e-9838-2268a06a3112` / `fdb1b993-c256-5789-b569-c61334390dcf` | `7974a58fed43f6060eca3beefe9cbff1eca89a321f159fc0a7163a1fefd83835` | `d13b21481a7840d23e92f71b270e3801192015a67e4be81be923f1dd5555d7fc` | `b42b6091d0bde37b4dd15f99a321e86dd965f2272a4ca68da6703b6f5ba2f0da` |
| Dialogue speaker | `9c9885f9-1890-436f-9177-5e14ac7e19b0` / `eb361fe4-0baa-54d8-a747-d44008af63f5` | `33f1726886424260b0f7970fade01f2e3edd0329b5923293d149ce8283d84e84` | `397aa9a044c2f61d8b98071fc7ba42e49de3ecd2715b91c40cc8a7125d5dd60f` | `6db94e679f663d3dfbed36c173eb8445d6a364beb114c7f8c070e30983247300` |
| Continuity disposition | `78c41f72-05be-4981-a3b3-c97d3c217232` / `13d31b82-e29c-5578-9928-518eae37a777` | `30e0e4726af7ec1332d2b804a75c6d13be634ecf3c820c95ddaa9e464d61e87e` | `b8a41dcffa4632b5b64830b6ce76cbef4c0c5c153e5a0842b89a42b7606f6c0f` | `d7497ae908f34096c6bbbfdcbf7ea8287967882880cd229af078eee2383c2554` |

The Phase 0 bridge reason is intentionally not serialized into the Phase 2
manifest. Its correction record, effective speaker, analysis, and approval
state were compared before and after restart.

All four decisions were immutable, approved, and byte-identical in their
recorded fingerprints after restart:

| Gate | Review / decision ID | Artifact fingerprint | Evidence fingerprint | Decision-record fingerprint | Before/after |
| --- | --- | --- | --- | --- | --- |
| `story_structure_review` | `e84bca14-e43a-51dc-9f79-56495aa01f5e` / `734787ff-d420-49a9-b4ea-32c448fcf1e7` | `89e398402fd4c49c20ff8230edf1d8745ab55aa73b6f6fd86a666cc709631aaa` | `89e398402fd4c49c20ff8230edf1d8745ab55aa73b6f6fd86a666cc709631aaa` | `f1dbb82d91b88aeeb2a7463db59be50c7d9b172843f16383c3f62840dcfbcb5d` | Approved and identical |
| `character_registry_review` | `2e8010da-1b3c-532c-8826-71f93560b5dc` / `f8b99705-7526-4c77-aacb-e4e7eb31ec27` | `1bdff4ded19d43681cdded3b2c9055ce3ac90d3d92bd8c3d39c321adc00cf8bd` | `1bdff4ded19d43681cdded3b2c9055ce3ac90d3d92bd8c3d39c321adc00cf8bd` | `6a88a213a75f23dde37d570ff8278b3e7ea80fb0a1fd92790e375f0ed2f6ca64` | Approved and identical |
| `dialogue_attribution_review` | `2d6a3366-a1fd-5785-ae89-18730796d528` / `6d50f130-4ac8-40bb-946c-6429dc00de7a` | `18ac81d4df761f2f2c79924f1bcd3b6182d08ab44e3dfedc03c310357514861f` | `18ac81d4df761f2f2c79924f1bcd3b6182d08ab44e3dfedc03c310357514861f` | `fece0c6335b5f98b9a0289af6e29bb988b4b5f8470b931504c711a5f1ca72f5c` | Approved and identical |
| `whole_book_analysis_review` | `6c31c643-646e-51a8-bb7c-a082f4ad35e1` / `8bba9f9d-c93e-4a5d-bec8-cce0a3e49ec9` | `a6a438c95944bac6efe21dbc2a048427eb2e56da1fde85526bef636bc0a99494` | `a6a438c95944bac6efe21dbc2a048427eb2e56da1fde85526bef636bc0a99494` | `f69f71b0b4cc9b1228dc04c57511de4e7b5608d70ff5ee1716d0e336d2c620ff` | Approved and identical |

The first three gates are independently reviewable; Whole-Book Analysis Review
requires all three current approvals. Approval is evidence-revision scoped and
does not authorize casting or audio production.

## Exact packaged workflow and restart

The schema-v4 machine result recorded this exact flow:

`create`, `import_synthetic_docx`, `wait_for_extraction`, `review_import`,
`approve_import`, `analyze`, `correct_speaker`,
`start_whole_book_analysis`, `observe_analysis_stages`, `inspect_structure`,
`inspect_character_registry`, `correct_character_identity`,
`inspect_dialogue_and_narration`, `correct_dialogue_speaker`,
`inspect_whole_book_intelligence`, `disposition_continuity`,
`approve_story_structure_review`, `approve_character_registry_review`,
`approve_dialogue_attribution_review`, `approve_whole_book_analysis_review`,
`close`, `restart`, `restore`, `verify_import_review_persistence`,
`verify_story_analysis_persistence`, `close`.

After the second launch, the exact source/extraction, Import Review approval,
baseline analysis, Phase 2 run, snapshot, 11 agent executions, four
corrections, correction-set fingerprint, and four gate decisions were
restored. `runPersisted`, `snapshotPersisted`, `correctionSetPersisted`,
`gateDecisionsPersisted`, and `agentExecutionsPersisted` were all true.

## Process ownership and exit proof

Before each launch, the fail-closed inventory found no preexisting relevant
process. Inventory was limited to the two exact executable identities. Owned
processes were established from exact executable path, PID, invariant creation
time, and parentage rooted in the test launcher; no process was adopted,
inspected for manuscript data, or terminated merely by name.

| Launch | Launcher/root | Owned app PIDs | Owned service PIDs | Exit |
| --- | --- | --- | --- | --- |
| 1 | 7176 / 5380 | 1236, 4656, 5380, 8496 | 628, 2784, 7892 | All seven exited gracefully; forced `[]`; remaining `[]` |
| 2 | 3304 / 2172 | 2172, 7712, 7776, 8252 | 5716, 6196 | All six exited gracefully; forced `[]`; remaining `[]` |

The exact owned identities were:

| Launch | PID | Parent PID | Kind | Invariant creation time |
| --- | ---: | ---: | --- | --- |
| 1 | 5380 | 7176 | app | `2026-07-31T01:17:29.9729690Z` |
| 1 | 4656 | 5380 | app | `2026-07-31T01:17:30.1100240Z` |
| 1 | 8496 | 5380 | app | `2026-07-31T01:17:30.1096830Z` |
| 1 | 1236 | 5380 | app | `2026-07-31T01:17:30.1531020Z` |
| 1 | 2784 | 5380 | service | `2026-07-31T01:17:30.3318640Z` |
| 1 | 628 | 2784 | service | `2026-07-31T01:17:30.8368550Z` |
| 1 | 7892 | 628 | service | `2026-07-31T01:17:34.0034060Z` |
| 2 | 2172 | 3304 | app | `2026-07-31T01:17:52.6249860Z` |
| 2 | 8252 | 2172 | app | `2026-07-31T01:17:52.7401420Z` |
| 2 | 7776 | 2172 | app | `2026-07-31T01:17:52.7447770Z` |
| 2 | 7712 | 2172 | app | `2026-07-31T01:17:52.7783310Z` |
| 2 | 5716 | 2172 | service | `2026-07-31T01:17:52.9478230Z` |
| 2 | 6196 | 5716 | service | `2026-07-31T01:17:53.4600710Z` |

Shutdown required two post-close absence observations for every exact owned
PID. Ownership was established, both launches completed, cleanup completed,
and no unrelated process was terminated.

## Build-evidence manifest

The manifest was generated at `2026-07-31T01:18:00.380Z` by
`GitHub Actions 1000001506`, Windows X64, GitHub-hosted, workflow run
`30595242789`, attempt 1, job `verify-and-build`.

| Artifact | Relative path | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| Desktop application | `apps/desktop/release/0.1.0/win-unpacked/Cinematic Story Studio.exe` | 225,613,824 | `452db4a9e7186ac22447e2ae82bca097551cb382e4ad0a0274f513f31f66ba4e` |
| Staged service | `apps/desktop/build-resources/service/cinematic-story-service.exe` | 27,133,007 | `801c111bf96cfe724034187990c29b981b77a51ae95e2a69072c6567453f2bd7` |
| Embedded service | `apps/desktop/release/0.1.0/win-unpacked/resources/service/cinematic-story-service.exe` | 27,133,007 | `801c111bf96cfe724034187990c29b981b77a51ae95e2a69072c6567453f2bd7` |
| Packaged result | `apps/desktop/release/0.1.0/packaged-e2e-result.json` | 22,666 | `9a9bb304b4955056631bc4afcefef7dd0318a82a27b21b3eb82e56be69ca189d` |
| Packaged screenshot | `apps/desktop/release/0.1.0/packaged-e2e.png` | 152,685 | `f0102f2d96f90cd867b449b278afe14197d8806f143b966acee70b70f542f241` |
| Manifest after download | `apps/desktop/release/0.1.0/build-evidence.json` | 34,904 | `9293ec7d2a06e250dcedc8537b19e0b7a0fc6a67167953a9a4198a2b98ca4665` |

Staged and embedded services matched. Packaged result and step outcome agreed.
All 14 named assertions were `true`:
`stagedServiceMatchesEmbeddedService`,
`packagedE2eHarnessResultMatchesStepOutcome`,
`packagedE2eOwnershipExitProven`, `phase1DocxImportReviewProven`,
`phase2ProfileAndAgentsProven`, `phase2ApprovedInputProven`,
`phase2RunSnapshotAndStagesProven`, `phase2StoryAssertionsProven`,
`phase2CorrectionsProven`, `phase2FourGateDecisionsProven`,
`phase2DecisionRecordsPersisted`, `phase2RestartDurabilityProven`,
`phase2WholeBookAnalysisProven`, and `packagedE2eEvidenceComplete`.

## Confidence, pagination, payload, and scale limits

Confidence is exact: `unknown` = 0; `low` is greater than 0 and less than 0.75;
`medium` is at least 0.75 and less than 0.85; `high` is at least 0.85 and at
most 1. Scores are evidentiary classifications, not statistical certainty.

The profile limits are: page default 50 and maximum 200; evidence excerpt 512
Unicode code points; 16 evidence spans per claim; 8 speaker candidates per
line; exact-text projection 16,384 code points; 32 warnings per entity;
150,000 analysis words; 250,000 persisted entities; 32 KiB runtime-agent
envelope; 64 MiB checkpoint; and 5 retained snapshot stages. Separate service
controls are a 64 KiB mutation body, 16 KiB/32-field correction patch, 256
unique IDs per correction array, at least one mention for a split, 1,000-code
point correction reason, 4,000-code point review rationale, and 4,096
correction-overlay target identities. List endpoints never return the complete
manuscript.

The deterministic generated scale case exercised 101,004 words, 200 scenes,
2,000 dialogue lines, 2,000 mentions, 4,000 beats, and 13,003 total analysis
entities. The executable peak-RSS ceiling is 320 MiB; the observed local
measurement was 163.793 MiB. Correction-aware merge POST completed in 0.829
seconds and corrected character GET in 0.581 seconds, both below the desktop
12-second request budget. Tests also proved bounded pages/payloads, controlling
index use, stable fingerprints, cooperative cancellation, and checkpoint
restart. These are regression measurements, not universal latency or memory
SLAs.

## Acceptance-test mapping

The full and focused Node, Pytest, and Vitest suites passed the primary
executable levels for `VS-201` through `VS-243` and the scale behavior in
`VS-249`. `VS-244` is the successful development Electron E2E. `VS-245` is
the successful exact-CI-artifact packaged E2E. `VS-246` is the two-launch
owned-process exit proof. `VS-247` is the successful tracked/staged policy
scan. `VS-248` is the checksum-pinned full-history Gitleaks result. `VS-250`
is the successful Phase 0/1 regression suite, build, development E2E, and
packaged gate. This mapping does not turn a unit-test observation into an
unrecorded manual UI claim.

## Dependency and vulnerability-evidence scope

CI dependency evidence consists of frozen pnpm installation, hash-locked
Python installation, `pip check`, exact locked parser assertions, and the
successful pull-request `Dependency review` workflow with a `moderate` failure
threshold. The separate `Security safeguards` workflow enforces repository
policy and scans Git history with Gitleaks; it is not described as a
dependency audit.

At the code artifact head, Dependency Review run/job `30595244861` /
`91046113464` succeeded with no vulnerable package at moderate-or-higher
severity, no denied package, and an empty dependency-change report. Its scope
is the pull-request dependency delta, not every transitive package in every
ecosystem.

Security push run/job `30595242831` / `91046106997` and pull-request run/job
`30595244846` / `91046113466` both succeeded. They scanned 264 tracked files
and 36 commits with checksum-pinned Gitleaks `8.30.1`, finding no leak.

Phase 2 does not add `pnpm audit` or `pip-audit` as CI gates and does not claim
a comprehensive ecosystem vulnerability audit. The live-registry audit
results in the Phase 1 record remain explicitly local-only and do not become
Phase 2 CI evidence. No downloader or mutable vulnerability scanner was added.

## Schema-v3 migration and rollback

Migration accepts only the exact frozen schema-v2 signature and ledger
`[1,2]`. Before mutation it creates a permission-restricted, SQLite-consistent
`v2-backup`, verifies quick check, schema signature, ledger, and canonical
logical SHA-256 equality, then atomically publishes the backup. Version-3 DDL,
legacy speaker-correction projection, indexes, ledger row 3, and
`user_version=3` are one immediate transaction.

Projects, source/extraction revisions, parser executions, Import Review
decisions, legacy story graph, jobs/checkpoints/idempotency, and Phase 0 human
speaker corrections are preserved. Migration creates no analysis run,
snapshot, or approval and auto-approves no gate. Unsupported drift or backup
failure is non-mutating; injected migration failure rolls the working database
back to exact v2 while retaining the verified backup.

There is no in-place v3-to-v2 downgrade. Recovery keeps the v3 database and
opens only a copy of the verified v2 backup with the matching Phase 1
application.

## Known limitations and unexercised checks

- The analyzer is a deterministic local rules baseline, not human-level
  literary understanding or production readiness. Implicit identity, irony,
  subtext, experimental dialogue/structure, and weak chronology may remain
  unknown, ambiguous, contradictory, or missed.
- Local service gates ran under Python 3.14.6; the documented supported
  Python 3.12 proof is the successful hosted CPython 3.12.10 job.
- Resume is durable at the verified structure artifact and complete
  pre-publication checkpoint, not inside every entity of every stage.
- Corrections carry automatically only across exactly compatible targets and
  evidence; changed source/extraction identity requires explicit remapping.
- No structure correction was performed in the packaged E2E, although typed
  structure correction behavior passed executable API, service, and UI tests.
- Hosted FFmpeg was absent, so exactly one unrelated audio capability test was
  skipped. It passed locally where FFmpeg was installed.
- Four conditional enforcement steps in the successful Windows job were
  skipped by design because their corresponding failure predicates were false.
- The Starlette TestClient deprecation warning and the setup/build diagnostics
  listed above remain non-failing. The action bootstrap's npm audit message is
  outside the locked project graph and remains an explicitly scoped warning.
- Builds are not claimed byte-reproducible across Python/build environments.
  Exact staged/embedded equality and exact-artifact E2E are proven within each
  recorded build.
- Cloud analysis, cloud speech, local model download, OCR, casting, voice
  selection, pronunciation production, synthesis, sound design, mixing, audio
  QC/export, installer signing, updates, releases, and Phase 3 are not
  implemented.
