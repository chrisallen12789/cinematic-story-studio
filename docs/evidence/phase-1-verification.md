# Phase 1 verification record

## Evidence policy

This file distinguishes commands run locally from checks run by GitHub Actions.
A check is not marked passed until its exact command or workflow job completed
for the recorded commit. Local results never stand in for the exact
CI-produced service/application artifact, and a GitHub result never implies a
developer-only E2E was run unless the workflow contains that gate.

Only public synthetic files under `fixtures/synthetic-story/` may be imported
document/manuscript test content. Machine-readable evidence records hashes,
byte counts, versions, states, stable codes, runner identity, and owned process
identities; it excludes source excerpts, absolute personal paths, tokens,
databases, audio, models, and generated application binaries. The short-lived
packaged UI screenshot may visibly contain only the public synthetic preview;
it must never contain private manuscript content.

## Revision under verification

| Field | Value |
| --- | --- |
| Branch | `codex/phase-1-secure-ingest` |
| Code/artifact source commit | `7405d5daa0b917cd60e8ab6709d3fc73e232358b` |
| Application version | `0.1.0` |
| Verification date | 2026-07-30 UTC |
| Windows CI run/job | `30513800598` / `90779170857`, successful |
| Artifact ID/digest | `8748186578` / `sha256:d4ea8a45e9604cb54c4c9d142697690b011cb8c39d01fcf01945e1bd906fca85` |

The source commit above is the code and exact artifact-producing revision.
This record's evidence-closure commit changes only this documentation; its
final-head workflows must also pass before handoff.

## Locally executed checks

Record command, environment, exact pass/fail/skip count, and any warning. Do not
copy a GitHub count into this table.

| Check | Command | Result |
| --- | --- | --- |
| Frozen dependency install | `pnpm install --frozen-lockfile` | Passed; all three workspace projects were already current and no tracked file changed |
| Lint | `pnpm lint` | Passed helper syntax, ESLint with zero warnings, Ruff, the tracked-content scan, and diff checks |
| Type checks | `pnpm typecheck` | Passed both TypeScript projects and strict mypy across 18 service source files |
| Repository/schema/tooling/service/desktop tests | `pnpm test` | Passed: 27 repository/schema/tooling, 154 backend, and 71 desktop tests; 252 total, 0 skipped, 1 warning |
| Windows service and desktop build | `pnpm build` | Passed the PyInstaller service smoke test and unpacked Electron build |
| Tracked-content scan | `pnpm scan:tracked` | Passed across 191 tracked files without printing matched values |
| Clean-tree check | `node scripts/git-check.mjs --clean` | Passed after the intended source commits |
| Development Electron E2E | Set `CSS_E2E=1`; run `pnpm --filter @cinematic-story-studio/desktop exec playwright test tests/e2e/persistence.spec.ts` after `pnpm build` | Passed: 1 development persistence test; the packaged-only test was explicitly skipped |
| Packaged Electron E2E against local build | Set `CSS_PACKAGED_E2E_EXECUTABLE`, `CSS_PACKAGED_E2E_EVIDENCE_PATH`, and `CSS_PACKAGED_E2E_RESULT_PATH` to the exact built release paths; run `pnpm --filter @cinematic-story-studio/desktop run test:e2e:packaged` | Attempted but did not launch: local Device Guard policy `0283ac0f-fff1-49ae-ada1-8a933130cad6` blocked the unsigned development executable at Enterprise signing level; the fail-closed result records no owned launch, no screenshot, and completed cleanup |

FFmpeg-dependent tests must be reported separately. If FFmpeg is locally
installed, record that local-only test result and version/capability. Its
presence does not change the GitHub backend count when the GitHub runner skips
the test.

The local backend suite reported 154 passed, 0 skipped, and one upstream
`StarletteDeprecationWarning` about the `httpx`/Starlette TestClient
compatibility path. Its explicit FFmpeg loudness test ran and passed with
`ffmpeg version 8.1.2-full_build-www.gyan.dev`; that local result must not be
quoted as a hosted-runner result.

## GitHub Actions checks

The required `Phase 1 Windows CI / Lint, test, build, and packaged E2E` job:

1. checks out the workflow head without persisted credentials;
2. installs frozen Node and hash-locked Python dependencies;
3. asserts `lxml==6.1.1` and `pypdf==6.14.2`;
4. regenerates/validates public fixtures and schemas;
5. runs lint, type checks, repository/tooling/backend/desktop tests;
6. builds the staged service and exact unpacked desktop executable;
7. runs development Electron DOCX import/review/persistence E2E against the
   freshly built development output;
