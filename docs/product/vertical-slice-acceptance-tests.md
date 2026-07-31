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
| VS-004 | Import the canonical fixture into that project twice with one import idempotency key and wait for its Phase 1 extraction job. | One immutable source/extraction/story revision is created; stored bytes hash equals the file SHA-256; detected format is `markdown`; extracted text is character-for-character equal; no absolute source path is persisted. | Pytest/API |
| VS-005 | Submit an executable, oversized file, unsupported extension, and malformed text. | Each is rejected with `400` or `413` and a stable error code; no source/story revision or staging file remains; the service remains healthy. | Pytest/security |
| VS-006 | Approve the current Import Review, then run `analyze_story` for that extraction revision. | Analysis is blocked before approval. After approval, the terminal job state is `succeeded`; project detail contains at least one chapter, one scene, narration, two dialogue lines in source order, and two characters; every derived object has source/provenance and revision data. | Pytest/integration |
| VS-007 | Open the analyzed project in the UI. | Chapter and scene navigation is keyboard operable; selecting the first scene displays its narration/dialogue in source order and the detected character list; loading and empty states are not shown as success. | Vitest/Playwright |
| VS-008 | Inspect each automated dialogue attribution. | It exposes `characterId` or `null`, confidence in `[0,1]`, warnings, method/agent version, and source span. An unresolved case is displayed as uncertain, never coerced to a character. | Contract/component |
| VS-009 | Correct one dialogue line to the other fixture character with its current revision and reason `fixture correction`. | API returns the selected character and `source=human`; a provenance event stores prior/new IDs, reason, actor, timestamp, and revision; project and line revisions advance exactly once. | Pytest/API |
| VS-010 | Repeat VS-009 with the stale pre-correction revision. | Response is `409 REVISION_CONFLICT` and includes current revision; the saved character and provenance are unchanged. | Pytest/API |
| VS-011 | Fully stop desktop/service and reopen the same data directory/project. | The imported hash, extraction and Import Review revisions, approval, chapter/scene structure, human-corrected speaker, correction reason/provenance, and project revision match the pre-restart values. | Playwright/integration |
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

## Phase 1 secure-ingest extension

The Phase 0 cases remain regression gates. Phase 1 also uses the deterministic
TXT, DOCX, EPUB, and PDF files generated from
`fixtures/synthetic-story/sample-story.txt`; only their base64 encodings are
committed for binary formats. No test may depend on a private manuscript,
network fetch, cloud credential, downloaded model, or OCR engine.

| ID | Setup and action | Exact pass criteria | Primary level |
| --- | --- | --- | --- |
| VS-101 | Import each valid Phase 1 fixture twice with the same idempotency key. | Format detection, immutable source hash, extraction revision/hash, ordered sections/mappings, parser record, and warnings are stable; replay creates no duplicate source, extraction, review, or job. | Pytest/API |
| VS-102 | Import DOCX/EPUB packages containing traversal, links, excessive members, a member name above 512 Unicode code points, a member above 32 MiB, expansion above 200 MiB, ratio above 100:1, depth above 20, malformed XML, script, or remote references. | Unsafe packages fail with typed redacted errors or omit passive content with an explicit warning; no path escapes staging, script executes, shell starts, or remote request occurs. | Pytest/security |
| VS-103 | Import an encrypted, image-only, malformed, truncated, or 2,001-page PDF. | The service reports the applicable typed failure or OCR-required state, publishes no analyzable story, and remains healthy. A valid text PDF preserves page-aware source mappings. | Pytest/security |
| VS-104 | Cross the fixed source, extracted-character, section, page, archive, and 30-second deadline boundaries by one unit. | The exact limit passes when semantically valid; one unit above fails closed without a partial extraction, review, or analyzable story. A post-publication parser failure may retain its immutable source and redacted attempt evidence. The 8,000-character preview truncates only the review projection, never canonical text. | Pytest/boundary |
| VS-105 | Complete extraction and attempt analysis before approval, then approve using a stale and current extraction revision. | Pending/stale calls fail closed; one local-human approval for the current revision succeeds idempotently and unlocks only that extraction. Changed-byte reimport or explicit re-extraction invalidates the effective gate without rewriting history. | Pytest/API |
| VS-106 | Migrate a representative schema-v1 database and inject backup/migration failures. | A verified `*.v1-backup.sqlite3` recovery copy exists before mutation; schema v2 preserves Phase 0 projects, source/story bytes and hashes, jobs, corrections, and synthesized pending import-review history; failure leaves the original usable or fails read-only with a recovery code. | Pytest/migration |
| VS-107 | Build on GitHub Windows and run the committed packaged persistence test against the exact unpacked executable. | The synthetic DOCX is imported, reviewed, approved, analyzed, corrected, closed, reopened, and restored; source/extraction/approval/analysis/correction persist; owned Electron/service PIDs are identified by ancestry and creation identity and are gone after both shutdowns. | Playwright/CI Windows |
| VS-108 | Generate build evidence after the exact CI build. | Schema-v2 manifest records tested SHA, runner/time, app/staged/embedded hashes, parser pins, exact canonical parser-profile fingerprint, separately named source/preview boundary limits, DOCX fixture hash, packaged Import Review result, and owned-process exit proof; staged and embedded services match. | Tooling/CI Windows |

