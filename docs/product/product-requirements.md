# Product Requirements

## Product intent

Cinematic Story Studio is a local-first Windows desktop application for turning an authored story into an inspectable, editable, resumable cinematic audio production. It preserves the source, keeps automated decisions reviewable, and makes cloud processing optional.

The primary user is an author, producer, or audio director who wants one installed application rather than a collection of scripts. A supporting user is a technical operator who configures providers and diagnoses failed work without reading private content in logs.

## Product principles

1. Preserve imported source bytes and extracted story characters. Rewriting is a separate, explicit user action and is outside Phase 1.
2. A human correction is durable provenance. Automation may propose a later change but may not silently replace the correction.
3. Local operation is the default. The application launches, opens projects, and performs the Phase 1 workflow without cloud credentials.
4. Every long operation is observable, cancellable, and recoverable.
5. Production decisions, inputs, provider identities, versions, and outputs are inspectable.
6. The installed application starts its dependencies; routine use never requires PowerShell, Docker, or a manually started service.

## Functional requirements

### Projects and import

- **PR-001** The user can create, list, open, rename, and delete a local project.
- **PR-002** Phase 1 imports TXT, Markdown, DOCX, EPUB, and text-based PDF through format-specific adapters. Encrypted, malformed, image-only, or unsupported inputs fail with typed, redacted errors; OCR is not performed.
- **PR-003** The application stores immutable original bytes, a SHA-256 digest, source revision, parser identity/version, exact extracted text, extraction digest, structure/source mappings, warnings, and a limits fingerprint. Re-extraction appends a new extraction revision.
- **PR-004** Import enforces the separate 100 MiB source boundary and the fixed parser profile before extraction publication: 2,048 archive members, 512 Unicode code points per member name, 32 MiB per member, 200 MiB total expansion, 100:1 compression ratio, archive depth 20, 10,000,000 extracted Unicode characters, 10,000 sections, 2,000 PDF pages, a parent-enforced 30-second spawned-parser wall-clock deadline, and a Windows Job Object ceiling of 768 MiB per parser process.
- **PR-005** An identical import reuses the current source/extraction; a changed-byte reimport creates a new source revision and, after successful extraction, a new story revision. It never overwrites the prior source.
- **PR-006** Opening a project restores its last durable state after an application restart.
- **PR-007** A completed extraction enters a durable Import Review gate. Story analysis remains blocked until a local human approves the exact source and extraction revisions.
- **PR-008** Import Review shows at most 8,000 preview characters plus format, byte size, source/extracted hashes, adapter/version, structure summary, warnings, and any omitted-content notice.
- **PR-009** The service accepts sources up to 100 MiB. The Phase 1 desktop native transfer boundary rejects a selected file above its separately enforced 8 MiB client cap before service upload; file bytes do not cross renderer IPC, and the UI must distinguish that client limit from the service parser limit.

### Story analysis and human review

- **PR-010** Analysis produces typed chapters, scenes, narration spans, dialogue lines, characters, locations, moods, action/continuity observations, confidence values, warnings, and provenance as applicable.
- **PR-011** Phase 0 detects at least one chapter and one scene in the synthetic fixture and displays chapters, scenes, narration, dialogue, and detected characters.
- **PR-012** Uncertain dialogue attribution remains explicitly uncertain; the product never invents confidence.
- **PR-013** The user can correct a dialogue speaker, supply an optional reason, and immediately see the saved result.
- **PR-014** Each correction records actor, timestamp, prior value, new value, project revision, and source span. Later automation preserves it unless the user explicitly supersedes it.
- **PR-015** Concurrent edits use revisions and return a conflict instead of silently losing a change.
- **PR-016** Review gates are durable and show the exact artifact revision approved or rejected.
- **PR-017** Phase 2 analysis consumes only the current approved extraction and
  records project/source/extraction/review/profile/agent identities and
  fingerprints in an immutable analysis run and snapshot.
- **PR-018** Phase 2 represents evidence-backed structure, beats, character
  identities/aliases/mentions, verbatim dialogue and candidate speakers,
  narration classification, POV, locations, narrative/story chronology,
  relationships, emotion/intent, and continuity findings. Unknown,
  contradictory, and low-confidence states remain explicit.
- **PR-019** Phase 2 human corrections are append-only overlays for structure,
  identity, aliases/mentions, dialogue speakers, POV, locations, time,
  relationships, continuity dispositions, and supported interpretive fields.
  Four post-import gates durably review structure, the character registry,
  dialogue attribution, and the whole-book snapshot.

### Casting and production

