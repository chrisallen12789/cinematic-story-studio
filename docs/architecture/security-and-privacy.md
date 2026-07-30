# Security and Privacy

## Security objectives

Protect unpublished manuscripts, credentials, generated audio, project decisions, and the user's machine while keeping the product local-first. The application assumes untrusted imported files, provider responses, renderer content, local web pages/processes, and archive/tool output. It does not claim to defend against a fully compromised Windows account.

## Trust boundaries and threats

| Boundary/threat | Required control |
| --- | --- |
| Web content or malicious renderer calls local service (CSRF/DNS rebinding/local process) | Bind IPv4 loopback only; random OS port; per-launch 256-bit bearer token delivered by inherited pipe; authenticate every API route; no CORS; validate `Host`; token retained only in Electron main. |
| Renderer compromise reaches OS | Sandbox and context isolation; no Node integration; restrictive CSP/navigation; allow-listed validated preload IPC; main owns files, credentials, and HTTP. |
| Malicious TXT/DOCX/EPUB/PDF escapes staging or exhausts resources | Streamed size/type checks; strict decoding; canonical package paths; reject absolute/drive/`..`/links/devices; fixed source/archive/text/section/page budgets; parent-supervised spawned parser; 30-second hard wall-clock deadline; exact-owned-process cancellation; Windows Job Object kill-on-close and 768 MiB per-process memory ceiling. |
| Filename/story/provider data becomes a command | Direct executable plus validated argument array, `shell=false`, private working directory, bounded environment/output/time, no user-controlled filter text. |
| Secret/manuscript leaks through DB, logs, telemetry, crash reports, Git, or cloud | OS credential store; no secret columns; allow-listed redacted structured logs; no request bodies/source excerpts; telemetry off in Phase 0; ignore/scan private artifacts; explicit cloud disclosure/approval. |
| Tampered provider/tool output corrupts project | Treat as untrusted; schema/size/decode/hash/QC validation; immutable staging; transactional/atomic publication; provenance. |
| Lost update or agent overwrites human decision | Optimistic revisions, append-only corrections/approvals, protected decisions in agent inputs, conflict response. |
| Dependency/installer/model tampering | Lockfiles/hashes, dependency review and secret scanning, SBOM/provenance, verified bundled resources, signed release requirement, no automated test downloads. |
| Data loss or ransomware-like application behavior | Project-scoped path capabilities, no arbitrary deletion API, preview/confirm destructive scope, migration backup, bounded cleanup, never repair sole copy in place. |
| Denial of service | Request/upload/event/concurrency limits, timeouts, worker pools, provider circuit limits, disk preflight, bounded retries/logs. |

## Local service authentication

Production bootstrap follows [desktop-runtime.md](desktop-runtime.md): Electron sends the secret through an inherited control pipe, not command-line arguments, environment, disk, renderer, or logs. The backend binds `127.0.0.1:0`, validates the bootstrap nonce, and requires constant-time bearer-token comparison. It refuses unsafe bind configuration in packaged mode.

Development requires an explicit non-placeholder `CSS_DEV_TOKEN` with `CSS_DEV_MODE=1`, still loopback-only. This is a convenience for local tooling, not a production credential scheme. API documentation/debug routes are disabled in packages. Responses have `Cache-Control: no-store`; sensitive values are not placed in URLs.

## Data classification and handling

| Class | Examples | Handling |
| --- | --- | --- |
| Restricted | manuscript/source text, provider prompts/responses, generated audio, project DB/backups, credentials | Private app/project roots; never Git/telemetry/general logs; credentials only OS store; cloud only after scoped approval. |
| Sensitive metadata | story/project filenames, character names, provider request/cost history, personal paths | Minimize in UI/logs; use opaque IDs/sanitized path categories; project retention/deletion. |
| Operational | versions, stable error/state codes, aggregate timings without content | Allowed in redacted diagnostics; telemetry remains opt-in if ever added. |
| Public | synthetic fixtures, public docs/contracts | May be committed after review/secret scan. |

