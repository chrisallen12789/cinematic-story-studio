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
| Code/artifact source commit | `0f2583b7b1409a663368b504f73c2a30f075f520` |
| Application version | `0.1.0` |
| Verification date | 2026-07-30 UTC |
| Canonical Windows CI run/job | `30588967280` / `91026816077`, successful |
| Code-head PR Windows CI run/job | `30588969013` / `91026821380`, successful |
| Artifact ID/size/digest | `8777922822`; 426,968,851 bytes; `sha256:fc27f10cf1aa916cb05eeba17ad63b3dcda06478e526974261f667c520e22df7` |
| Artifact expiry | `2026-08-06T23:14:00Z` |

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
| Full repository tests | `pnpm test` | 38 schema/tooling passed; 236 backend passed; 156 desktop passed across 10 files; 430 total passed, 0 failed, 0 skipped; one backend `StarletteDeprecationWarning` |
| Focused Phase 2 service suites | The seven migration, intelligence, API, correction, graph, and scale test files used by CI | 90 passed, 0 skipped; one identical `StarletteDeprecationWarning`; these 90 repeat tests already present in the full backend count |
| Windows build | `pnpm build` | Passed contracts, PyInstaller service smoke/staging, renderer/main/preload build, and unpacked Electron packaging |
| Development Electron E2E | `CSS_E2E=1`; Playwright `tests/e2e/persistence.spec.ts` | 1 passed; test 29.9 seconds, total 31.4 seconds |
| Packaged Electron E2E | `pnpm --filter @cinematic-story-studio/desktop run test:e2e:packaged` against the exact local unpacked executable | 1 passed in 1.2 minutes; both launches, restart persistence, ownership, and shutdown checks passed |
| Tracked/staged/precommit safeguards | `pnpm scan:tracked`; `pnpm scan:staged`; `pnpm precommit` | Passed before every Phase 2 commit; the code-head tracked scan covered 263 files |
| Clean checkout | `node scripts/git-check.mjs --clean` | Passed after the code commits and local gates |

FFmpeg was installed locally, so the loudness capability test ran and the local
backend suite reported 236 passed and 0 skipped. The hosted runner did not have
FFmpeg and therefore reported 235 passed and 1 skipped. These counts are not
interchangeable.