- **PR-020** Each character can have a typed voice profile and casting assignment, including provider, voice identifier, performance constraints, status, and provenance.
- **PR-021** Phase 0 displays a placeholder casting panel from real typed project data; it must not hard-code a successful provider result.
- **PR-022** The target product supports performance direction, ambience, Foley, sound effects, restrained music, transitions, continuity review, mixing, mastering, and quality-control findings.
- **PR-023** The target product exports scene and chapter artifacts plus WAV and MP3 masters; M4B is deferred until its metadata and chapter behavior are specified and tested.
- **PR-024** A published render is reproducible from a versioned manifest or is marked non-reproducible with the exact missing/non-deterministic input.
- **PR-025** Phase 3A casting starts only from the current approved Import
  Review and all four current Phase 2 analysis reviews. A run freezes source,
  extraction, review, snapshot, correction-set, character-registry, catalog,
  profile, producer, and gate-decision identities/fingerprints and rejects
  stale or cross-revision evidence.
- **PR-026** The versioned provider-independent catalog separates provider
  descriptors, model descriptors, project-independent voice profiles, and one
  rights record per voice. The default repository catalog is synthetic,
  deterministic, credential-free, content-free, and unable to synthesize or
  make a cloud request.
- **PR-027** The casting job derives stable production roles and bounded
  candidates with separately visible hard constraints, soft preferences,
  rights/consent, availability, unknown states, explanations, conflicts,
  provenance, and fingerprints. Scores never assign a voice or claim artistic
  correctness or acoustic similarity. Character identity remains the effective
  Character Registry identity; importance and language/locale/performance
  requirements project approved evidence conservatively without inference.
  Candidate warnings reference actual persisted conflicts, and assignment-time
  conflict recomputation fails closed at the governed cap.
- **PR-028** Machine proposals remain separate from append-only human
  selections, locks, intentionally-uncast decisions, conflict/rights
  acknowledgements, candidate rejections, role corrections, and rationale.
  Current assignment and review projections preserve complete supersession
  history and survive restart/rerun when evidence remains compatible.
- **PR-029** Narrator Casting Review, Character Casting Review, and Complete
  Cast Review use append-only human decisions and system invalidation
  decisions. Only humans may approve; only the system may author
  `invalidated`. Selected catalog/provider/model/profile/rights drift durably
  invalidates only the affected assignment, role gate, and dependent Complete
  gate until explicit human reselection/reapproval. Complete Cast requires the
  two current prerequisite reviews and current eligible rights evidence. An
  approved Phase 3A cast is eligible only for a separately authorized later
  phase; Phase 3A generates no speech or audio.

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

## Phase 1 release boundary

Phase 1 adds secure local DOCX, EPUB, and text-based PDF extraction; immutable source and extraction revisions; typed parser records; a mandatory Import Review approval before analysis; schema-v2 migration with a verified recovery backup; deterministic public fixtures; adversarial parser tests; and packaged persistence evidence for the DOCX review flow.

Phase 1 does **not** add OCR, manuscript rewriting, cloud parsing, audio generation, bundled models, an installer/public release, or any Phase 2 production feature. Embedded media, executable content, scripts, macros, remote EPUB resources, and PDF attachments are not executed or fetched.

## Phase 2 release boundary

Phase 2 adds the deterministic local
`whole-book-intelligence-v1` analysis profile, typed controlled story-analysis
agents, immutable runs/snapshots, schema-v3 migration and verified v2 backup,
a bounded evidence-backed graph spanning the governed Phase 2 collections, a
human correction overlay, paginated APIs, an inspectable Analysis workspace,
and four analysis approval gates. Executable coverage includes the generated
100,000-word scale case and compact public E2E fixture; dated local and CI
results are recorded only in Phase 2 verification evidence.

Phase 2 does **not** add speech synthesis, voice cloning/casting, provider
credentials, cloud LLM or speech calls, local model downloads, music/Foley/
ambience generation, audio mixing/export, OCR, signing, updates, a release
tag, or a DAW timeline.

## Phase 3A release boundary

Phase 3A adds contract `3.0.0`, deterministic governed profile
`governed-voice-casting-v1@1.0.0`, a repository-owned synthetic catalog,
schema-v4 catalog/rights/role/candidate/assignment/invalidation/correction/
review records, a durable casting job, bounded authenticated APIs and IPC, the
Casting workspace, metadata-based conflict review, immutable human casting
authority, and three casting approval gates. Exact behavior and claim limits are in
[Phase 3A governed voice casting](phase-3a-voice-casting.md).

Phase 3A does **not** add synthesis, audition audio, real-provider calls,
credentials, model downloads, voice cloning, acoustic analysis, playback,
waveforms, pronunciation, direction, music/Foley/ambience, mixing, audio
export, signing, updates, releases, marketplaces, Phase 3B, or Phase 4. A
rights state records evidence under a versioned policy; it is not legal
certainty. A compatibility result explains declared metadata; it is not
artistic correctness.

## Acceptance and traceability

The executable behavioral contract is [vertical-slice-acceptance-tests.md](vertical-slice-acceptance-tests.md). Architecture documents may refine implementation but may not weaken those criteria. A capability is complete only when its applicable automated check passes or a dated manual result records the exact build, environment, and evidence.
