# Product Requirements

## Product intent

Cinematic Story Studio is a local-first Windows desktop application for turning an authored story into an inspectable, editable, resumable cinematic audio production. It preserves the source, keeps automated decisions reviewable, and makes cloud processing optional.

The primary user is an author, producer, or audio director who wants one installed application rather than a collection of scripts. A supporting user is a technical operator who configures providers and diagnoses failed work without reading private content in logs.

## Product principles

1. Preserve imported source bytes and extracted story characters. Rewriting is a separate, explicit user action and is outside Phase 0.
2. A human correction is durable provenance. Automation may propose a later change but may not silently replace the correction.
3. Local operation is the default. The application launches, opens projects, and performs the Phase 0 workflow without cloud credentials.
4. Every long operation is observable, cancellable, and recoverable.
5. Production decisions, inputs, provider identities, versions, and outputs are inspectable.
6. The installed application starts its dependencies; routine use never requires PowerShell, Docker, or a manually started service.

## Functional requirements

### Projects and import

- **PR-001** The user can create, list, open, rename, and delete a local project.
- **PR-002** The target product imports TXT, Markdown, DOCX, EPUB, and PDF. Phase 0 must implement TXT and Markdown and reject unsupported or malformed files clearly.
- **PR-003** The application stores the original file bytes, a SHA-256 digest, encoding/extraction metadata, and an exact extracted-text representation. Parsing never mutates that record.
- **PR-004** Import validates type, size, archive members, and extraction limits before making content durable. A failed import leaves no visible partial story.
- **PR-005** Reimport creates a new source/story revision; it never overwrites the prior source.
- **PR-006** Opening a project restores its last durable state after an application restart.

### Story analysis and human review

- **PR-010** Analysis produces typed chapters, scenes, narration spans, dialogue lines, characters, locations, moods, action/continuity observations, confidence values, warnings, and provenance as applicable.
- **PR-011** Phase 0 detects at least one chapter and one scene in the synthetic fixture and displays chapters, scenes, narration, dialogue, and detected characters.
- **PR-012** Uncertain dialogue attribution remains explicitly uncertain; the product never invents confidence.
- **PR-013** The user can correct a dialogue speaker, supply an optional reason, and immediately see the saved result.
- **PR-014** Each correction records actor, timestamp, prior value, new value, project revision, and source span. Later automation preserves it unless the user explicitly supersedes it.
- **PR-015** Concurrent edits use revisions and return a conflict instead of silently losing a change.
- **PR-016** Review gates are durable and show the exact artifact revision approved or rejected.

### Casting and production

- **PR-020** Each character can have a typed voice profile and casting assignment, including provider, voice identifier, performance constraints, status, and provenance.
- **PR-021** Phase 0 displays a placeholder casting panel from real typed project data; it must not hard-code a successful provider result.
- **PR-022** The target product supports performance direction, ambience, Foley, sound effects, restrained music, transitions, continuity review, mixing, mastering, and quality-control findings.
- **PR-023** The target product exports scene and chapter artifacts plus WAV and MP3 masters; M4B is deferred until its metadata and chapter behavior are specified and tested.
- **PR-024** A published render is reproducible from a versioned manifest or is marked non-reproducible with the exact missing/non-deterministic input.

### Jobs and recovery

- **PR-030** Analysis and rendering run as persisted background jobs with typed input snapshots, progress, attempts, checkpoints, structured errors, and event history.
- **PR-031** The UI displays state, current stage, completed/total units when known, progress from 0 through 1, and a redacted actionable error.
- **PR-032** A user can request cancellation. The worker stops cooperatively at a safe boundary, cleans temporary artifacts, and never publishes a partial result.
- **PR-033** A failed job can be retried as a new recorded attempt. An interrupted job can resume from a compatible checkpoint or explain why a restart is required.
- **PR-034** On startup, abandoned `running` jobs become `interrupted`; they are never reported as still running.
- **PR-035** Repeated commands support idempotency so a UI retry cannot create duplicate imports, corrections, or jobs.

### Providers and local capabilities

- **PR-040** Provider implementations sit behind a typed adapter boundary and report `available`, `degraded`, `unavailable`, `unauthorized`, or `disabled` without blocking application launch.
- **PR-041** Cloud use is off by default. Before content is sent, the UI identifies the provider, content category, purpose, and applicable retention/policy link and requires explicit confirmation.
- **PR-042** Credentials are stored through Windows Credential Manager or an equivalent OS-protected facility; SQLite stores only a non-secret credential reference.
- **PR-043** Phase 0 exposes provider health, a development-only Docker Kokoro health check, and an FFmpeg capability check. Kokoro being absent does not break project work.
- **PR-044** Packaged builds manage required FFmpeg binaries and a future bundled local speech runtime. Docker remains a development option only.

## Windows experience

- **PR-050** A packaged user installs through a normal Windows installer and launches from Start menu and, when selected, a desktop shortcut.
- **PR-051** Electron starts the bundled loopback-only local service on an OS-assigned port, authenticates it with a per-launch secret, verifies readiness, opens SQLite, and restores the last project.
- **PR-052** The renderer has no direct Node.js, filesystem, process, credential, or unauthenticated backend access.
- **PR-053** Closing the app requests graceful service shutdown and has a bounded, safe fallback for an unresponsive owned child process.
- **PR-054** Phase 0 must produce an initial unpackaged Electron build. A distributable installer is a target, not a Phase 0 release claim.

## Quality attributes

- **Security and privacy:** loopback-only authenticated service; least-privilege renderer; validated paths and archives; safe subprocess argument arrays; no source text, secrets, or audio in telemetry or logs; dependency and secret scanning.
- **Reliability:** transactional writes, foreign keys, migration backups, atomic artifact publication, durable jobs, bounded retries, and actionable recovery.
- **Performance:** UI actions remain responsive while jobs run. Lists are paginated and large story/audio payloads are streamed rather than copied through renderer IPC.
- **Accessibility:** all Phase 0 workflows are keyboard operable, have visible focus, expose semantic status/progress, and do not rely on color alone.
- **Reproducibility:** stable ordering, schema/agent/provider versions, hashes, configuration, locale, timestamps separated from content identity, and seeds where supported.
- **Compatibility:** supported production platform is maintained 64-bit Windows. Development may also run on other platforms without changing Windows guarantees.
- **Observability:** structured, redacted logs use correlation, project, job, and attempt identifiers but never manuscript passages or credentials.

## Phase 0 release boundary

Phase 0 includes repository safeguards, typed contracts, architecture, TXT/Markdown import, basic deterministic parsing, SQLite persistence, speaker correction, persisted analysis jobs, provider/capability health, synthetic fixtures, test coverage, and an unpackaged desktop build.

Phase 0 does **not** claim production-quality speech, a complete audiobook render, bundled large models, DOCX/EPUB/PDF import, M4B output, automatic updating, a signed public installer, or final cinematic quality.

## Acceptance and traceability

The executable behavioral contract is [vertical-slice-acceptance-tests.md](vertical-slice-acceptance-tests.md). Architecture documents may refine implementation but may not weaken those criteria. A capability is complete only when its applicable automated check passes or a dated manual result records the exact build, environment, and evidence.