The local unpacked artifact was
`apps/desktop/release/0.1.0/win-unpacked/Cinematic Story Studio.exe`.
The application was 225,613,824 bytes with SHA-256
`4b50888ddab6327f329f2a53c732b5c79de4ea40e3b9bf5aaa16d0760d69a7a6`.
The local staged and embedded services were each 28,474,084 bytes with SHA-256
`7192fcbf16e7facf31dda402c8de853eba8f1b2499c8af6c721a8f7936590750`.
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
`0f2583b7b1409a663368b504f73c2a30f075f520`; the push-run pull-request head
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
| Backend | 235 passed, 1 skipped, 1 warning in 399.49 seconds |
| Hosted FFmpeg result | `test_audio_qc.py:254` skipped because FFmpeg was not installed; all 235 other backend tests passed |
| Desktop | 10 files, 156 passed in 11.47 seconds |
| Focused Phase 2 service suites | 90 passed, 1 warning in 250.10 seconds; repeated subset, not additive to the full backend total |
| Development Electron E2E | 1 passed; test 23.9 seconds, total 25.0 seconds |
| Exact-artifact packaged E2E step | Successful; 1 passed; test 33.4 seconds, total 34.3 seconds |
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
| Source document ID/revision | `3cca5303-8fc7-4356-b424-f8a570973d53` / 1 |
| Extraction ID/revision | `b93c2323-49e1-4099-91af-59cfd07c17ec` / 1 |
| Extracted text hash | `0df10ce37649dd291e3bb8f3a45a06f46bf2513154d59c04573335a65800ea69` |
| Import Review ID/revision/decision | `e670d7e4-d8e7-4b2b-9c1c-183b6d415e87` / 2 / `f5c2491c-4e2c-4708-9a1d-a614b3d885b6` |
| Story ID/revision/fingerprint | `0db24899-a2a7-4de9-b6d7-a169e87588d3` / 1 / `0df10ce37649dd291e3bb8f3a45a06f46bf2513154d59c04573335a65800ea69` |
| Extraction/review result | Extraction revision 1, zero warnings, approved; extraction, approval, baseline analysis, and correction persisted after restart |
| Approved evidence fingerprint | `319545e4a179a06d0d28a4fc44ae03d0b81c08843ecdefa8b8dd7d3348c71ca6` |

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
| `story-structure@1.0.0` | `32262ef1-9118-4526-8660-454b15313ed0` | `325c1fe80c1bbbf40cfd407741e44eee235bd28ace63dfdd72f451f00c0d0fd3` |
| `story-beats@1.0.0` | `da85440c-16c4-414d-8804-3024d0bb50e4` | `88cb14c0b1cb3121c2f1dd0399a2722bd1e7c3beefc7ba3f97f7a47030e1a01b` |
| `character-identity@1.0.0` | `59832e45-e5ba-4e81-b7ef-51ab5dce9c3c` | `b31cfe36dc8fed3af773c183a8f6af93b5e98e5cf92ea985ce35b3f92cc5505a` |
| `dialogue-attribution@1.0.0` | `1a6b76c5-009b-4629-973c-27d2f0b5b506` | `21a712b7f4b87baa807d1e6378599f4b6df699a99562f4d086b7d89b427a2e63` |
| `point-of-view@1.0.0` | `c18f0af1-d4f3-4728-bdb0-8ee003244d79` | `b8fb429fe54bc7a7c14ed1ad86ad0eb048ac1402c3e53ea946b2b664921e5406` |
| `story-setting@1.0.0` | `1d97fec4-9c91-40a8-bd01-0230e24a46d5` | `b675af06581b2371e56488e3211565cf621dd40d7e073f3d6b9f7d076193cf9c` |
| `story-timeline@1.0.0` | `8f450525-cdda-4e00-9a10-441f6143d7eb` | `aa31ebd4269075f5fbc33bfc6e0cc0f6dabbb5c994ac4a146102433955c9b76e` |
| `character-relationships@1.0.0` | `f4f95171-53d3-45ed-b0cf-b2d5d94a08cb` | `6a911b7b6598439ebc01d92239697af391d4325b75a4976b0258294e704b2c86` |
| `emotion-dramatic-intent@1.0.0` | `f35c47d7-17f8-4f51-a7d5-622e79756576` | `7a35c0e5302efbb1adb8654878bb365cca8f03cd79fbf20a59d578a89f01a757` |
| `story-continuity@1.0.0` | `bd07ba22-5efd-4062-b853-f848bf55321c` | `4f191a138c52efd9e988571c78fe464e255679c7a55d9a354eaf6764cefb5aff` |
| `analysis-synthesis@1.0.0` | `875b37e2-4315-466d-8337-6b6eb4d9aa3f` | `f4e7e639bb6770e3f7287748cf3dd88ca140506c63aa3718d23ce4bb1a61da25` |

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
| Analysis run/job | `963ed125-ea6d-4ad2-b787-f037b2cb1704` / `0648a1d5-7149-40b9-bc79-bf1ec37e5716`, succeeded |
| Input fingerprint | `0df10ce37649dd291e3bb8f3a45a06f46bf2513154d59c04573335a65800ea69` |
| Run fingerprint | `2c6c09f41e1be1343b5c1a31a42fffdd90d16ee7def5dc287e1a6a1d16d02951` |
| Snapshot ID/revision | `2674c3c1-0c80-4352-a000-5260cce6f038` / 1 |
| Snapshot fingerprint | `95a364ce0ba7f1e3084054c58e51648fc54590f9072c9e1e9723be9b6a72eb5b` |
| Correction-set fingerprint | `d701b4c808234e388a3e88e11d6b9e6cd9ddbab5929332bf747eaa90214bcc6f` |

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
| Character identity | `5aadef85-8638-4259-9074-e203c4e87c57` / `e0a4d1b4-985f-5fe6-a26a-74416ac6fcfb` | `03a46ecf3de8309e93d569193c8b9418c7194ced2db37e2fff9178edd425e48f` | `3a5d293b736095316b45a643d1850e6ebde7a4321445436efe9384da304d3f26` | `b42b6091d0bde37b4dd15f99a321e86dd965f2272a4ca68da6703b6f5ba2f0da` |
| Dialogue speaker | `9efca9e6-a6a0-4b1b-bea9-971bf242d061` / `9f409df4-1940-5377-b6e0-1440461f4590` | `fa9c99a6ec97d20f9f8a06adb0f615fa58cfcb7891f53f324b96a1255ea77238` | `e6a686653e220b4b0e4515da842cba13ab7cf4eb243acc3b99cdb51f48003d64` | `6db94e679f663d3dfbed36c173eb8445d6a364beb114c7f8c070e30983247300` |
| Continuity disposition | `ad29281e-b168-4e52-aef3-78c8097339c0` / `2603bd5e-a0cc-5db4-8dc3-af5e752095ab` | `87336a8d7b77f466739d64b47be65c670b5e386e7f5adbcb709455601344d6ef` | `f815e99fe085dfd5945658903db6bfb3c227bb2e003e6bb9999bbac79ad662bc` | `d7497ae908f34096c6bbbfdcbf7ea8287967882880cd229af078eee2383c2554` |