## Phase 2 whole-book intelligence extension

Phase 0 and Phase 1 cases remain regression gates. Phase 2 uses only the
repository-owned compact story and deterministic generated scale fixture.
Cloud/model/network analysis remains disabled.

| ID | Required executable behavior | Primary level |
| --- | --- | --- |
| VS-201 | Analysis creation succeeds only for the current approved extraction. | Pytest/API |
| VS-202 | A stale, rejected, wrong-revision, or fingerprint-mismatched approval is rejected without a run. | Pytest/API |
| VS-203 | Run creation persists exact source/extraction/review/profile/agent lineage and is idempotent. | Pytest/API |
| VS-204 | Python, TypeScript/tooling, and evidence agree on the canonical profile fingerprint. | Pytest/Node |
| VS-205 | Same inputs/profile/agent versions/corrections reproduce stable IDs, ordering, and content fingerprints. | Pytest |
| VS-206 | Explicit chapter/section/scene markers create evidence-backed bounded structure. | Pytest |
| VS-207 | Human add/remove/move/relabel structure corrections become effective without editing machine rows. | Pytest/API |
| VS-208 | At least six fixture characters are extracted with stable IDs and evidence. | Pytest |
| VS-209 | Alias and honorific claims retain evidence, confidence, warnings, and time/range scope. | Pytest |
| VS-210 | Human character merge is append-only, protected, and preserved on rerun. | Pytest/API |
| VS-211 | Human character split preserves prior machine identity/mention history. | Pytest/API |
| VS-212 | Named and ambiguous mentions can be resolved or left explicitly unresolved. | Pytest/API |
| VS-213 | Duplicate display names remain distinct unless evidence or a human merge connects them. | Pytest |
| VS-214 | Dialogue and narration slices equal exact approved canonical text at their recorded offsets. | Pytest |
| VS-215 | Explicit speech tags create documented high-support attribution candidates. | Pytest |
| VS-216 | Ambiguous dialogue retains bounded candidates and a nullable/unknown effective speaker. | Pytest/UI |
| VS-217 | Turn-taking uses a bounded documented window and never forces complete coverage. | Pytest |
| VS-218 | Existing and new human speaker corrections remain effective over later automation. | Pytest/API |
| VS-219 | POV modes/shifts are evidence-backed and unknown/mixed states are representable. | Pytest |
| VS-220 | Human POV correction and lock survive restart/rerun. | Pytest/API |
| VS-221 | Stable location entities and scene assignments require textual evidence. | Pytest |
| VS-222 | Location aliases remain scoped and identical labels are not automatically merged. | Pytest |
| VS-223 | Narrative sequence and story-world sequence are stored independently. | Pytest |
| VS-224 | Flashback/flash-forward evidence creates temporal constraints rather than invented dates. | Pytest |
| VS-225 | Unknown or contradictory temporal order remains explicit and reviewable. | Pytest/UI |
| VS-226 | Relationship edges require evidence, direction, type, and confidence. | Pytest |
| VS-227 | Relationship change over time creates versioned/scoped evidence rather than overwriting an edge. | Pytest |
| VS-228 | Emotional and dramatic-intent proposals separate interpretation from evidence. | Pytest/UI |
| VS-229 | The intentional fixture anomaly produces an evidence-backed continuity finding. | Pytest |
| VS-230 | Human continuity dispositions are append-only and persist across rerun/restart. | Pytest/API |
| VS-231 | Every applicable correction survives a compatible deterministic rerun. | Pytest |
| VS-232 | Four Phase 2 gate projections begin pending for each new snapshot. | Pytest/API |
| VS-233 | Changed evidence invalidates only approvals whose canonical evidence set changed. | Pytest |
| VS-234 | Unrelated current evidence retains its matching approval decision. | Pytest |
| VS-235 | Cancellation at bounded analysis stages publishes no replacement snapshot. | Pytest |
| VS-236 | Explicit retry preserves the failed attempt and can publish one valid snapshot. | Pytest |
| VS-237 | Restart marks abandoned work interrupted and resumes only from a compatible checkpoint. | Pytest |
| VS-238 | A genuine frozen schema-v2 database migrates atomically to exact schema v3 with preserved Phase 0/1 history. | Pytest/migration |
| VS-239 | Backup/migration failure leaves v2 usable and retains the verified v2 backup. | Pytest/migration |
| VS-240 | Every Phase 2 route rejects absent/wrong launch authentication and preserves project isolation. | Pytest/security |
| VS-241 | Collection pages default to 50, cap at 200, bind cursors to query/revision, bound excerpts, and never return the full manuscript. | Pytest/API |
| VS-242 | Main and preload reject unknown, oversized, stale, and cross-project Phase 2 IPC payloads. | Vitest |
| VS-243 | React Analysis workspace exposes all required paginated review views, confidence/warnings, corrections, and gates. | Vitest |
| VS-244 | Development Electron E2E completes and restores the Phase 2 review/correction/gate flow. | Playwright Windows |
| VS-245 | Exact CI artifact packaged E2E completes and restores the same Phase 2 flow. | Playwright/CI Windows |
| VS-246 | Both packaged launches establish exact Electron/service ownership and leave no forced or remaining PID. | Playwright/CI Windows |
| VS-247 | Tracked/staged policy scans reject private content, generated databases, manifests, binaries, paths, and logs. | Node/CI |
| VS-248 | Checksum-pinned Gitleaks reports no history finding. | CI Security |
| VS-249 | Deterministic generated 100,000-word analysis verifies bounded pages, the controlling collection index, cancellation/intermediate restart, stable IDs, non-quadratic candidate paths, and peak RSS below the 320 MiB test ceiling. | Pytest/scale |
| VS-250 | Every Phase 0 and Phase 1 suite, development E2E, build, and exact packaged gate remains successful. | Full CI |

