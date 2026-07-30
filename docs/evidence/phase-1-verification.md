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
| Commit | To be filled from the pushed review revision |
| Application version | `0.1.0` |
| Verification date | 2026-07-30 UTC |
| Windows CI run/job | To be filled from GitHub Actions |
| Artifact ID/digest | To be filled from GitHub Actions |

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
| Tracked-content scan | `pnpm scan:tracked` | Pending final revision |
| Clean-tree check | `node scripts/git-check.mjs --clean` | Run only after intended changes are committed |
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
| Workflow head SHA / tested checkout SHA | Pending |
| Backend Pytest passed / skipped / warnings | Pending |
| FFmpeg-dependent GitHub result | Pending |
| Secure-ingest fixture/schema/tooling gate | Pending |
| Development Electron E2E | Pending |
| Packaged-E2E step outcome | Pending |
| Import Review proof | Pending |
| Launch 1 owned app/service PIDs and exit | Pending |
| Launch 2 owned app/service PIDs and exit | Pending |
| Build-evidence manifest generation | Pending |
| Tracked scan / clean checkout | Pending |
| Artifact ID / digest / retention | Pending |

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
