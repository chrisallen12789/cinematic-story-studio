# Prototype v3 security review

**Audit date:** 2026-07-29

**Decision:** **Do not run, import, or migrate the prototype source. Clean-room rewrite required.**

## Review boundary and threat model

This review covers the 29-file external artifact inventoried in
`prototype-v3-inventory.md`. The artifact was inspected but never executed.
Relative line references below refer to the external prototype, not files committed
to this repository.

Protected assets are private manuscripts, human corrections, provider credentials,
generated speech and sound, models, local filesystem state, and paid provider
operations. Relevant adversarial inputs include imported documents, project JSON,
browser origins able to reach loopback, provider responses, voice metadata,
configuration changes, model files, and concurrent local requests.

Severity meanings:

- **Critical:** credible credential/private-data disclosure or command execution.
- **High:** material integrity, privacy, cost, or denial-of-service exposure.
- **Moderate:** defense-in-depth, reproducibility, or operational weakness.

## Findings

### PV3-SEC-01 — Critical — The legacy server publishes its entire application root

**Evidence:** `voice_hub_server.py:1209-1226` resolves any requested static path
under its root and returns the bytes. Sensitive files are intentionally located in
that same root (`voice_hub_server.py:24-30`).

When the standalone Voice Hub server runs, predictable URLs can return
`voice_hub_config.json`, `book.json`, project JSON, source, and any known cache
artifact. The config can contain plaintext credentials and the book/project files
contain a full private manuscript. Loopback binding reduces network reach but does
not protect against local processes, DNS rebinding, a compromised renderer, or a
future binding/configuration regression.

**Required disposition:** reject generic root static serving. Serve an explicit
asset allowlist from a content-free directory. Keep project data and secrets outside
web roots and require authenticated, purpose-specific APIs for private data.

### PV3-SEC-02 — Critical — Mutable loopback APIs have no authentication or origin/host defense

**Evidence:** `cinematic_story_server.py:994-1149` and
`voice_hub_server.py:1094-1207` accept reads and state-changing POSTs without a
session capability, `Host` validation, `Origin` validation, CSRF protection, or
content-type enforcement.

Reachable callers can overwrite or delete projects, change provider settings,
trigger cloud analysis/synthesis and cost, render private text, or launch the render
folder. A simple JSON body is accepted regardless of media type. Modern browser
private-network controls are not a durable application security boundary.

**Required disposition:** use an ephemeral per-launch capability shared only with
the desktop shell, validate `Host` and `Origin`, accept a strict JSON media type and
schema, disable ambient browser access, and authorize each mutating operation.

### PV3-SEC-03 — Critical — Configurable endpoints can become SSRF and credential-exfiltration sinks

**Evidence:** the config setter accepts arbitrary existing fields
(`voice_hub_server.py:1160-1174`; the studio has an equivalent merge at
`cinematic_story_server.py:1119-1135`). Azure accepts an arbitrary endpoint and
sends the stored subscription key to it (`voice_hub_server.py:227-242,529-551`).
The local OpenAI-compatible adapter accepts an arbitrary base URL and sends its
configured bearer value (`voice_hub_server.py:406-431,685-698`).

An attacker who can mutate config can redirect the next list or synthesis request
to an attacker-controlled or internal-network endpoint. Existing stored credentials
then travel in request headers. The same primitive permits local-network discovery.

**Required disposition:** provider endpoints must be immutable allowlisted HTTPS
origins. A local adapter may accept only validated loopback addresses and must not
reuse cloud credentials. Endpoint changes require explicit desktop approval and
must never be accepted through an unauthenticated web API.

### PV3-SEC-04 — Critical — Windows SAPI builds executable PowerShell from story text

**Evidence:** `voice_hub_server.py:724-752` serializes the selected voice, story
text, and temporary path as JSON strings, interpolates them into a PowerShell
program, and invokes `powershell.exe -Command`.

JSON quoting is not PowerShell quoting. PowerShell expands expressions inside
double-quoted strings, and backslash does not escape a quote in PowerShell. An
imported passage or provider-controlled voice identifier can therefore alter or
execute the generated program when Windows SAPI is selected.

**Required disposition:** reject this implementation completely. The installed
application must not require PowerShell. Use an in-process/native typed API or a
fixed helper protocol that passes data over stdin/IPC without source-code
interpolation.

### PV3-SEC-05 — High — Credentials are stored in plaintext beside content and code

