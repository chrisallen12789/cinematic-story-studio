# Local Service

## Role

The local service is the authoritative application/use-case boundary. It exposes a small versioned FastAPI API, validates all input with Pydantic, coordinates transactions and background work, and delegates through explicit domain ports. It is not a general-purpose local web server and is never a LAN service.

## Startup and composition

Startup order is:

1. validate production bootstrap or explicit development mode;
2. force host to `127.0.0.1` and choose port `0` in production;
3. acquire a single-instance storage/migration lock;
4. create a pre-migration backup when required and apply transactional migrations;
5. verify SQLite foreign keys/integrity and private data directories;
6. mark abandoned job leases `interrupted`;
7. register local/provider/tool adapters without making availability a startup requirement;
8. start the persistent worker and emit the readiness record;
9. serve authenticated requests.

Configuration is typed and allow-listed. Production ignores unsafe host, port, token, docs/debug, reload, and CORS environment overrides. OpenAPI and interactive docs are disabled in packaged builds; a versioned schema artifact remains available in the repository/build.

## API v1

Every route below is rooted at `/api/v1` and authenticated. Writes use a transaction and support an `Idempotency-Key` header or typed key field. Resource reads return an `ETag`/revision where useful.

| Route | Purpose |
| --- | --- |
| `GET /health` | Low-cost service, contract, instance, database, worker, and time status. |
| `GET /providers/health` | Typed health/capabilities for registered local/cloud adapters. |
| `GET /capabilities/ffmpeg` | Managed FFmpeg availability, compatible version, and required features. |
| `GET /projects` | Paginated project summaries with deterministic sort and opaque cursor. |
| `POST /projects` | Create one durable project from a validated name. |
| `GET /projects/{projectId}` | Load the project detail projection, including current extraction and Import Review state. |
| `POST /projects/{projectId}/imports` | Stream and validate a multipart TXT/Markdown/DOCX/EPUB/PDF source, preserve immutable bytes, and create extraction work. |
| `GET /projects/{projectId}/imports/{reviewId}/review` | Read one bounded extraction preview, its warnings/revision, state, and latest decision. |
| `POST /projects/{projectId}/imports/{reviewId}/review/decision` | Append an idempotent local-human decision for the exact source/extraction revision. |
| `POST /projects/{projectId}/imports/{sourceDocumentId}/reextract` | Append extraction work for an existing immutable source. |
| `PUT /projects/{projectId}/dialogue-lines/{lineId}/speaker` | Save a human correction using `expectedRevision`. |
| `POST /projects/{projectId}/jobs` | Persist a typed job; `analyze_story` is required in Phase 0. |
| `GET /jobs/{jobId}` | Read durable job state and current attempt. |
| `GET /jobs/{jobId}/events` | Read/stream ordered events after an optional sequence. |
| `POST /jobs/{jobId}/cancel` | Idempotently request cooperative cancellation. |
| `POST /jobs/{jobId}/retry` | Start another attempt after a recoverable failure. |
| `POST /jobs/{jobId}/resume` | Continue compatible interrupted/paused work from checkpoint. |
| `POST /projects/{projectId}/analysis-runs` | Recheck exact approved extraction/profile preconditions and create an immutable Phase 2 run plus durable job idempotently. |
| `GET /projects/{projectId}/analysis-runs` | Return a stable bounded page of run summaries. |
| `GET /projects/{projectId}/analysis-runs/{runId}` | Return one run, snapshot/gate summary, profile, progress, warnings, and provenance without full manuscript text. |
| `GET /projects/{projectId}/analysis-runs/{runId}/entities/{collection}` | Return a bounded, filtered page of one allow-listed typed analysis collection. |
| `GET /projects/{projectId}/analysis-runs/{runId}/corrections` | Return a bounded correction-history page. |
| `POST /projects/{projectId}/analysis-runs/{runId}/corrections` | Append one typed human correction with revision/fingerprint preconditions. |
| `GET /projects/{projectId}/analysis-runs/{runId}/reviews` | Return the four current Phase 2 gate projections and decision history summaries. |
| `POST /projects/{projectId}/analysis-runs/{runId}/reviews/{gateId}/decisions` | Append an idempotent human approve/reject/changes-requested decision for exact current evidence. |

Deletion/cache cleanup and approval/render routes are added behind the same boundary when their product flows are implemented; they must not be improvised as filesystem access APIs.

## Contract shapes

All API values are explicit domain DTOs rather than ORM records. Shared primitives include:

```text
EntityRef       { id, revision }
SourceSpan      { storyRevisionId, startUtf8Byte, endUtf8Byte, line?, column? }
Provenance      { producerType, producerId, producerVersion, inputRevisions[],
                  provider?, model?, configurationHash, createdAt }
Warning         { code, severity, message, reviewRequired, sourceSpan? }
Confidence      { value: 0..1, method, calibrated: boolean }
Page<T>         { items[], nextCursor? }
```

`ProjectDetail` composes project/source/story metadata, ordered chapters/scenes/story content items, characters, current dialogue attributions, casting assignments, approval summaries, and relevant job summaries. Large complete source or audio bytes use dedicated streaming endpoints when introduced, not this projection.

Mutation models reject unknown fields. Names and reasons are Unicode-normalized only for validation/display metadata; source text is never normalized. Empty identifiers, non-finite numbers, invalid enum values, reversed/out-of-range spans, stale revisions, and cross-project references are rejected.

Phase 2 collection pages default to 50 and reject a requested limit above 200.
Evidence excerpts are derived only for authenticated project-scoped reads and
are capped at 512 Unicode code points. A claim carries at most 16 evidence
spans; a dialogue line carries at most eight speaker candidates. List
endpoints never return the complete manuscript. See
[analysis-performance-and-pagination.md](analysis-performance-and-pagination.md).

## Error model

Expected failures use stable codes and safe user-facing messages:

```json
{
  "error": {
    "code": "CHECKPOINT_INCOMPATIBLE",
    "message": "This work cannot resume after the input changed; retry it.",
    "retryable": false,
    "correlationId": "opaque-id",
    "details": {"jobId": "opaque-id"}
  }
}
```

Use `400` invalid input, `401` failed launch authentication, `404` absent/inaccessible resource, `409` revision/state/idempotency conflict, `413` size limit, `415` media type, `422` semantic validation, `429` bounded resource concurrency, and `500/503` internal/unavailable failures. Production responses never include stack traces, SQL, prompt/provider bodies, source excerpts, credentials, tokens, or absolute paths.

## Import boundary

Uploads are streamed to a newly created project-scoped staging directory with restrictive permissions. The service has a 100 MiB source ceiling. Detection considers extension, declared type, magic/signature/package structure, and decoder result rather than trusting any one value. The desktop's current byte-transfer bridge enforces a separate 8 MiB client cap and reports it distinctly.

TXT/Markdown import:

- retains original bytes and SHA-256;
- detects BOM/encoding from a small allow-list;
- decodes strictly and records encoding/newline metadata;
- stores exact decoded characters with no newline or Unicode normalization;
- records Phase 1 extraction sections/mappings as half-open Unicode-code-point
  offsets; downstream Phase 0 story/analysis entities retain their existing
  half-open UTF-8 byte-span contract, and typed boundaries do not conflate the
  two units.

Phase 1 DOCX/EPUB/PDF import runs behind format-specific extractors with the
fixed `secure-ingest-v1` profile: 2,048 archive members, 512 Unicode code points
per member name, 32 MiB per member, 200 MiB total expansion, 100:1 compression
ratio, path depth 20, 10,000,000 characters, 10,000 sections, 2,000 PDF pages,
and a 30-second deadline. Archive
paths are validated as relative package names; links, devices, absolute/drive
paths, backslashes, `..`, active content, external fetches, and encrypted PDF
are rejected or explicitly omitted. See
[secure-document-ingest.md](secure-document-ingest.md).

## Internal ports

Use cases depend on interfaces such as:

```text
ProjectRepository
SourceStore
JobRepository / JobScheduler
StoryImporter / StoryAnalyzer
AgentOrchestrator
ProviderRegistry / CredentialStore
RenderEngine / ArtifactStore
Clock / IdGenerator / EventSink
```

Infrastructure implementations are injected at the composition root. A route handler does not query SQLite, call a provider SDK, or launch FFmpeg directly. Domain services receive a cancellation/deadline context and return typed outcomes.

## Concurrency and resource limits

SQLite writes are short and transactional. CPU/provider work runs outside database transactions, then commits only if the snapshotted input revision remains valid. A project-scoped write lease prevents two jobs from publishing conflicting projections. API concurrency, upload bytes, worker count, provider calls, subprocess duration/output, and event subscriber buffers are bounded.

Health checks have short timeouts and cannot send story content. Provider checks are cached briefly and report stale age. Slow/disconnected UI clients cannot block workers; durable events are authoritative and streams may direct a client to poll after backpressure.

## Observability

Structured events include UTC time, severity, stable event/error code, component, correlation ID, and opaque project/job/attempt IDs. Field names are allow-listed; free-form exception/provider output is sanitized and bounded. The service does not log request/response bodies, source names containing sensitive text, source passages, prompts, audio, bearer tokens, credentials, or personal paths.