The Phase 0 bridge reason is intentionally not serialized into the Phase 2
manifest. Its correction record, effective speaker, analysis, and approval
state were compared before and after restart.

All four decisions were immutable, approved, and byte-identical in their
recorded fingerprints after restart:

| Gate | Review / decision ID | Artifact fingerprint | Evidence fingerprint | Decision-record fingerprint | Before/after |
| --- | --- | --- | --- | --- | --- |
| `story_structure_review` | `3b9efb61-3033-5234-90cd-bb5b057ca8d6` / `001439b5-5091-4e54-86b6-38bd10e919af` | `117729e8c06caba4c609e792b36c5304612fc0e3738967f6aea641e6d4330029` | `117729e8c06caba4c609e792b36c5304612fc0e3738967f6aea641e6d4330029` | `121757e245db8f4453d67ab2452354a561359913c147206a88dff1b34d3286ca` | Approved and identical |
| `character_registry_review` | `a5b3299a-b107-52e9-921b-92ce91b23329` / `5a8d9035-7e91-4c74-9dcb-2c9a682153a8` | `85e479efce8060379c990898598c5be89e45ed334f5200275cc6339cb13e31a4` | `85e479efce8060379c990898598c5be89e45ed334f5200275cc6339cb13e31a4` | `c2e2867c686107c2c4406ae35259509cc836e67fee78f5bacebe4b7bba55f9bb` | Approved and identical |
| `dialogue_attribution_review` | `46fbe0a6-e6d7-5d99-aa8e-140a605b3ac4` / `de9598aa-c8d5-475a-8c0e-4f59c89170a0` | `e03cf2e6eaeea6a8c229f4096e748bd82c2c7263227368ede8c45ae39bb6d14b` | `e03cf2e6eaeea6a8c229f4096e748bd82c2c7263227368ede8c45ae39bb6d14b` | `454ef6039d174d2b20be8cf125f1331c2df5297c6a82ca8a4baf2d58ddd530f5` | Approved and identical |
| `whole_book_analysis_review` | `f7530fc9-d9ef-5a45-ab3a-86f0e8ff8a95` / `5301fc16-5bda-48f0-9f26-e1269ca868e1` | `7a726836238923834268e4780edd7bd594ed6b2924b613937c496a3fb057bca1` | `7a726836238923834268e4780edd7bd594ed6b2924b613937c496a3fb057bca1` | `6196829fcd2f3946c29efb03989fb8229ac0a470bbe33146752ca51d80bd4557` | Approved and identical |

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
| 1 | 5072 / 2096 | 332, 1292, 2096, 3100 | 2252, 4076, 9424 | All seven exited gracefully; forced `[]`; remaining `[]` |
| 2 | 5348 / 1492 | 1492, 5332, 8292, 9044 | 6932, 8212 | All six exited gracefully; forced `[]`; remaining `[]` |