**Evidence:** `voice_hub_server.py:80-108` reads/writes JSON and merely blanks
secret fields in the public representation. The historical architecture explicitly
places API credentials in local config files. The settings API writes supplied
secrets back to that file.

The reviewed artifact had empty cloud credential slots; one non-empty local
placeholder was not a demonstrably live secret. This does not make the design safe.

**Required disposition:** store secrets in Windows Credential Manager/DPAPI-backed
storage, retain only opaque references in application data, redact structured logs
and errors, and support rotate/delete without rewriting a general config document.

### PV3-SEC-06 — High — Project data can produce stored same-origin script execution

**Evidence:** `cinematic_index.html:120,128,130,132-133` builds markup with
`innerHTML`. Several attribute values and numeric/provider fields are not escaped.
`cinematic_story_server.py:1086-1089` accepts and persists an essentially untyped
project object, including its raw ID.

A crafted saved project or provider response can become stored XSS when the studio
reopens it. Same-origin script can read the manuscript, mutate projects, and invoke
provider operations.

**Required disposition:** render untrusted values with framework text bindings or
`textContent`, validate all domain objects at the service boundary, use a restrictive
CSP in the desktop renderer, and never enable Node or privileged desktop APIs in
untrusted content.

### PV3-SEC-07 — High — Imported containers and request bodies are insufficiently bounded

**Evidence:** the studio permits an 80 MiB JSON request
(`cinematic_story_server.py:1009-1013`) and then decodes base64 in memory. DOCX and
EPUB ZIP members are read without member-count, expanded-size, compression-ratio,
or total-text limits (`:161-201`). PDF parsing has no page, time, or memory budget
(`:204-218`). The Voice Hub has no body-size limit at all
(`voice_hub_server.py:1109-1111`).

**Required disposition:** stream uploads to a private staging area; validate magic,
extension, size, member count, expanded bytes, path names, pages, and parsing time;
isolate complex parsers; and delete staging data deterministically.

### PV3-SEC-08 — High — Provider/model configuration controls process and filesystem targets

**Evidence:** Piper recursively scans a configurable model directory
(`voice_hub_server.py:434-444`) and launches a configurable executable against a
selected model (`:701-721`). Both fields are writable through the broad config API.

An absolute model root can escape the application data directory, and an executable
path can select an unintended local program. No model digest, format constraint,
size limit, source, or license record is checked.

**Required disposition:** use a fixed application-owned helper, a validated
application-owned model root, signed/digested model manifests, strict file-size and
format checks, and explicit license/provenance metadata.

### PV3-SEC-09 — High — Private cloud egress lacks a durable disclosure and approval record

**Evidence:** selected scene text (up to 18,000 characters) is sent to the OpenAI
Responses API (`cinematic_story_server.py:570-630`). Speech text is sent to the
selected TTS provider (`voice_hub_server.py:489-698`). Sound prompts can be sent to
ElevenLabs (`cinematic_story_server.py:790-812`).

Buttons and provider selection imply intent, but the project has no durable record
of exactly which content, provider, model, purpose, retention setting, estimated
cost, or user approval authorized each transmission.

**Required disposition:** default to local processing. Before every new cloud data
class/provider, show a scoped disclosure and record approval, payload classification,
provider/model/version, retention choice, cost estimate, request ID, and result
provenance. Never send full content for a health check.

### PV3-SEC-10 — High — Project/config storage is untyped, non-atomic, and race-prone

**Evidence:** projects and configs are complete mutable JSON documents written
directly with `write_text`; the service is a `ThreadingHTTPServer`
(`cinematic_story_server.py:71-107,1152-1155`). Save accepts caller-controlled
objects and has no schema version, revision, transaction, lock, or atomic replace.
Render paths derive from sanitized titles and may collide.

Concurrent edits can be lost or corrupt a file. A sanitized ID can alias another
raw ID. Human corrections have no separate durable provenance and can be silently
overwritten by a later full-document save.

**Required disposition:** use versioned typed contracts and transactional SQLite,
optimistic revisions, immutable source records, append-only correction provenance,
stable UUIDs, project-scoped render directories, and atomic job state.

### PV3-SEC-11 — High — Private audio and outputs are retained globally and indefinitely

**Evidence:** synthesized speech is cached by a hash of provider/request text and
written under a global root (`voice_hub_server.py:765-791`). Generated sound is
similarly cached (`cinematic_story_server.py:814-825`). Render and open-folder
responses return absolute local paths. Audio responses can be browser-cached for a
year (`voice_hub_server.py:1141-1154,1178-1189`).

