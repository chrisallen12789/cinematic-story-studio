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

1. The user selects TXT, Markdown, DOCX, EPUB, or PDF through the native picker. The renderer never receives an arbitrary path capability.
2. Main checks picker provenance, bounded metadata, and the Phase 1 desktop transfer cap. A rejection above that client cap is reported as a desktop transfer limit, not as the service's 100 MiB parser ceiling.
3. The service streams to private staging, hashes and preserves the original bytes, detects the format from extension/signature/package structure, and applies the fixed secure-ingest limits.
4. A bounded local parser extracts exact canonical text, ordered sections and source mappings. It never starts a shell, executes active content, follows an archive path outside the package, or fetches a network resource.
5. The service publishes immutable source and extraction records and opens Import Review. The UI shows format, sizes, hashes, parser identity, an 8,000-character preview, structural summary, and typed warnings.
6. The user explicitly approves, requests changes, or rejects the exact source/extraction revision. The decision endpoint records that local-human action; background extraction and analysis jobs do not write decisions, and only an effective approval unlocks analysis.
7. An identical import reuses the current source/extraction. A changed-byte reimport or explicit re-extraction appends a revision and invalidates the effective approval without deleting its history. Restart restores the source, extraction, preview/warnings, effective decision, and analysis state; prior decision rows remain durable.

An identical request carrying the same idempotency key returns the original
result. Validation failure before source publication removes staging and leaves
the previous revision active. Extraction failure preserves the new immutable
source and its redacted attempt evidence but publishes no analyzable story or
Import Review.

## 4. Inspect and correct dialogue

1. The user selects a chapter and scene.
2. The UI displays narration and dialogue in source order, with characters, confidence, warning indicators, and source locations.
3. The user selects a dialogue line, chooses an existing character (or creates one through a separate typed action), and optionally records a reason.
4. Save includes the line's expected revision. The service atomically writes a correction event, updates the current attribution projection, increments revisions, and returns the durable value.
5. The UI marks the line as human-corrected and shows provenance.

If another edit won, the service returns `409 REVISION_CONFLICT` with the current revision. The UI preserves the user's unsaved choice and offers comparison/reapply; it never overwrites silently. Re-analysis reads corrections as protected constraints.

## 4A. Analyze and review the whole book

1. After the current Import Review is approved, the user opens Analysis and
   starts the `whole-book-intelligence-v1` profile.
2. The service transactionally rechecks source, extraction, extracted-text
   hash, approval decision, profile, and agent versions, then creates an
   immutable analysis run plus durable job.
3. The workspace shows the exact run inputs and durable stages for structure,
   beats, identity, dialogue, POV, setting, timeline, relationships,
   emotion/intent, continuity, and synthesis.
4. A complete snapshot exposes paginated chapters/scenes, character registry,
   aliases/mentions, dialogue candidates and effective speakers, POV,
   locations, narrative/story chronology, relationships, emotional/dramatic
   proposals, continuity findings, confidence, evidence, warnings, and
   provenance.
5. Low-confidence, contradictory, and unknown results remain visible. The user
   can filter them without loading every entity or the full manuscript.
6. The user appends typed corrections with a reason and expected
   revision/fingerprint. The machine proposal remains inspectable and the
   effective view labels human authority.
7. The user supplies a nonblank rationale and approves, rejects, or requests
   changes at Story Structure, Character Registry, Dialogue Attribution, and
   Whole-Book Analysis Review. A changed evidence fingerprint requires a new
   decision; unrelated gate evidence retains its decision.
8. Closing and reopening restores the run, snapshot, corrections,
   dispositions, warnings, and decisions. A rerun preserves compatible human
   authority and never silently reapproves.

The four current gate projections are queryable and form prerequisites for
Phase 3A casting eligibility. The Phase 2 boundary itself has no casting
authority; voice selection, speech generation, and audio production remain
unavailable until an explicitly implemented later phase supplies the applicable
governed boundary.

## 4B. Build and approve a governed cast

1. With Import Review and all four current Phase 2 reviews approved, the user
   opens Casting. The workspace shows prerequisite decision IDs, the current
   analysis snapshot/correction fingerprint, profile
   `governed-voice-casting-v1@1.0.0`, and synthetic catalog
   `synthetic-voice-catalog-v1@1.0.0`.
2. Starting casting submits every current snapshot, correction, gate, catalog,
   and profile precondition plus an idempotency key. The service rejects stale
   or mismatched evidence rather than applying an old cast to changed analysis.
3. A durable job progresses through Phase 2 validation, evidence freeze,
   catalog load, role creation, constraint evaluation, bounded candidates,
   metadata-only conflicts, and atomic run/snapshot publication. The user can
   cancel, retry, or resume only when the persisted job state permits it.
4. The workspace pages narrator and character roles and shows dialogue,
   narration, approximate-word, chapter, and scene workload. Unresolved
   material is a role only when the approved analysis represented it
   explicitly. Character roles retain stable Character Registry identity;
   importance and language/locale/performance requirements stay conservative
   when approved evidence is absent or conflicting.
5. For one role at a time, the user inspects at most 12 candidates. Hard
   constraints, soft preferences, language, rights/consent,
   provider/model availability, long-form suitability, confidence, warnings,
   and bounded explanations remain separate. The highest score is not selected
   automatically. Candidate warnings refer to actual persisted conflict rows.
6. The user appends a human selection and optional lock for the narrator and
   characters. Clear, unlock, intentionally uncast, label/requirement changes,
   restricted-rights acknowledgement, planned reuse approval, candidate
   rejection, and custom rationale create immutable superseding corrections;
   machine rows and older assignments remain inspectable.
