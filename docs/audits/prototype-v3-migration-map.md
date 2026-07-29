# Prototype v3 migration map

**Audit date:** 2026-07-29

**Migration rule:** preserve useful product intent, not prototype implementation.

The prototype failed the source migration gate. Every executable capability below
must be implemented behind the repository's typed desktop, service, storage,
provider, agent, and render boundaries. `prototype-v3-security-review.md` is
authoritative for controls that must not be inherited.

Disposition terms:

- **Phase 0 rewrite:** required by the initial vertical slice; implement cleanly now.
- **Preserve concept:** retain the product behavior but redesign the implementation.
- **Defer:** useful, but outside Phase 0; define an interface only when needed.
- **Reject:** misleading, unsafe, private, or product-specific behavior that must not
  migrate.

## Projects, import, analysis, and correction

| Prototype capability and evidence | Disposition | Target boundary and migration requirement | Acceptance evidence |
| --- | --- | --- | --- |
| Project library, open/current selection (`cinematic_story_server.py:88-125`; UI `:119-125`) | Phase 0 rewrite | Desktop calls a typed project service backed by transactional SQLite. Use UUIDs and revisions; current UI selection is desktop preference, not a plaintext project ID file. | Create/list/open survives restart; concurrent revision conflict is explicit. |
| TXT and Markdown upload (`:152-158,221-231`) | Phase 0 rewrite | Import service stores original bytes, encoding decision, digest, source filename, and canonical text without normalization. | Synthetic UTF-8/BOM/Windows-1252 fixtures round-trip exactly as specified. |
| DOCX import (`:161-178`) | Defer | Sandboxed document adapter with archive budgets and an immutable original artifact. | Adversarial archive tests plus paragraph/span provenance. |
| EPUB import (`:181-201`) | Defer | Standards-aware adapter follows package/spine order; bounded ZIP/XML parsing. | Multi-spine and hostile-container fixtures. |
| PDF import (`:204-218`) | Defer | Isolated, bounded parser; distinguish extracted text from original artifact and warn on OCR/layout loss. | Page/time/size budgets and extraction-confidence report. |
| Latent RTF-as-plain-text behavior (`:221-231`) | Reject | Do not advertise or accept RTF until a real bounded adapter exists. | Unsupported-format typed error. |
| Chapter heading heuristics (`:234-282`) | Phase 0 rewrite | Versioned deterministic analyzer emits source spans, confidence, and warnings; it never changes the source. | Golden synthetic fixtures, stable ordering, analyzer version recorded. |
| Scene-break and long-scene splitting (`:285-306`) | Phase 0 rewrite | Versioned scene analyzer returns non-overlapping source spans and reasons for every boundary. | Boundary coverage/no-loss invariants and deterministic rerun. |
| Narration/dialogue event segmentation (`:324-346`) | Phase 0 rewrite | Derived events reference immutable source offsets. Quote punctuation and whitespace remain in source; render text is an explicit projection. | Concatenated spans cover source; no invented or dropped text. |
| Conventional speech-tag inference (`:309-321`) | Preserve concept | Speaker-attribution agent emits candidate, confidence, evidence span, warnings, and human-review requirement. | Ambiguous synthetic cases remain uncertain. |
| Explicit `Unassigned Dialogue` state | Phase 0 rewrite | Typed nullable/uncertain speaker assignment, not a magic character string. | Ambiguous dialogue visibly requires review. |
| Detected character/cast list and default voice assignment (`:429-489`) | Preserve concept | Analysis proposes characters; casting is a separate human-editable projection. Never infer gender from ordering or silently assert identity. | Proposal confidence/warnings and user approval are persisted. |
| Full JSON project persistence (`:491-508`) | Reject implementation | Normalize typed project/source/analysis/correction/job records in SQLite with schema migrations and transactions. | Restart, migration, rollback, and corruption-recovery tests. |
| Edit event type, speaker, text, cast, scene design, and cues (UI `:128-140`) | Preserve concept | Separate immutable source from editable render overlays. Every human correction records author, time, before/after, source span, and revision. | Reanalysis cannot overwrite an accepted correction; undo/history is inspectable. |
| Delete project (UI `:124`; server `:1091-1095`) | Phase 0 rewrite | Explicit desktop confirmation; transactional deletion plan covers source, cache, jobs, renders, and secure-store references. Prefer recoverable deletion where feasible. | Exact project scope and recovery/retention result are reported. |
| Private seeded book/project (`cinematic_story_server.py:511-557`) | Reject | Use synthetic fixtures only. No automatic seeding on import or module load. | Repository private-content scan remains clean. |

