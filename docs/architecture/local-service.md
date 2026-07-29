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
| `GET /projects/{projectId}` | Load the project detail projection used by Phase 0 UI. |
| `POST /projects/{projectId}/imports` | Stream and validate a multipart source, then atomically publish it. |
| `PUT /projects/{projectId}/dialogue-lines/{lineId}/speaker` | Save a human correction using `expectedRevision`. |
| `POST /projects/{projectId}/jobs` | Persist a typed job; `analyze_story` is required in Phase 0. |
| `GET /jobs/{jobId}` | Read durable job state and current attempt. |
| `GET /jobs/{jobId}/events` | Read/stream ordered events after an optional sequence. |
| `POST /jobs/{jobId}/cancel` | Idempotently request cooperative cancellation. |
| `POST /jobs/{jobId}/retry` | Start another attempt after a recoverable failure. |
| `POST /jobs/{jobId}/resume` | Continue compatible interrupted/paused work from checkpoint. |

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

Multipart uploads are streamed to a newly created project-scoped staging directory with restrictive permissions. Phase 0 defaults to a documented 100 MiB hard limit for TXT/Markdown; tests use a lower injected limit. Detection considers extension, declared type, magic/signature where applicable, and decoder result rather than trusting any one value.

TXT/Markdown import:

- retains original bytes and SHA-256;
- detects BOM/encoding from a small allow-list;
- decodes strictly and records encoding/newline metadata;
- stores exact decoded characters with no newline or Unicode normalization;
- uses UTF-8 byte offsets in all cross-language source spans.

Future DOCX/EPUB/PDF import runs behind format-specific extractors with archive member-count, expanded-size, compression-ratio, recursion, timeout, and memory limits. Each archive path is canonicalized beneath a fresh staging root; links, devices, absolute paths, drive prefixes, `..`, active content, embedded executables, and unexpected encrypted members are rejected or quarantined according to the importer contract.

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