Encryption at rest is provided by the user's Windows account/device protections; SQLite is not assumed encrypted in Phase 0. The product documents that limitation and recommends BitLocker. A future encryption feature requires key lifecycle, recovery, backup, migration, and performance design; a hard-coded key is prohibited.

## Credentials and cloud disclosure

Provider secrets are set/read/delete through Windows Credential Manager (or an equivalent reviewed OS facility). The database contains an opaque reference and non-secret settings only. Secrets are never returned to renderer after entry, included in diagnostics, or passed on command lines.

Cloud adapters default disabled. Before story text/audio/metadata leaves the machine, a durable decision records provider, destination class, content category, purpose/scope, policy/retention link shown, project, actor, and time. Only minimum approved data is transmitted. Health checks send no content. Consent is renewed for a changed provider/category/purpose/policy and can be revoked.

## File, path, process, and network controls

- Native picker selection is streamed; the service never accepts a renderer-controlled arbitrary read path.
- Application records verified relative paths under typed roots. Resolve/canonicalize before use and re-check after creating/opening; reject links/reparse points where they can cross roots.
- Imports and provider/tool artifacts enter unique restrictive staging, are validated/hashes checked, and publish atomically.
- The import boundary applies its separately named source-size ceiling, then document adapters apply the fixed `secure-ingest-v1` archive/text/section/page/deadline/process-memory profile. Each extraction runs in a spawned child with bounded typed IPC. On Windows, a named Job Object applies kill-on-close ownership and a 768 MiB per-process ceiling to the launcher and actual parser target. Adapters disable XML entity/network/DTD behavior, never execute document active content, and never fetch EPUB/PDF references. This is not a low-integrity process or an OS-enforced outbound-network sandbox. See the [document ingest threat model](../security/document-ingest-threat-model.md).
- Downloads/models, if later supported, require explicit action, size/hash/signature/license metadata, safe staging, and cancellation. CI never downloads large models.
- FFmpeg and local runtimes are invoked only through reviewed wrappers with safe arrays, resource bounds, sanitized output, and verified owned-process termination.
- No inbound listener other than authenticated loopback. Outbound network is adapter-specific, policy-gated, TLS-validated, and destination allow-listed where practical.

## Logs, diagnostics, and crash handling

Logs are structured with stable event/error codes, UTC time, component, correlation/project/job/attempt opaque IDs, and bounded numeric/enum metadata. They exclude HTTP bodies, source excerpts, prompts/responses, audio, token/secret fields, raw provider errors, and absolute user paths. Sanitization occurs before any sink; rotation and retention are bounded.

Diagnostics export is explicit, previewable, and uses an allow-list—not "redact after collecting everything." Crash reporting/telemetry is off in Phase 0. Future telemetry must be opt-in, content-free, documented, deletable where supported, and threat-reviewed.

## Deletion

Project deletion and cache/export cleanup enumerate exact application-managed roots, block/stop active jobs, preserve user-selected exports unless separately chosen, and are idempotent. Credential deletion respects shared references. Temporary files are cleaned after failures/startup.

SQLite secure delete/VACUUM and file removal cannot guarantee forensic erasure on SSDs, backups, sync services, or restore points; the UI states this. The application never recursively deletes a broad/unresolved path or follows links outside the project root.

## Secure development and release

- Public-repository ignores cover `.env`, databases, sources, audio, models, caches, logs, staging, dependencies, builds, installers, and prototype extraction.
- CI runs lint/type/tests/build plus secret scanning. Pull requests run GitHub
  dependency review with a moderate-severity failure threshold. The scheduled
  security workflow scans repository policy and Git history for private
  content/secrets; it is not a scheduled dependency-vulnerability audit.
- Lock dependencies and justify additions. Generate an SBOM and preserve build provenance for distributable artifacts.
- Production installers/executables require trusted code signing and signature verification before public distribution; signing secrets live only in protected CI.
- Security tests cover auth/binding, renderer isolation/IPC validation, traversal/archive bombs, import limits, revision conflicts, safe subprocesses, redaction, credential persistence, project deletion, and corrupted artifacts/database recovery.

Report vulnerabilities privately as described in `SECURITY.md`; never include real credentials or manuscripts in a report.