7. The user reviews metadata differentiation, reuse, availability,
   suitability, and rights conflicts. The UI calls them metadata risks, never
   acoustic similarity. Unknown or prohibited rights cannot receive final
   approval; restricted rights require an exact acknowledgement.
8. The user approves Narrator Casting Review and Character Casting Review, then
   Complete Cast Review. Each decision carries a nonblank rationale,
   acknowledged warnings, current cast-snapshot revision, evidence fingerprint,
   and idempotency key.
9. Closing and reopening restores the exact catalog revision, roles, run,
   candidates, assignments, correction history, conflict dispositions,
   reviewable cast snapshot, and all three decisions. Selected
   catalog/provider/model/profile/rights drift appends durable invalidation
   evidence and system-invalidates only the affected role gate and Complete
   gate. Reverting the catalog does not restore authority; the user must
   explicitly reselect and reapprove. Unrelated changes preserve unaffected
   assignments and approvals.

This workflow uses fictional metadata only and produces no audio. There is no
audition, playback, waveform, provider credential, model download, cloud
transmission, or synthesis control in the Phase 3A workspace.

## 4C. Audition the governed cast locally

1. With Import Review, all four Phase 2 reviews, and all three Phase 3A reviews
   approved, the user opens Auditions. The service accepts only the latest
   succeeded casting run and its exact current validated immutable cast-snapshot
   manifest. It uses only that manifest's selected assignment revisions and
   their lock state, exact current leaf provider/model/profile/rights evidence,
   temporally applicable rights, and the latest eligible Phase 3A reviews with
   their exact approved human decision provenance, supersession chain, and
   warning acknowledgements.
2. The workspace shows each prerequisite, selected role/voice/right, local
   provider, runtime profile, exact model manifest, installation, and
   verification state. The deterministic provider is visibly fixture-only.
3. The user verifies and activates an installed package, creates one role-bound
   session from server-issued evidence, and saves the bounded repository-owned
   synthetic script exposed by the Phase 3B desktop. The service contract also
   accepts exact source-linked scripts, but desktop authoring for those kinds is
   deferred until a typed source-span selector exists. No model is downloaded
   implicitly.
4. The user appends or supersedes a pronunciation entry, records its human
   decision, and inspects the exact normalization and effective pronunciation
   plan before generation.
5. A durable job revalidates every Phase 2/3A decision, assignment, rights,
   catalog, model, runtime, dictionary, script, and request fingerprint. It
   never mixes evidence across cast snapshots. A newer current snapshot removes
   older-snapshot work from the current workspace, and a new same-snapshot
   Phase 3A decision tuple invalidates dependent Phase 3B authority even when
   the selected cast is unchanged. The job either
   verifies a project-private cache hit or publishes a bounded 24 kHz mono
   PCM16 WAV through atomic private storage.
6. Audio is loaded only by authenticated project/session/clip/artifact identity
   through Electron main. Playback never uses `file://`, absolute paths, or
   autoplay; pause, seek, replay, and stop remain explicit.
7. The user records per-role, narrator, character, pronunciation, and voice-
   readiness decisions in prerequisite order. Automated QC and cache evidence
   never count as human approval.
8. Assignment, rights, model/runtime, or applicable pronunciation changes
   append targeted invalidation. Unrelated entries preserve unaffected clips
   and approvals. Regeneration produces new evidence and never reapproves it.
9. Restart restores the dictionary, sessions, jobs, clips, cache/QC records,
   decisions, runtime history, and readiness projection. Phase 3B does not
   render or export a book.

Phase 3B.1 adds one narrowly governed real-provider path for the exact
allow-listed Kokoro package. The user first reviews the neutral Real Local
Voices inventory, package/runtime state, tensor hash, restricted rights and
unknown consent state, then acknowledges the exact private-audition warning.
Selecting the real voice still occurs through the Phase 3A immutable correction
workflow and requires the applicable casting gates to be explicitly reapproved;
the Auditions workspace cannot write around that history. A real session binds
the current assignment and approved cast snapshot to the exact catalog,
provider, model, package, tensor, rights record, acknowledgement, runtime, and
pronunciation evidence. The comparison workspace never autoplays and requires
an exact-clip listening attestation for a real per-role decision.

This path authorizes only bounded private local audition. It does not establish
performer consent, likeness clearance, commercial clearance, production-export
eligibility, naturalness, artistic quality, or full-book readiness. The Voice
Readiness gate remains blocked until the required current human listening
decisions exist. See [Phase 3B.1 governed real local voice activation](phase-3b1-governed-real-voice-activation.md).

## 5. Observe, cancel, retry, and resume work

1. Creating analysis or render work returns a durable job before execution begins.
2. The jobs panel consumes typed job events (with polling as a fallback) and displays state, stage, progress, attempts, and warnings.
3. `Cancel` changes the durable state to `cancel_requested`; the worker acknowledges at a safe point and finishes as `cancelled`.
4. `Retry` on a failed job creates another attempt under the same job identity, preserving the earlier error and events.
5. `Resume` on an interrupted job validates the checkpoint and current input revision. It continues when compatible; otherwise the UI explains that a clean retry is required.
6. Restarting the desktop turns abandoned work into `interrupted` and offers the same choices.

Closing the app requests graceful service shutdown. Active work checkpoints at
its safe boundary and becomes resumable `interrupted` work; the current desktop
does not present a shutdown-choice dialog. A user who wants `cancelled` rather
than `interrupted` requests cancellation before exit. Jobs do not continue
after the desktop exits.

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
- Database recovery never edits the sole copy in place. Schema migration uses
  a verified versioned sibling backup; a separate manual recovery copy may be
  timestamped. The application records the migration/recovery result.