The hash hides the text in a filename but does not protect the audio. There is no
project isolation, retention control, quota, secure deletion, ACL hardening, or
cache manifest.

**Required disposition:** use project-scoped private storage, opaque IDs, restrictive
ACLs, quotas and retention controls, `no-store` for private media, and a versioned
cache/render manifest without personal paths in APIs or logs.

### PV3-SEC-12 — High — Dependency and container provenance is not reproducible

**Evidence:** install scripts run unpinned `pip install --user --upgrade` and an
unpinned Winget install. The launcher runs a mutable Docker `latest` image and
publishes its port without a loopback host qualifier. No dependency manifest, lock,
hash, signature, license inventory, or SBOM exists.

**Required disposition:** pin and hash dependencies, review licenses, generate an
SBOM, pin container digests for development only, bind development services to
loopback, and do not require Docker or PowerShell for the installed application.

### PV3-SEC-13 — Moderate — Source preservation and provenance claims are incomplete

**Evidence:** TXT decoding keeps a decoded source string, but derived scene/event
text normalizes whitespace and strips structural punctuation
(`cinematic_story_server.py:253-346`). DOCX/EPUB/PDF imports store extracted text,
not the original bytes. RTF is treated as plain text despite not being advertised.
The private seeded project has an empty top-level source field. UI edits mutate
render text without correction history.

**Required disposition:** retain original imported bytes and a digest, preserve a
character-accurate canonical source, store derived spans by offsets, make edits
explicit overlays, and record analyzer/provider/version/confidence/warnings.

### PV3-SEC-14 — Moderate — Render reproducibility, governance, and job control are absent

Procedural noise has no recorded seed. AI plans and audio outputs lack complete
provider/model/version/input manifests. Cache identity cannot capture an upgraded
remote service behind unchanged config. Long renders run synchronously in
unbounded handler threads with no queue, cancellation, retry policy, cost ceiling,
or resumable checkpoint.

**Required disposition:** introduce durable versioned jobs, bounded concurrency,
cancellation and retry policies, deterministic seeds, content/config digests, and
production/render/provenance manifests.

### PV3-SEC-15 — Moderate — Errors, paths, and browser defenses leak unnecessary detail

Provider response bodies and subprocess stderr are returned in error strings;
render/open-folder APIs return personal absolute paths. Responses lack CSP,
`X-Content-Type-Options`, frame protection, and a strict referrer policy. Static
private media uses long-lived cache headers.

**Required disposition:** map internal failures to redacted typed errors, retain
details only in redacted structured local logs, return opaque resource IDs, and set
desktop/web security headers appropriate to every response.

## Positive observations to preserve

These controls are useful evidence, not approval of the architecture:

- Both Python servers bind explicitly to `127.0.0.1`.
- Render download paths and Piper model selections attempt containment checks.
- Most subprocess calls use argument arrays with `shell=False`.
- Public config responses blank known secret fields and expose only presence flags.
- JSON API responses generally use `no-store`.
- The AI Director defaults remote response storage off and requests strict
  structured output.
- Unknown dialogue is exposed as unassigned instead of silently asserted.
- Temporary document/audio files generally use cleanup blocks.
- Known-format scanning found no demonstrably live credential in the artifact.

## Gate blockers and exit criteria

The prototype source cannot cross the gate while PV3-SEC-01 through PV3-SEC-12 are
unresolved. Phase 0 may use only the sanitized, non-executable examples under
`prototypes/v3-reference/`.

Before any equivalent production feature is accepted, executable tests must prove:

1. only allowlisted assets are served and private roots are unreachable;
2. loopback APIs reject missing/invalid launch capabilities, hosts, origins,
   content types, and schemas;
3. secrets remain in OS-backed storage and never appear in files, responses, logs,
   URLs, or provider endpoint overrides;
4. malicious document, project, voice, and story-text fixtures cannot escape parser,
   renderer, model, or subprocess boundaries;
5. original source and durable human corrections survive restart and reanalysis;
6. cloud transmissions require scoped disclosure and produce complete provenance;
7. caches, jobs, and renders are private, bounded, resumable, and deterministic
   where promised; and
8. the installed desktop application launches without Docker or PowerShell.

The external extraction still contains private material. After the repository owner
confirms no further evidence is needed, it should be removed through an approved
local cleanup workflow; it must never be committed, attached to an issue, or copied
to build/test fixtures.
