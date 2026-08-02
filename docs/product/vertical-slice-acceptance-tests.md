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

## Phase 3A governed casting extension

Phase 0-2 cases remain regression gates. Phase 3A uses only the approved public
story fixture and the repository-owned synthetic voice catalog. It performs no
network call, credential operation, model download, synthesis, or audio work.

| ID | Required executable behavior | Primary level |
| --- | --- | --- |
| VS-301 | Casting creation succeeds only for the current approved Import Review, Phase 2 run/snapshot/correction set/character registry, and all four current approved Phase 2 gate decisions. | Pytest/API |
| VS-302 | Missing, rejected, wrong-project, wrong-revision, superseded, or fingerprint-mismatched Phase 2 evidence is rejected without a publishable casting run. | Pytest/API/security |
| VS-303 | Run creation freezes every source/extraction/review/snapshot/correction/gate/catalog/profile/producer identity and is idempotent. | Pytest/API |
| VS-304 | TypeScript, Python, catalog tooling, desktop validation, and evidence agree on profile `governed-voice-casting-v1@1.0.0` and fingerprint `3eaa6b4d1333b49e55707b1e9aa20606f262e1315a043bff2912a0fe77f97fa6`. | Node/Pytest/Vitest |
| VS-305 | Synthetic catalog `synthetic-voice-catalog-v1@1.0.0` reproduces fingerprint `68d116d1f66e4ea4bcceabfd0520fd889cf9da3074ee1b9186c43c285575c25f`, two provider descriptors, five model descriptors, fourteen profiles, and fourteen rights records. | Node/Pytest |
| VS-306 | Provider/model descriptors expose all typed availability, execution, output, licensing, rights-metadata, health, and provenance fields while both fixture providers remain non-synthesizing and the cloud-capable provider remains disabled. | Contract/Pytest |
| VS-307 | Voice profiles preserve every declared language/locale/presentation/range/suitability/rights/state/provenance field and reject real-person, credential, content, duplicate, cross-descriptor, or malformed data. | Contract/Pytest/security |
| VS-308 | Verified, restricted, unknown, and prohibited rights remain distinct; restricted selection requires exact acknowledgement, and unknown/prohibited rights cannot receive Complete Cast approval. | Pytest/API/UI |
| VS-309 | Stable primary/secondary narrator, named-character, unresolved, group/crowd, quoted/announcement, internal-thought, and custom role types are supported; generated roles preserve stable Character Registry identity separately from analysis entity identity, derive importance conservatively, project only approved language/locale/performance evidence, and do not invent unresolved material or missing requirements. | Pytest |
| VS-310 | Same snapshot/catalog/profile/correction inputs reproduce stable role/candidate/conflict IDs, ordering, compatibility content, and fingerprints. | Pytest |
| VS-311 | Hard constraints fail closed and soft preferences remain separately visible; language, long-form, provider/model unavailable, blocked, deprecated, unknown, and prohibited cases retain their exact states. | Pytest/contract |
| VS-312 | Each role evaluates at most 50 pre-reduction voices and publishes at most 12 candidates with bounded explanations, complete provenance, at most 32 warnings tied to its actual persisted conflict rows, and no automatic assignment. | Pytest/scale |
| VS-313 | Metadata similarity, narrator/major-character reuse, incompatible reuse, locale, expressive, rights, availability, deprecated, role-length, unresolved-role, and reuse-threshold categories are typed; no result claims acoustic similarity, and assignment-time conflict recomputation rejects the whole mutation before crossing 10,000 conflicts. | Pytest/UI |
| VS-314 | Human select, clear, lock, unlock, intentionally-uncast, label/requirement change, restricted-rights acknowledgement, voice-reuse approval, candidate rejection, and custom rationale append immutable correction evidence. | Pytest/API |
| VS-315 | Machine proposals, human selections, and human locks remain separate assignment revisions; stale writes conflict and automation cannot overwrite human authority. | Pytest/API |
| VS-316 | Compatible reruns/restarts preserve human selection, lock, correction, and conflict disposition. Changed incompatible evidence requires explicit recasting. | Pytest/integration |
| VS-317 | Selected catalog/provider/model/profile/rights drift appends a durable affected-assignment invalidation plus system `invalidated` decisions for only that role gate and Complete; catalog reversion does not restore authority, explicit human reselection/reapproval is required, and unrelated catalog changes preserve unaffected assignment and gate approval. | Pytest |
| VS-318 | Narrator and Character Casting Reviews are independently append-only; Complete Cast requires both current approvals and eligible current cast evidence. System actors can only invalidate, human actors cannot author `invalidated`, decisions persist after restart, and reruns never reapprove. | Pytest/API |
| VS-319 | `analyze_casting` exposes all nine ordered stages, monotonic progress, bounded content-free events, cancellation before publication, explicit failed-attempt retry, and compatible checkpoint interruption recovery. | Pytest/integration |
| VS-320 | Catalog/run/role/candidate/conflict/assignment/correction pages default to 50 and cap at 200, candidates cap at 12, cursors bind query/revision, and no list returns manuscript text. | Pytest/API |
| VS-321 | Mutation body, identifier, idempotency, correction-field/reason, warning-acknowledgement/rationale, and explanation bounds fail closed; every Phase 3A route requires the correct launch token and project scope. | Pytest/API/security |
| VS-322 | Main and preload reject unknown, oversized, stale, and cross-project Phase 3A IPC requests/responses while preserving renderer isolation. | Vitest |
| VS-323 | The React Casting workspace exposes prerequisites/profile/catalog, paginated role workload/candidates, hard/soft/rights/availability explanations, metadata conflicts, assignments/locks/uncast status, correction history, three gates, and valid job controls without playback/waveform/audio controls. | Vitest |
| VS-324 | A schema-valid synthetic catalog persists and API-pages 5,000 profiles; candidate scale executes 300 roles and 50 pre-reduction assessments per role with bounded final reduction/conflicts, deterministic rerun, cancellation, restart recovery, and no role-by-voice database access pattern. Supporting SQLite index plans are labeled as structural evidence, not runtime keyset-query evidence. | Pytest/scale |
| VS-325 | A genuine frozen populated schema-v3 fixture migrates atomically to exact schema v4 after a verified logical-digest-equal v3 backup, preserving every Phase 0-2 value and creating no Phase 3A evidence. | Pytest/migration |
| VS-326 | Backup corruption/publication failure, v3/v4 structural or ledger drift, injected v4 migration failure, and forged downgrade fail without mutation; rollback retains a usable verified v3 backup. | Pytest/migration |
| VS-327 | Development Electron E2E completes the synthetic import, Phase 2 prerequisites, casting selections/locks/conflict/rights/gates, close/reopen restoration, and clean shutdown. | Playwright Windows |
| VS-328 | Exact-CI-artifact packaged E2E completes and restores the same Phase 0-3A workflow against the exact embedded service and generates bounded screenshot/machine evidence. | Playwright/CI Windows |
| VS-329 | Both packaged launches establish exact owned Electron/service identities by creation/ancestry/handshake, terminate only those identities, and finish with no forced or remaining owned PID. | Playwright/CI Windows |
| VS-330 | Schema-`4.0.0` build evidence retains top-level Phase 2 `packagedE2e` schema `4.0.0`; `voiceCastingContract.packagedE2e` schema `5.0.0` validates all Phase 3A identities/counts/fingerprints/rights/corrections/conflicts/gates/restart/process assertions while the manifest binds exact application/service hashes; the version-scoped Windows artifact upload occurs only after both E2Es, initial manifest validation, tracked-content rescan, clean-tree verification, and final exact-byte manifest revalidation all succeed, with Gitleaks enforced separately by Security safeguards. | Tooling/CI |
| VS-331 | Every Phase 0-2 test, development E2E, build, packaged E2E, migration, scan, and clean-tree gate remains successful. | Full CI |