## Concrete service contract

All routes are under `/api/v1`, require `Authorization: Bearer <launch-token>`,
accept/return JSON unless the import is multipart, and return a correlation ID.
Phase 1 preserves the Phase 0 routes and makes import extraction asynchronous.
Required routes:

| Method and route | Minimum typed behavior |
| --- | --- |
| `GET /health` | Returns service/contract version, instance ID, `starting|ready|degraded`, database state, and UTC time. |
| `GET /providers/health` | Returns every registered adapter's kind, status, capabilities, optional version, redacted reason, and checked time. |
| `GET /capabilities/ffmpeg` | Returns `available|missing|incompatible|failed`, sanitized executable origin, version, and required capabilities. |
| `GET /projects` | Returns paginated project summaries in stable updated/name/ID order. |
| `POST /projects` | Accepts `{name}` and idempotency key; returns the durable project. |
| `GET /projects/{projectId}` | Returns a `ProjectDetail` with source/story revision, chapters/scenes/lines/characters, casting placeholders, approvals, and relevant jobs. |
| `POST /projects/{projectId}/imports` | Streams multipart `file` plus optional declared format; preserves the immutable source and returns source, pending extraction, and persisted extraction-job metadata. |
| `GET /projects/{projectId}/imports/{reviewId}/review` | Returns the bounded current Import Review projection, state, warnings, and latest decision. |
| `POST /projects/{projectId}/imports/{reviewId}/review/decision` | Appends an idempotent local-human decision for the exact review revision and evidence fingerprint. |
| `POST /projects/{projectId}/imports/{sourceDocumentId}/reextract` | Appends a pending extraction revision and persisted job for an existing immutable source. |
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