## Direction and user experience

| Prototype capability and evidence | Disposition | Target boundary and migration requirement | Acceptance evidence |
| --- | --- | --- | --- |
| Deterministic mood, ambience, score, and sparse SFX rules (`:349-418`) | Preserve concept | Versioned local direction agent with typed inputs/outputs, confidence, warnings, editable proposals, and deterministic rule-set identity. | Stable golden outputs; source text unchanged. |
| Editable title/summary/mood/intensity/prompts/cues (UI `:60-71,133-139`) | Preserve concept | Typed scene-plan editor with validation, provenance, accessibility, and durable approval state. | Keyboard/screen-reader checks and persistence tests. |
| Optional structured AI scene direction (`:570-636`) | Defer cloud implementation | Provider-neutral agent contract first. A cloud adapter requires explicit disclosure, approval, cost/retention metadata, response validation, and model provenance. | Local fake adapter exercises approval/retry/failure paths without network. |
| Five-stage project/cast/direction/render/settings navigation | Preserve concept | Electron renderer uses accessible components and a constrained preload API; no direct service fetch from arbitrary web content. | Keyboard flow and desktop-to-service integration test. |
| Project and scene list/detail layout | Preserve concept | Derive from typed view models and render untrusted values as text, never HTML. | Stored-XSS fixtures remain inert. |
| Provider status, voice search/filter, language/gender metadata, preview links (legacy UI `:58-80`) | Preserve concept | Provider registry returns normalized, provenance-tagged voice metadata. Remote preview URLs are proxied/allowlisted or opened with explicit consent. | Malformed provider metadata cannot inject markup or navigate privileged content. |
| Voice audition for a selected cast entry | Preserve concept | Generate a synthetic, non-story audition phrase through the provider adapter after cost/privacy disclosure. Never embed proprietary sample text. | Local fake and configured-provider contract tests. |
| Browser speech playback and saved position (`Legacy_Standalone...:134-319`) | Defer | Optional accessibility/preview adapter only; it is not a render source. Store playback preference separately from project truth. | Unsupported recording is explicit; position/speed survives restart. |
| Sleep timer and wake lock | Defer | Desktop playback concern with OS-aware lifecycle cleanup. | Timer cancellation and wake-lock release tests. |
| Faux percentage progress (studio UI `:143`) | Reject | Progress must come from durable job stages/units, not fixed percentages. | Restart resumes accurate job state. |

## Voice-provider capability map

All providers must implement a common typed interface for health, capabilities,
catalog, synthesis request/result, privacy classification, pricing metadata,
timeouts, retries, cancellation, provenance, and redacted errors. Phase 0 needs the
interface, local fake, and health path; it does not need every provider.

| Prototype provider | Evidence | Disposition | Migration requirement |
| --- | --- | --- | --- |
| Browser/Edge/Windows Web Speech | `voice_hub_server.py:47-77`; legacy client Web Speech | Defer | Playback-only desktop adapter; never claim renderability. |
| Windows SAPI | `:447-472,724-752` | Defer; reject PowerShell code | Native/fixed helper with data IPC, no source interpolation or PowerShell. |
| Local OpenAI-compatible/Kokoro | `:406-431,685-698` | Preserve concept | Loopback-only typed adapter, strict endpoint validation, health/version identity; installed app cannot require Docker. |
| Piper local models | `:434-444,701-721` | Defer | Fixed helper and app-owned verified model manifests; no arbitrary executable/root. |
| OpenAI TTS/custom voices | `:164-189,489-507` | Defer cloud adapter | OS-backed secret, explicit data disclosure, current documented API contract, model/voice provenance. |
| ElevenLabs TTS | `:192-224,510-526` | Defer cloud adapter | Paginated catalog, bounded calls, terms/license/cost metadata, OS-backed secret. |
| Azure Speech | `:227-254,529-552` | Defer cloud adapter | Fixed HTTPS regional endpoint construction; no arbitrary credential-bearing endpoint. |
| Google Cloud TTS | `:257-271,555-577` | Defer cloud adapter | Avoid secrets in general config; typed language/voice request and redacted transport metadata. |
| Amazon Polly | `:274-308,580-598` | Defer cloud adapter | Standard credential chain/OS store, least privilege, pinned SDK, engine capability checks. |
| Deepgram Aura | `:311-313,601-611` | Defer cloud adapter | Catalog must be fetched or checked in with source, license, date, and version; do not copy prototype catalog. |
| Murf | `:315-337,614-642` | Defer cloud adapter | Allowlist returned audio location before fetching; capture model/style/cost provenance. |
| Cartesia | `:340-376,645-663` | Defer cloud adapter | Pin supported API contract through maintained SDK/docs; validate owned/public voice scope. |
| PlayHT | `:379-403,666-682` | Defer cloud adapter | Treat both API secret and account identifier as protected; normalize catalog safely. |

