# Phase 0 Vertical-Slice Acceptance Tests

## Test contract

These checks are the completion gate for the first connected slice. Automated tests use an isolated application-data directory, an ephemeral SQLite database, a fixed development token, no cloud credentials, no downloaded models, and only the repository's synthetic fixture. Network access is denied except loopback.

The canonical fixture is `fixtures/synthetic-story/sample-story.md`. It must be original synthetic content, contain at least one Markdown chapter heading, a scene boundary, narration, two named characters, and two quoted dialogue lines, and be small enough for source review. Tests derive expected text and SHA-256 from the fixture bytes rather than duplicating private content.

All IDs are opaque strings. Timestamps are UTC RFC 3339. API errors use the documented typed envelope. UI and API checks must not rely on execution timing alone; workers expose controllable test barriers.

## Required behavior

| ID | Setup and action | Exact pass criteria | Primary level |
| --- | --- | --- | --- |
| VS-001 | Launch the unpackaged desktop with no service running. | Exactly one owned service process starts without a visible shell; it binds an OS-assigned `127.0.0.1` port, authenticated health reaches `ready`, and the UI shows `Backend ready`. | Playwright/manual Windows |
| VS-002 | Call an authenticated service route without a token and with a wrong token. | Both return `401`; no project data is returned; the correct per-launch/dev token succeeds. A non-loopback bind configuration is rejected at startup. | Pytest integration |
| VS-003 | Start with an empty database and create project `Synthetic Demo` twice using one idempotency key. | Both successful responses have the same project ID; only one project row exists; name is `Synthetic Demo`, revision is at least `1`, and status is `empty`. | Pytest/API |
| VS-004 | Import the canonical fixture into that project twice with one import idempotency key. | One immutable source/story revision is created; stored bytes hash equals the file SHA-256; returned format is `markdown`; extracted text is character-for-character equal; no absolute source path is persisted. | Pytest/API |
| VS-005 | Submit an executable, oversized file, unsupported extension, and malformed text. | Each is rejected with `400` or `413` and a stable error code; no source/story revision or staging file remains; the service remains healthy. | Pytest/security |
| VS-006 | Run `analyze_story` for the imported revision. | The terminal job state is `succeeded`; project detail contains at least one chapter, one scene, narration, two dialogue lines in source order, and two characters; every derived object has source/provenance and revision data. | Pytest/integration |
| VS-007 | Open the analyzed project in the UI. | Chapter and scene navigation is keyboard operable; selecting the first scene displays its narration/dialogue in source order and the detected character list; loading and empty states are not shown as success. | Vitest/Playwright |
| VS-008 | Inspect each automated dialogue attribution. | It exposes `characterId` or `null`, confidence in `[0,1]`, warnings, method/agent version, and source span. An unresolved case is displayed as uncertain, never coerced to a character. | Contract/component |
| VS-009 | Correct one dialogue line to the other fixture character with its current revision and reason `fixture correction`. | API returns the selected character and `source=human`; a provenance event stores prior/new IDs, reason, actor, timestamp, and revision; project and line revisions advance exactly once. | Pytest/API |
| VS-010 | Repeat VS-009 with the stale pre-correction revision. | Response is `409 REVISION_CONFLICT` and includes current revision; the saved character and provenance are unchanged. | Pytest/API |
| VS-011 | Fully stop desktop/service and reopen the same data directory/project. | The imported hash, chapter/scene structure, human-corrected speaker, correction reason/provenance, and project revision match the pre-restart values. | Playwright/integration |
| VS-012 | Create an analysis job while its test worker is held before execution. | Creation returns state `queued`; release moves it through `running` to `succeeded`; events have strictly increasing sequence numbers; progress is bounded `[0,1]`, never decreases within the attempt, and ends at `1`. | Pytest/API/UI |
| VS-013 | Hold a running job at a cancellation point and request cancel twice. | Both requests are safe; state becomes `cancel_requested`, then `cancelled`; no result is published, temporary output is removed, and the worker can execute a later job. | Pytest/integration |
| VS-014 | Cause a deterministic test failure, then retry. | First attempt is `failed` with a redacted stable error; retry increments `attempt` by one, preserves attempt-one events/error, and can reach `succeeded` without a duplicate job row/result. | Pytest/integration |
| VS-015 | Stop the service after a durable checkpoint while a job is `running`, then restart and resume. | Startup changes it to `interrupted`; resume verifies input/checkpoint versions, increments attempt, continues after the checkpoint, and succeeds. An intentionally incompatible checkpoint is rejected with `CHECKPOINT_INCOMPATIBLE` and remains inspectable. | Pytest/integration |
| VS-016 | Subscribe to job events, disconnect, and reconnect after the last sequence; also disable streaming. | Reconnect returns only later events in order; polling returns the same final state. The jobs UI accurately presents queued/running/cancelled/failed/interrupted/succeeded. | Contract/component |
| VS-017 | Open casting after analysis. | One typed row appears per detected character with `unassigned` status and no fabricated provider/voice ID; unavailable providers do not prevent rendering the panel. | Vitest/component |
| VS-018 | Query provider health with no cloud credentials and no Kokoro container. | Response is successful and typed; cloud adapters are `disabled` or `unauthorized`, development Kokoro is `unavailable`, each has a redacted reason/capabilities, and project APIs still work. | Pytest/component |
| VS-019 | Query FFmpeg capability in environments with and without the configured test binary. | With the controlled binary it reports `available` and parsed version/capabilities; without it reports `missing`/`unavailable`; neither case crashes startup or executes a shell string. | Pytest/integration |
| VS-020 | Make the backend unavailable while the renderer is open, then restore it. | UI shows a non-success disconnected state, disables mutating controls, preserves unsaved form state, and returns to ready after authenticated reconnection. | Vitest/Playwright |
| VS-021 | Attempt imports with traversal filenames/archive members and subprocess metacharacters. | Input is rejected or names are safely canonicalized inside staging; no file escapes its root; subprocess test captures an argument array with no shell invocation. | Pytest/security |
| VS-022 | Collect logs and diagnostics across import, correction, job failure, and provider health. | They contain correlation/project/job IDs and stable codes but no fixture passages, bearer/dev token, credential value, raw request body, audio, or absolute user path. | Automated scan |
| VS-023 | Run cache cleanup during no active jobs and while an artifact is leased. | Unreferenced application cache is removed; leased/source/database/export files remain; repeating cleanup succeeds with no additional destructive effect. | Pytest/integration |
| VS-024 | Build the desktop and local service in development configuration. | Root build exits `0`, creates the expected renderer/main artifacts and bundled-service staging artifact, and a smoke test can start the artifact on Windows. This does not assert a signed installer. | CI Windows |
| VS-025 | Inspect all tracked/staged files and produced test artifacts. | No private manuscript, credential, `.env` value, project database, generated audio, model, cache, personal path, or extracted prototype temp directory is tracked. Secret scan exits `0`. | CI/review |

