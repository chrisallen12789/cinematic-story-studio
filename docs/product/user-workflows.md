# User Workflows

## 1. First launch and service startup

1. The user installs and launches Cinematic Story Studio from Windows.
2. Electron main starts the owned local service with a fresh per-launch secret and no shell window.
3. The service binds `127.0.0.1` on an OS-assigned port, opens/migrates storage, and reports readiness over its inherited control channel.
4. Electron authenticates `GET /api/v1/health`. The renderer sees `Starting`, then `Ready`; it never receives the secret.
5. The last project opens if it still exists. Otherwise the project chooser opens.

If startup fails, the application shows a redacted reason, log location, and `Retry startup`. It remains possible to open diagnostics or safely exit. It never instructs a packaged user to start FastAPI manually.

## 2. Create or reopen a project

1. From the project chooser, the user creates a project with a non-empty display name or selects an existing one.
2. Creation is transactional and returns a project identifier and revision.
3. The workspace shows import, story outline, cast, jobs, providers, and settings sections appropriate to current state.
4. The most recently opened project is recorded only after it opens successfully.

Deleting a project shows the affected project, local source/cache/render scope, and whether user-exported files are excluded. Confirmation removes application-managed data and credential references associated only with that project; it does not delete arbitrary exports.

## 3. Import and review a story

1. The user chooses a file through the native picker.
2. Electron main streams the selected file to the service; the renderer cannot submit an arbitrary local path.
3. The service validates declared/observed type, configured size, extraction limits, and hash. TXT/Markdown decoding is explicit and lossless; unsupported encoding is a reviewable error.
4. The UI shows filename, format, size, digest, and an import preview without logging its content.
5. The user confirms import. An analysis job is created against that immutable story revision.
6. The outline incrementally shows durable analysis results. Partial results are labelled with their job/agent state and are not treated as approved.
7. Import review and later review gates record the exact artifact revision.

An identical request carrying the same idempotency key returns the original result. A failed import removes staging files and leaves the previous revision active.

## 4. Inspect and correct dialogue

1. The user selects a chapter and scene.
2. The UI displays narration and dialogue in source order, with characters, confidence, warning indicators, and source locations.
3. The user selects a dialogue line, chooses an existing character (or creates one through a separate typed action), and optionally records a reason.
4. Save includes the line's expected revision. The service atomically writes a correction event, updates the current attribution projection, increments revisions, and returns the durable value.
5. The UI marks the line as human-corrected and shows provenance.

If another edit won, the service returns `409 REVISION_CONFLICT` with the current revision. The UI preserves the user's unsaved choice and offers comparison/reapply; it never overwrites silently. Re-analysis reads corrections as protected constraints.

## 5. Observe, cancel, retry, and resume work

1. Creating analysis or render work returns a durable job before execution begins.
2. The jobs panel consumes typed job events (with polling as a fallback) and displays state, stage, progress, attempts, and warnings.
3. `Cancel` changes the durable state to `cancel_requested`; the worker acknowledges at a safe point and finishes as `cancelled`.
4. `Retry` on a failed job creates another attempt under the same job identity, preserving the earlier error and events.
5. `Resume` on an interrupted job validates the checkpoint and current input revision. It continues when compatible; otherwise the UI explains that a clean retry is required.
6. Restarting the desktop turns abandoned work into `interrupted` and offers the same choices.

Closing the app while work is active asks whether to cancel, leave resumable work interrupted, or keep the app open. Phase 0 does not run jobs after the desktop exits.

## 6. Configure providers and local tools

1. The providers screen shows capability and health without exposing secret values.
2. A credential is entered into an OS-protected flow and represented in the project/app database only by an opaque reference.
3. A local or cloud adapter can be enabled, disabled, and health-checked independently.
4. Before the first cloud operation for a content category, the user sees what data will leave the machine, provider identity, purpose, and policy/retention disclosure, then approves or cancels.
5. Revoking a credential changes health to `unauthorized`; local project browsing continues.

Development Docker Kokoro may report `unavailable` with setup guidance. Packaged users never need Docker. FFmpeg health distinguishes `available`, `missing`, `incompatible`, and `failed` and records the detected version/path category without exposing a personal path.

## 7. Review production and render

1. The user completes required gates for structure, characters, attribution, casting, direction, and sound design.
2. The application snapshots approved revisions into a render manifest and shows blockers before accepting the job.
3. The job resolves or generates hashed inputs, lays out a deterministic timeline, mixes and validates, then atomically publishes artifacts.
4. The user reviews the first scene, then chapter outputs, then the final master at explicit gates.
5. Export writes to a user-selected location without changing the immutable application artifact.

Cancelling or failing leaves no file presented as a finished master. A rerun using identical deterministic inputs yields the same manifest identity; non-deterministic provider output is captured and hashed as an input artifact.

## 8. Recover, clean up, and diagnose

- On a service crash, Electron reports loss of connection, restarts at most a bounded number of times, and then offers a manual retry. Durable data remains authoritative.
- On low disk space, work stops before publication and reports required/available space without deleting source data.
- Cache cleanup previews scope, skips artifacts referenced by active jobs, and is safe to repeat.
- Diagnostics export is opt-in and redacted by construction. It contains versions, states, codes, and identifiers—not story text, prompts containing story text, keys, audio, or personal paths.
- Database recovery never edits the sole copy in place. The application first creates a timestamped local backup and records the migration/recovery result.