8. runs packaged DOCX import/review/persistence E2E against that executable
   with isolated `APPDATA`, `LOCALAPPDATA`, `TEMP`, and `TMP`;
9. records only owned Electron/service processes by PID, ancestry, executable
   identity, and creation time, then proves those PIDs exit after both launches;
10. generates schema-v2 build evidence, rescans tracked content, verifies the
   checkout is clean, and uploads a seven-day artifact;
11. fails the job if packaged E2E or evidence enforcement fails.

Fill the final run evidence without combining environments:

| GitHub check | Exact result |
| --- | --- |
| Workflow head SHA / tested checkout SHA | Both `7405d5daa0b917cd60e8ab6709d3fc73e232358b`; push-run PR head is correctly `null` |
| Backend Pytest passed / skipped / warnings | 153 passed, 1 skipped, 1 warning |
| FFmpeg-dependent GitHub result | Explicitly skipped because FFmpeg was not installed; all 153 other backend tests passed |
| Secure-ingest fixture/schema/tooling gate | 20 passed, 0 skipped; exact `lxml==6.1.1` and `pypdf==6.14.2` assertions passed |
| Development Electron E2E | 1 passed in 10.7 seconds |
| Packaged-E2E step outcome | Successful; 1 passed in 27.2 seconds against the exact unpacked executable |
| Import Review proof | DOCX source `024cdebffa5824a454ef5167be0bf147e5717f4ef107dadea5fa207927703d1e`; extracted text `9c4f7ae0261dd50d1064d2b9cf1ee40b1a46f89fa65485c096baafd84830c5d5`; extraction revision 1; 0 warnings; approval, extraction, analysis, and correction persisted after restart |
| Launch 1 owned app/service PIDs and exit | App 3816, 5008, 6404, 9140; service 3400, 5624, 7812; all seven exited gracefully, none forced or remaining |
| Launch 2 owned app/service PIDs and exit | App 6420, 7556, 9620, 9668; service 984, 7612; all six exited gracefully, none forced or remaining |
| Build-evidence manifest generation | Schema 2.0.0 passed every assertion at `2026-07-30T04:31:00.825Z` on GitHub-hosted Windows X64 runner `GitHub Actions 1000001458` |
| Tracked scan / clean checkout | Both passed after build and E2E |
| Artifact ID / digest / retention | ID `8748186578`; 426,208,065 bytes; digest `sha256:d4ea8a45e9604cb54c4c9d142697690b011cb8c39d01fcf01945e1bd906fca85`; expires 2026-08-06 |

## Dependency and vulnerability-audit scope

CI dependency evidence consists of frozen Node installation, hash-locked Python
installation, exact parser-version assertions, and GitHub dependency review for
pull requests. Dependency review fails at `moderate` severity. The separate
security workflow scans repository policy and Git history for secrets; it is
not described as a dependency audit. Dependency review is not equivalent to a
full ecosystem vulnerability scan.

If `pnpm audit` or a Python audit is run manually, record it as local-only with
the exact command, tool version, database/update time, severity threshold, and
result. Do not describe it as a CI gate unless a pinned, immutable CI tool and
explicit fail threshold are committed. No unpinned downloader or mutable
security scanner is introduced by Phase 1.

The following live-registry checks were run locally at approximately
2026-07-30T03:13:27Z and are not CI gates:

- pnpm 11.9.0 ran `pnpm audit --audit-level=moderate` against the locked Node
  dependency graph and reported no known vulnerabilities at or above the
  moderate threshold.
- pip-audit 2.10.1 ran
  `python -m pip_audit -r apps/local-service/requirements.lock` from a pinned,
  temporary environment outside the repository. It reported no known
  vulnerabilities; pip-audit applies no severity threshold and did not expose
  an advisory-database snapshot timestamp beyond the live query time.

Parser choice and license evidence is recorded in
`docs/decisions/ADR-0008-secure-document-parser-dependencies.md`.

## Build-manifest evidence

The uploaded `build-evidence.json` must contain:

- schema `2.0.0`, workflow/tested/PR head SHA, application version, UTC test
  time, and runner identity;
- repository-relative application, staged-service, embedded-service,
  screenshot, machine-result, and secure-ingest input paths with sizes/hashes;