## Concrete Phase 0 service contract

All routes are under `/api/v1`, require `Authorization: Bearer <launch-token>`, accept/return JSON unless the import is multipart, and return a correlation ID. Required routes:

| Method and route | Minimum typed behavior |
| --- | --- |
| `GET /health` | Returns service/contract version, instance ID, `starting|ready|degraded`, database state, and UTC time. |
| `GET /providers/health` | Returns every registered adapter's kind, status, capabilities, optional version, redacted reason, and checked time. |
| `GET /capabilities/ffmpeg` | Returns `available|missing|incompatible|failed`, sanitized executable origin, version, and required capabilities. |
| `GET /projects` | Returns paginated project summaries in stable updated/name/ID order. |
| `POST /projects` | Accepts `{name}` and idempotency key; returns the durable project. |
| `GET /projects/{projectId}` | Returns a `ProjectDetail` with source/story revision, chapters/scenes/lines/characters, casting placeholders, approvals, and relevant jobs. |
| `POST /projects/{projectId}/imports` | Streams multipart `file` plus optional declared format; validates before transactional publication and returns immutable source/story metadata. |
| `PUT /projects/{projectId}/dialogue-lines/{lineId}/speaker` | Accepts `{characterId, reason?, expectedRevision}` and returns corrected attribution plus provenance/revisions. |
| `POST /projects/{projectId}/jobs` | Accepts `{type, inputRevision, idempotencyKey}`; returns a persisted job (`analyze_story` required). |
| `GET /jobs/{jobId}` | Returns current state, attempt, stage/progress, checkpoint availability, timestamps, warnings, and redacted error. |
| `GET /jobs/{jobId}/events?afterSequence=N` | Returns ordered events or an authenticated event stream with equivalent schema. |
| `POST /jobs/{jobId}/cancel` | Idempotently requests cancellation. |
| `POST /jobs/{jobId}/retry` | Valid only for terminal recoverable failure; records a new attempt. |
| `POST /jobs/{jobId}/resume` | Valid only for interrupted/paused work with a compatible checkpoint. |

Unknown JSON fields are rejected at write boundaries in Phase 0. Errors are:

```json
{
  "error": {
    "code": "REVISION_CONFLICT",
    "message": "The dialogue line changed; refresh and compare.",
    "retryable": false,
    "correlationId": "opaque-id",
    "details": {"currentRevision": 4}
  }
}
```

`details` is allow-listed and never contains source content, credentials, provider prompts/responses, or absolute paths.

## Verification commands

From a clean checkout on Windows, all applicable commands must exit `0`:

```text
pnpm install --frozen-lockfile
pnpm lint
pnpm typecheck
pnpm test
pnpm build
```

Python lint, type checks, and Pytest may be invoked by those root commands and separately in CI. The end-to-end run must use a temporary app-data directory and remove it after evidence is collected. Manual launch evidence must state commit, Windows version, build command, observed port address (not token), service/UI states, and whether shutdown left an owned child process.