## Phase 3B local speech auditions extension

Phase 0-3A cases remain regression gates. Phase 3B hosted product-path tests use
only repository-owned synthetic story/script material, isolated application and
temporary directories, and the visibly fixture-only deterministic PCM adapter.
The ignored Kokoro model and generated real audio never enter Git or hosted CI.

| ID | Required executable behavior | Primary level |
| --- | --- | --- |
| VS-401 | Session creation, generation, review projection, and readiness accept only the latest succeeded Phase 3A run, its exact validated immutable current cast-snapshot manifest, assignments/revisions and lock state selected by that manifest, exact leaf/temporally applicable rights evidence, and latest eligible reviews with exact approved human decision provenance, supersession, and warning acknowledgements. | Pytest/API |
| VS-402 | Evidence from different cast snapshots cannot be combined; a newer current snapshot suppresses older-snapshot work, and a new same-snapshot Phase 3A decision tuple invalidates dependent Phase 3B evidence even when assignments are unchanged. | Pytest/integration |
| VS-403 | The deterministic PCM provider is marked fixture-only/export-ineligible. Kokoro's component command can verify a bounded private worker run, but the governed product rejects `af_heart` without an exact Phase 3A voice/profile/assignment/rights binding. | Pytest/component |
| VS-404 | Model ZIP installation enforces authenticated bounded upload, exact allow-listed inventory/size/hash, traversal/link/reparse/special-entry and collision rejection, private atomic placement, verification-before-activation, and active-job leases without renderer paths or downloads. | Pytest/security |
| VS-405 | Each worker uses the fixed executable/module, bounded authenticated frames, service-computed resolved-executable hash, exact PID/topology/Job Object ownership, no name-based adoption or termination, and confirmed exact owned-process exit. | Pytest/Playwright Windows |
| VS-406 | Scoped append-only pronunciation entries, decisions, precedence, dictionary/effective-plan fingerprints, ambiguity rejection, and normalization previews preserve exact source text while excluding markup and forbidden control characters. | Pytest/API/UI |
| VS-407 | Role-bound sessions and bounded private scripts freeze exact authority/provenance; interruption around private script publication reconciles only exact owned files and never leaks text into lists, logs, paths, or checkpoints. | Pytest/integration/security |
| VS-408 | `generate_audition` exposes all 13 ordered stages, hash-/ID-only checkpoints, bounded cancellation/retry/restart, one provider dispatch per durable attempt, atomic clip publication, and no partial successful evidence. | Pytest/integration |
| VS-409 | Cache keys bind all output-affecting evidence inside one private project; each hit revalidates exact bytes/WAV metadata and records zero dispatch, while retry appends a new provider request and never hides an internal retry. | Pytest |
| VS-410 | Published clips are bounded 24 kHz mono PCM16 WAV; exact size/hash/format and versioned peak/RMS/silence/clipping QC bind review evidence, while machine results never claim intelligibility or approval. | Pytest/contract |
| VS-411 | Audio loads only by authenticated project/session/clip/artifact identity; service and Electron main independently revalidate ownership, no-store, size, SHA-256, and WAV structure before the named IPC returns at most one 24 MiB `ArrayBuffer`. | Pytest/Vitest |
| VS-412 | Per-role, Narrator, Character, Pronunciation, and Voice Readiness reviews bind exact immutable evidence. Human and system actor unions are enforced, decisions are append-only, and generation/cache/QC never grants or restores approval. | Pytest/API/UI |
| VS-413 | Assignment, leaf/temporal rights, model/runtime, applicable pronunciation, cache/artifact integrity, cast snapshot, and Phase 3A decision drift append targeted invalidation; unrelated same-snapshot evidence and all immutable history are preserved. | Pytest/integration |
| VS-414 | A genuine frozen 67,675-byte schema-v4 fixture with SHA-256 `50dc228bb25466571b2600e1eb14a60dadee0539971f78245569a6ca8b201290` migrates atomically to exact schema v5 after a verified logical-digest-equal v4 backup; drift/failure rolls back without fabricated model, audio, cache, or approval evidence. | Pytest/migration |
| VS-415 | Phase 3B collection pages default to 50 and cap at 200. The workspace returns exactly two role pages for 300 roles, rejects cross-project `roleCursor` reuse, and rejects a cursor after the tested bound session/clip collection generation change; exact snapshot-selected assignment and leaf/temporal rights drift separately removes trusted audition-action and current session/generation/review authority while bounded metadata may remain visible. | Pytest/API/scale |
| VS-416 | The React Auditions workspace exposes exact prerequisites, roles, assignments/rights, package/runtime state, pronunciation and normalization, sessions/jobs/clips/cache/QC, playback controls, five gates, warnings, and restart-safe state without a generic HTTP/path/audio capability. | Vitest |
| VS-417 | Development Electron E2E completes the retained Phase 0-3A flow plus three governed fixture auditions, authenticated WAV loading, cache hit, targeted pronunciation invalidation/regeneration, all five decisions, restart restoration, and clean owned-process shutdown. | Playwright Windows |
| VS-418 | Exact-CI-artifact packaged E2E runs the same flow against `release/<version>/win-unpacked/Cinematic Story Studio.exe`, identifies only owned Electron/service/provider-worker processes, proves every exact PID exits after both launches, and never inspects or terminates unrelated processes. | Playwright/CI Windows |
| VS-419 | Schema-`6.0.0` build evidence embeds validated schema-`7.0.0` Phase 3B results, binds exact app/staged/embedded-service/result/screenshot hashes, proves staged/embedded equality and process/network assertions, excludes private content, and is revalidated immediately before short-lived upload. | Tooling/CI Windows |
| VS-420 | Every Phase 0-3A test, full/focused backend and desktop suite, development E2E, build, frozen-service check, packaged E2E, migration, policy/security scan, dependency review, manifest revalidation, and clean-tree gate remains successful or has its exact environment-dependent skip reported separately. | Full CI |

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
| `GET /projects/{projectId}/casting/catalog` | Returns the current immutable descriptor/model/profile/rights catalog and bounded voice page with optional catalog preconditions. |
| `POST /projects/{projectId}/casting-runs` | Creates one durable idempotent `analyze_casting` run/job after rechecking all exact Phase 2, catalog, and profile preconditions. |
| `GET /projects/{projectId}/casting-runs` | Returns bounded stable run history. |
| `GET /projects/{projectId}/casting-runs/{runId}` | Returns one exact run and its reviewable cast snapshot. |
| `GET /projects/{projectId}/casting-runs/{runId}/roles` | Returns bounded production roles only after run/catalog/snapshot preconditions match. |
| `GET /projects/{projectId}/casting-runs/{runId}/roles/{roleId}/candidates` | Returns at most 12 candidates after also checking exact role revision. |
| `GET /projects/{projectId}/casting-runs/{runId}/conflicts` | Returns bounded metadata-only conflict evidence. |
| `GET /projects/{projectId}/casting-runs/{runId}/assignments` | Returns bounded immutable machine/human assignment history/projection. |
| `GET /projects/{projectId}/casting-runs/{runId}/corrections` | Returns bounded immutable correction history. |
| `POST /projects/{projectId}/casting-runs/{runId}/corrections` | Appends one idempotent human correction against exact role/run/catalog/snapshot/correction fingerprints. |
| `GET /projects/{projectId}/casting-runs/{runId}/reviews` | Returns Narrator, Character, and Complete Cast Review projections for an exact cast snapshot. |
| `POST /projects/{projectId}/casting-runs/{runId}/reviews/{gateId}/decisions` | Appends an idempotent human casting decision against exact review/run/snapshot/evidence preconditions. |

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