The exact owned identities were:

| Launch | PID | Parent PID | Kind | Invariant creation time |
| --- | ---: | ---: | --- | --- |
| 1 | 2096 | 5072 | app | `2026-07-30T23:13:24.2901960Z` |
| 1 | 3100 | 2096 | app | `2026-07-30T23:13:24.4396500Z` |
| 1 | 332 | 2096 | app | `2026-07-30T23:13:24.4448670Z` |
| 1 | 1292 | 2096 | app | `2026-07-30T23:13:24.4758040Z` |
| 1 | 4076 | 2096 | service | `2026-07-30T23:13:24.6662990Z` |
| 1 | 2252 | 4076 | service | `2026-07-30T23:13:25.4073430Z` |
| 1 | 9424 | 2252 | service | `2026-07-30T23:13:28.3533600Z` |
| 2 | 1492 | 5348 | app | `2026-07-30T23:13:46.4970490Z` |
| 2 | 8292 | 1492 | app | `2026-07-30T23:13:46.6339020Z` |
| 2 | 9044 | 1492 | app | `2026-07-30T23:13:46.6381300Z` |
| 2 | 5332 | 1492 | app | `2026-07-30T23:13:46.6858940Z` |
| 2 | 8212 | 1492 | service | `2026-07-30T23:13:46.8611230Z` |
| 2 | 6932 | 8212 | service | `2026-07-30T23:13:47.4238530Z` |

Shutdown required two post-close absence observations for every exact owned
PID. Ownership was established, both launches completed, cleanup completed,
and no unrelated process was terminated.

## Build-evidence manifest

The manifest was generated at `2026-07-30T23:13:55.073Z` by
`GitHub Actions 1000001491`, Windows X64, GitHub-hosted, workflow run
`30588967280`, attempt 1, job `verify-and-build`.

| Artifact | Relative path | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| Desktop application | `apps/desktop/release/0.1.0/win-unpacked/Cinematic Story Studio.exe` | 225,613,824 | `4b50888ddab6327f329f2a53c732b5c79de4ea40e3b9bf5aaa16d0760d69a7a6` |
| Staged service | `apps/desktop/build-resources/service/cinematic-story-service.exe` | 27,132,642 | `8e19520876a0b81f34d375b50a06cbb17456fb42ec73eeee7667932a8007f772` |
| Embedded service | `apps/desktop/release/0.1.0/win-unpacked/resources/service/cinematic-story-service.exe` | 27,132,642 | `8e19520876a0b81f34d375b50a06cbb17456fb42ec73eeee7667932a8007f772` |
| Packaged result | `apps/desktop/release/0.1.0/packaged-e2e-result.json` | 22,667 | `ef6eb17ec2e9e70f2b28f7e7f380981d1b4a58477c3a380e59ec332358a537e0` |
| Packaged screenshot | `apps/desktop/release/0.1.0/packaged-e2e.png` | 111,406 | `4493833d034c47cdfa525e9411075651060250a0f9f4848e3b4c36ec5f6e45be` |
| Manifest after download | `apps/desktop/release/0.1.0/build-evidence.json` | 34,905 | `4be79d50c607bf19ed6a0b3e803e553bffdbc538efbf3f7c544a8e720a0b11b8` |

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

At the code artifact head, Dependency Review run/job `30588968960` /
`91026821074` succeeded with no vulnerable package at moderate-or-higher
severity, no denied package, and an empty dependency-change report. Its scope
is the pull-request dependency delta, not every transitive package in every
ecosystem.

Security push run/job `30588967284` / `91026815933` and pull-request run/job
`30588968995` / `91026821214` both succeeded. They scanned 263 tracked files
and 33 commits with checksum-pinned Gitleaks `8.30.1`, finding no leak.

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