The prototype's Deepgram and Kokoro catalog JSON files are not migration inputs:
their source, license, retrieval date, and supported-version relationship are
unknown.

## Audio, sound, render, and playback

| Prototype capability and evidence | Disposition | Target boundary and migration requirement | Acceptance evidence |
| --- | --- | --- | --- |
| Provider-neutral synthesis relay (`voice_hub_server.py:755-791`) | Preserve concept | Typed provider service; bounded text, voice, speed, format, timeout, cancellation, cost, and provenance. | Contract suite runs against fake/local adapters. |
| Content-addressed speech cache (`:765-791`) | Preserve concept | Project-private cache with a versioned manifest, provider/model/version/input/config digests, ACLs, quota, retention, and purge. | Hit/miss correctness, isolation, invalidation, and purge tests. |
| Procedural ambience and score (`cinematic_story_server.py:692-758`) | Preserve concept | Deterministic local sound provider with explicit seed/version and typed prompt categories. | Same manifest reproduces the same bytes where promised. |
| Procedural Foley/SFX (`:759-788`) | Preserve concept | Local provider with bounded duration/loudness and honest quality label. | Duration/format/loudness assertions on synthetic prompts. |
| ElevenLabs generated SFX (`:790-812`) | Defer cloud adapter | Separate sound-generation contract and approval/cost gate; clean all intermediate files. | Fake adapter proves consent, retry, failure, and cleanup. |
| FFmpeg discovery and clip normalization (`:639-667`; Voice Hub `:803-884`) | Preserve concept | Fixed verified binary/package, argument arrays, bounded subprocess, redacted stderr, explicit 44.1 kHz mono PCM contract. | Executable integration test records version and output format. |
| Speech-block grouping and pause policy (`voice_hub_server.py:813-861,887-938`) | Preserve concept | Versioned performance-plan stage; preserve source spans and record pause rules. | Deterministic timing manifest and no-text-loss invariant. |
| Event start calculation and cue placement (`cinematic_story_server.py:828-909`) | Preserve concept | Typed timeline in integer samples/timebase; validate cue targets and bounds. | Sample-accurate synthetic fixture. |
| Background loop/trim, music fades, and dialogue ducking (`:880-917`) | Preserve concept | Typed mix graph compiled by the render boundary; no user strings interpolated into filter syntax. | Executable mix test measures ducking/fades. |
| Loudness normalization and MP3 encoding (`:917-944`) | Preserve concept | Versioned mastering profile and render manifest record FFmpeg, codec, bitrate, loudness target, inputs, and outputs. | Loudness/peak/codec assertions on deterministic fixture. |
| Render selected scene (`:851-945`) | Preserve concept | Durable render job with project/revision snapshot, resumable stages, cancellation, and isolated output. | Interrupt/resume test and stable manifest. |
| Render chapter by composing scenes (`:948-975`) | Preserve concept | Durable chapter job consumes approved scene renders and inserts versioned scene pauses. | Failure resumes without regenerating valid completed scenes. |
| Book-specific two-role chapter renderer (`voice_hub_server.py:982-1071`) | Reject implementation | General renderer consumes typed project/cast/timeline; no hardcoded work, role, chapter count, artist, or album metadata. | Synthetic multi-character project renders without special cases. |
| Render all chapters sequentially (legacy UI `:192-210`) | Preserve concept | Bounded production queue with pause/resume, per-chapter status, retry, cost ceiling, and approval gate. | Restart and partial-failure integration test. |
| Cover attachment and audio metadata | Preserve concept | Project-owned optional asset with rights/provenance, format/size validation, and user-authored metadata. Never use prototype artwork. | Synthetic image fixture and metadata verification. |
| Audio preview/download and rendered-file list | Preserve concept | Desktop resolves opaque render IDs through a private service. Responses use `no-store`; no absolute paths. | Unauthorized/other-project IDs fail; player loads authorized output. |
| Open render folder | Preserve concept | Narrow desktop-shell command for a validated project render directory; service never launches arbitrary paths. | Path containment and platform integration test. |
| Cache statistics | Preserve concept | Report project-scoped byte/count/quota data from manifests without filenames or content. | Counts match storage and expose no private paths. |
| Local sound-library placeholder | Defer | Asset index requires license, source, digest, tags, and project-use record before any file is selectable. | Unlicensed/unindexed assets cannot enter a render. |

