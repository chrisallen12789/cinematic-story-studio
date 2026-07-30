# Contributing

Changes must preserve the local-first trust boundaries, typed contracts,
immutable original sources and derived extraction revisions, durable human
decisions/corrections, reproducible build inputs, and the rule that Electron
owns one authenticated loopback service process.

## Before editing

1. Read `AGENTS.md` and the closest narrower instructions.
2. Read the relevant product, architecture, security, decision, agent, and audio documents.
3. State which acceptance checks in `docs/product/vertical-slice-acceptance-tests.md` are affected.
4. Inspect `git status --short`. Preserve unrelated work and never use a destructive reset to clear another contributor’s changes.
5. Use only synthetic public content. Never copy a real manuscript, credential, database, generated audio file, model, cache, log, or personal path into the repository.

## Development environment

Use 64-bit Windows, Node.js 24, pnpm 11.9.0, and Python 3.12 with `venv` and `pip`. pnpm and Python must be available directly on `PATH`; neither Corepack nor `uv` is required.

```powershell
pnpm install
pnpm lint
pnpm typecheck
pnpm test
pnpm build
```

The install hook creates `apps/local-service/.venv`, installs the exact,
hash-verified transitive set in `apps/local-service/requirements.lock`, installs
the local package without resolving a second dependency graph, and runs
`pip check`. Direct pins in `requirements.in` stay aligned with
`pyproject.toml`; regenerate the lock from Python 3.12 after an intentional
dependency review:

```powershell
pnpm service:python -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 'Lock generation requires Python 3.12.x')"
pnpm service:python -m piptools compile --allow-unsafe --generate-hashes --strip-extras --output-file apps/local-service/requirements.lock apps/local-service/requirements.in
```

Each Python process is started with an argument array:

```text
python -m pip install --disable-pip-version-check --require-hashes --requirement apps/local-service/requirements.lock
python -m pip install --disable-pip-version-check --no-build-isolation --no-deps -e apps/local-service
python -m pip check
```

The command runs with the repository as its working directory and passes every value as a separate argument, so spaces in the checkout path are safe. No shell string is used. Dependencies belong in the owning package manifest, need a documented product or development purpose, and must be maintained and narrowly scoped. Commit the resulting `pnpm-lock.yaml`; do not hand-edit it.

`pnpm build` performs these ordered steps:

1. Compile `@cinematic-story-studio/contracts`.
2. Run the pinned PyInstaller spec in one-file mode by default, smoke-test `--help`, and stage `cinematic-story-service.exe` under `apps/desktop/build-resources/service`.
3. Build the renderer, main, and preload code.
4. Run electron-builder with `--dir`.
5. Verify the staged service at
   `apps/desktop/build-resources/service/cinematic-story-service.exe`, the
   desktop executable under `apps/desktop/release/<version>/win-unpacked`, and
   the embedded service at
   `apps/desktop/release/<version>/win-unpacked/resources/service/cinematic-story-service.exe`.

For a diagnostic one-directory PyInstaller build, set `CSS_PYINSTALLER_MODE=onedir` only for that command. Both modes stage the same executable name and are ignored by Git.

The service keeps standard input/output for its bounded bootstrap record and readiness line. Electron starts it with `windowsHide=true`, so the owned console-subsystem executable does not show a shell window.

## Python helper

Normal development uses `pnpm dev`; do not start another service beside the Electron-owned child. Run bounded backend tools through the prepared cross-platform interpreter:

```powershell
pnpm service:python -m pytest apps/local-service/tests
pnpm service:python -m ruff check apps/local-service
```

The helper resolves `apps/local-service/.venv/Scripts/python.exe` on Windows and the corresponding `bin/python` on other development hosts, then spawns an argument array with `shell=false`. The application service itself is always bootstrapped by its owning Electron parent over inherited standard input. Never place a launch token in an environment variable, command-line argument, URL, committed `.env`, screenshot, issue, or log.

## Required safeguards

Before every commit:

```powershell
pnpm scan:tracked
pnpm scan:staged
pnpm precommit
git status --short
```

The repository scan checks prohibited paths, private-content markers, personal paths, common secret formats, databases, source documents, generated audio, dependency trees, and build/runtime output. It reports only file, line, and rule; matched values are never printed. `pnpm precommit` scans the exact staged blobs and runs a redacted `git diff --cached --check`.

Only `.gitkeep` is tracked in `local-projects`, `local-models`, `local-cache`,
and `local-renders`. Their other contents are ignored and must remain local.
Automated ingest tests may use only the compact public synthetic fixture family
under `fixtures/synthetic-story/`; binary DOCX, EPUB, and PDF fixtures are
generated deterministically from that source and committed only as base64 text.

CI repeats lint, type checks, tests, development Electron E2E, the Windows
build, exact-artifact packaged E2E, build-evidence validation, tracked-content
scanning, Gitleaks history scanning with redaction, and dependency review on
supported pull requests. CI uploads a seven-day development artifact and never
publishes a release.

## Manual verification record

Automated success does not establish desktop lifecycle behavior. Attach a record with every Phase 0 vertical-slice claim:

```text
Commit:
Windows edition/version/build:
Node, pnpm, and Python versions:
Install command/result:
Lint/typecheck/test/build result:
Unpacked artifact path:
Observed bind address (address and port only; no token):
Service states observed:
UI states observed:
Synthetic fixture import/hash result:
Uncertain attribution and correction result:
Restart persistence result:
Owned child exited after desktop shutdown:
Residual process check:
Limitations or skipped checks:
```

Use the sequence in `README.md` and the exact pass criteria in `docs/product/vertical-slice-acceptance-tests.md`. Never attach application data, request bodies, story excerpts beyond the committed synthetic fixture, raw logs, or tokens as evidence.

## Rollback

The application has no updater and does not support an in-place database
downgrade. Phase 1 retains a verified schema-v1 backup before migration. Roll
back code and artifacts without mutating the current worktree or opening
schema-v2 data with an older application:

1. Close every Cinematic Story Studio window and confirm only the service process owned by that window has exited.
2. Record `git status --short`; do not discard unrelated changes.
3. Copy the exact `%LOCALAPPDATA%\Cinematic Story Studio` data root to a separately protected backup location. Do not add that copy to this repository.
4. Create a sibling worktree at the reviewed last-known-good commit:

   ```powershell
   git worktree add ..\cinematic-story-studio-rollback <known-good-commit>
   Set-Location ..\cinematic-story-studio-rollback
   pnpm install --frozen-lockfile
   pnpm lint
   pnpm typecheck
   pnpm test
   pnpm build
   ```

5. Test the known-good unpackaged artifact with a new temporary application-data directory. Do not point it at data already opened or migrated by a newer build.
6. To recover a real project, use a backup created by the matching application/database version. Keep the newer data copy until integrity and expected revisions are verified.
7. Remove the rollback worktree from the original checkout only after evidence is retained:

   ```powershell
   git worktree remove ..\cinematic-story-studio-rollback
   ```

If only dependency preparation failed before any application data was opened, remove the exact generated `apps/local-service/.venv` and root `node_modules` directories from the repository checkout, then rerun `pnpm install`. Never delete `local-projects`, user-selected exports, or `%LOCALAPPDATA%` as a dependency rollback step.

## Change control

Keep commits focused and use conventional commit messages. Do not commit or publish generated artifacts. Do not force-push, rewrite shared history, merge your own pull request, enable auto-merge, publish a release, or add signing/provider credentials to CI. There is no repository license; do not add one without an explicit owner decision.