- staged/embedded service equality;
- supported formats, exact parser dependency versions/licenses, separately
  named source/preview boundary limits, the exact canonical parser profile and
  its reproducible canonical JSON plus matching Python parser-profile
  fingerprint, and deterministic DOCX decoded size/SHA-256;
- the validated packaged-result file reference/hash, DOCX source/extracted
  hashes, extraction revision, warning count, approval and restart persistence;
- two-launch owned-process inventories and empty remaining-PID sets;
- assertions that harness/step agree, ownership exit is proven, DOCX Import
  Review is proven, and packaged evidence is complete.

The paired `packaged-e2e-result.json`, whose file evidence is bound into the
manifest, carries schema `3.0.0` and the exact ordered Phase 1 flow. Manifest
generation rejects a different schema or flow.

Generated manifests, screenshots, result JSON, service/application binaries,
and unpacked release trees remain ignored CI artifacts and are never committed.

For run `30513800598`, the manifest records:

- desktop executable: 225,613,824 bytes,
  SHA-256 `4e230f2273f736094d6dc577986b09c8fbd68e0bc1a385c0074f8836355ec9c1`;
- staged and embedded service: 26,916,144 bytes each,
  SHA-256 `cbd5f8f18213b120385c1bb17de66c4bb2279b70df578969d989a4325eaf9a67`,
  with equality asserted;
- packaged result JSON: 5,243 bytes,
  SHA-256 `2452ca9a42b502689301b43fce257d34989e20cfed33aa28d7d1689dea37282a`;
- packaged screenshot: 170,328 bytes,
  SHA-256 `e259bd0e36c382861d1d6eb4a5342033d876b5969607a586c2f12cbdb5b12931`;
- parser-profile fingerprint
  `3c9fef89ac411e84ef0ef8962b3d43ef3469d090035a537c9bf72c6d93cdd922`.

## Manual verification

Manual review complements automation and records observations, not unsupported
claims:

- use only the public TXT/Markdown/DOCX/EPUB/PDF fixtures;
- verify keyboard/focus/status behavior through Import Review;
- confirm warning, unsupported, encrypted, OCR-required, timeout, cancellation,
  stale-revision, and desktop-cap errors are actionable and content-redacted;
- restart after approval, analysis, and human correction and compare hashes,
  revisions, warnings, decisions, and correction provenance;
- inspect process evidence to ensure inventory stayed limited to the two exact
  relevant executable names; any preexisting matching-name identity was used
  only for exclusion, and no process was adopted or terminated by name;
- inspect the short-lived artifact manifest and match its executable/service
  sizes and hashes to the uploaded files.

## Known limitations

- No OCR, password handling, damaged-file recovery, layout-perfect conversion,
  macro/script execution, embedded media/attachment extraction, or network
  retrieval.
- Text-based PDF extraction quality varies with the document's text layer,
  reading order, fonts, and layout; low-quality or image-only input is warned
  or rejected rather than supplemented with invented text.
- Cloud parsing is disabled. Original sources remain private and local, Import
  Review approval is required before analysis, and audio production is not
  part of Phase 1.
- Each extraction runs in one parent-supervised spawned parser process with a
  hard 30-second wall-clock deadline, bounded typed IPC, exact target-PID
  ownership evidence, and cancellation/deadline termination before
  publication. Windows applies a named Job Object with kill-on-close and a
  768 MiB per-process memory ceiling to the launcher and actual parser target.
  This does not provide a low-integrity token, contain a native-library exploit,
  or enforce an OS outbound-network sandbox.
- The service source ceiling is 100 MiB, while the current desktop native
  transfer cap rejects UI import above 8 MiB.
- Builds are not claimed byte-reproducible across environments. Evidence proves
  the exact staged and embedded service match and tests the exact CI artifact.
- FFmpeg is unrelated to document parsing; an absent runner FFmpeg may leave its
  existing capability test skipped and must be reported explicitly.

## Rollback and data backup

Schema-v1 to v2 migration creates and verifies a retained SQLite backup before
mutation. On failure, preserve both files, do not edit the sole copy, and use a
copy of the verified version-1 backup with a compatible application. Parser or
UI rollback does not delete Phase 1 source/extraction/review history. A
changed-byte reimport, explicit re-extraction, or new human decision appends a
revision; it never rewrites a prior approved record. An identical import
reuses the current source/extraction.