## Platform and operations

| Prototype capability and evidence | Disposition | Target boundary and migration requirement |
| --- | --- | --- |
| Loopback Python HTTP service | Preserve concept | Local service binds loopback, uses an ephemeral launch capability, validates host/origin/schema, and exposes only typed endpoints. |
| Inline static HTML application | Reject implementation | Electron renderer/preload/main separation with CSP, context isolation, no Node integration, accessible components, and typed IPC/client. |
| JSON config files | Reject production use | Typed non-secret preferences in app data; credentials in OS-backed secure storage; schema version/migration and validation. |
| `current_project.txt` and browser `localStorage` | Reject as project truth | Desktop preference store may remember UI selection/speed; durable domain state remains transactional. |
| Import-time directory creation and private seeding | Reject | Explicit application bootstrap/migrations, idempotent and testable, with synthetic data only. |
| Docker-first launcher and mutable image | Reject for installed app | Docker may be an opt-in development profile only; installed local providers use supported packaged/user-managed adapters. |
| PowerShell and batch launch/install scripts | Reject for installed app | Packaged desktop launch; developer scripts must be pinned, non-interactive where possible, and documented. |
| Provider settings/status | Preserve concept | Desktop settings service separates health from credentials and never transmits story text during health checks. |
| Request/subprocess console logs | Preserve observability concept | Structured redacted logs with event IDs, severity, retention, and zero story text/secret/path leakage. |
| Synchronous handler-thread renders | Reject | Durable bounded job scheduler with progress, cancellation, retry, pause/resume, and crash recovery. |
| Duplicate historical readmes/architecture notes | Reject copy | Product, architecture, security, and operator docs are maintained from current contracts and decisions. |

## Data migration map

No private prototype record will be imported into repository fixtures. If the owner
later authorizes a local-only user migration tool, it must transform data through
validated contracts and never upload or log content.

| Prototype data | Target representation |
| --- | --- |
| project `id`, title, timestamps | UUID project row with revision and schema version |
| `source_filename`, `story_text` | immutable source artifact, digest, encoding/media type, canonical text, import provenance |
| chapters/scenes with copied source text | ordered analysis nodes referencing immutable source spans |
| event `type`, `speaker`, `text` | typed derived event with source span, attribution confidence/warnings, optional render-text overlay |
| characters and provider voice blobs | character entity plus versioned casting assignment and normalized provider voice reference |
| human speaker/text/cast edits | append-only correction/approval provenance linked to project revision |
| mood/intensity/prompts/cues | versioned scene-plan proposal and explicit human approval |
| `render_settings` and cinematic config | typed project production settings with bounded values and schema migration |
| provider secrets | OS secure-store item referenced by opaque credential ID |
| provider/model/voice results | versioned provider capability and provenance records |
| hashed audio cache files | project-private cache entries with full input/config/provider digests and retention state |
| MP3 paths | opaque render artifact IDs plus production/render manifests and digests |
| current project and playback position | non-sensitive desktop preference keyed by project UUID |

## Reference subset and exclusions

The repository contains only:

- a prominent quarantine README;
- a non-secret historical cinematic-settings example; and
- a voice-provider settings example with all protected values and story-specific
  instructions removed.

No Python, HTML, JavaScript, batch, PowerShell, provider catalog, manuscript,
project, cover, WAV, bytecode, model, cache, render, local-state file, or personal
path was copied.

## Phase 0 completion checks derived from the prototype

The prototype forensics gate is complete when the repository can demonstrate, with
synthetic content only:

1. create/list/open a durable project;
2. import TXT/Markdown while preserving the source and digest;
3. produce versioned chapter, scene, narration, dialogue, and character proposals;
4. expose uncertain attribution and persist a human speaker correction as durable
   provenance;
5. resume bounded background analysis after interruption;
6. report local/fake provider health without cloud credentials or story egress;
7. enforce authenticated loopback, typed storage/API, safe paths/subprocesses,
   redacted logs, and private untracked data roots; and
8. build the unpackaged Electron desktop without Docker or PowerShell as an
   end-user requirement.

Audio quality, cloud providers, complex document formats, model downloads,
full-chapter production, and release packaging remain later gates and must not be
claimed from prototype evidence.
