# Phase 3B API pagination and payload limits

This document records the implemented Phase 3B local-service and desktop
boundaries. It is descriptive, not a substitute for the typed contracts. The
authoritative implementations are the service routes in
[`app.py`](../../apps/local-service/src/cinematic_story_service/app.py), the
strict request models in
[`schemas.py`](../../apps/local-service/src/cinematic_story_service/schemas.py),
the repository limits in
[`audition_repository.py`](../../apps/local-service/src/cinematic_story_service/audition_repository.py),
and the desktop validators and client in
[`audition-validation.ts`](../../apps/desktop/src/main/audition-validation.ts)
and [`api-client.ts`](../../apps/desktop/src/main/api-client.ts).

## Authentication and transport boundary

Every `/api/v1` route requires the per-launch bearer token. The service accepts
only a loopback host (`127.0.0.1` or `localhost`; `testserver` exists for the
in-process test client), compares the supplied token in constant time, enables
no renderer CORS path, and returns `Cache-Control: no-store`, `Pragma: no-cache`,
`X-Content-Type-Options: nosniff`, and an `X-Correlation-ID` header. The launch
token is bounded to 512 characters by service configuration and remains in
Electron main-process memory; preload and renderer never receive it.

The renderer has no raw HTTP or generic IPC method. Each desktop operation uses
a named, versioned IPC channel. Main revalidates the request, active-project
ownership, and service response before returning a typed result. The desktop
client also rejects a constructed service route longer than 2,048 characters,
containing a backslash, NUL, or `..`. Desktop opaque entity identifiers use the
safe pattern `[A-Za-z0-9][A-Za-z0-9._:@+-]{0,127}`. Service path parameters are repository
lookup keys rather than filesystem paths; the FastAPI path declarations do not
add a separate length annotation, so authenticated non-desktop clients must
still obey the 128-character contract bound.

## Common bounds

| Boundary | Implemented bound |
| --- | --- |
| Phase 3B JSON mutation body | At most 65,536 UTF-8 bytes. Electron main checks its encoded request, and service middleware independently checks both declared and streamed bytes for the Phase 3B JSON mutation routes below. |
| JSON response accepted by Electron main | At most 16,777,216 bytes for these routes, followed by exact response-shape, collection, ownership, revision, and fingerprint validation. |
| Unknown JSON fields | Rejected by every Phase 3B Pydantic request model and by the desktop IPC validators. |
| Opaque entity identifiers | 1–128 characters unless a narrower field is listed below. Desktop IPC additionally applies its safe-identifier pattern. Descriptive provider/model/version fields use their separately typed bounds. |
| Fingerprints and SHA-256 values | Exactly 64 lowercase hexadecimal characters. |
| Revisions | Integers at least 1, except `expectedDictionaryRevision` on entry creation may be 0 for the initial empty dictionary. |
| Idempotency key | 1–160 characters. The service rejects blank/control/space-containing values; desktop IPC uses its narrower safe-identifier pattern. Reuse with different request material returns an idempotency conflict. |
| Pagination | Default 50, minimum 1, maximum 200 items; cursor maximum 512 characters. Collection routes use `cursor`/`limit`; the workspace role projection uses the separately named `roleCursor`/`roleLimit` pair with the same bounds. |
| Audition script text | 1–4,000 code points in the request. Its private persisted UTF-8 record is independently capped at 16,384 bytes. |
| Normalization edits | At most 2,000. A request may name at most 2,000 unique accepted optional-normalization IDs and 50 unique custom pronunciation-scope IDs; every ID is at most 128 characters. The 65,536-byte body cap remains authoritative. |
| Pronunciation dictionary | At most 1,000 current entries. Written form is 1–120 characters; pronunciation and IPA are 1–256; provider ID is at most 80; provider-compiled value and reason are at most 1,000. Priority is -1,000 through 1,000. Plain-text validators reject forbidden control characters and provider markup. |
| Audition records | At most 2,000 sessions per project, 20 scripts per session, and 10,000 cache records per project. |
| Review text | Rationale is 1–4,000 characters; a model-operation or cache-clear reason is 1–1,000. |
| Provider controls | Speaking rate 0.5–2.0; optional pitch -1.0–1.0; optional energy 0.0–1.0; optional style 1–80 characters. |

The JSON middleware applies to an `application/json` `POST`, `PUT`, or `PATCH`
whose path matches the Phase 3B mutation allow-list. The desktop client always
uses that content type. Model installation and repair are the separately bounded
multipart operations described below.

## Endpoint inventory

`{projectId}`, `{modelPackageId}`, `{entryId}`, `{auditionSessionId}`,
`{clipId}`, `{gateId}`, and `{reviewId}` are opaque identifiers. No endpoint
accepts an arbitrary local path, storage key, model URL, provider URL, or shell
fragment.

| Method and route | Query, body, and result boundary |
| --- | --- |
| `GET /api/v1/projects/{projectId}/auditions/workspace` | Optional `roleCursor` (at most 512 characters) and `roleLimit` (default 50, minimum 1, maximum 200); no body. Returns the bounded Phase 3B metadata snapshot: prerequisites, providers/runtime evidence, current model state, dictionary summary, one page of the governed role set, current reviews, and readiness. It does not return script or audio bytes. |
| `GET /api/v1/projects/{projectId}/speech/model-packages` | Common `cursor`/`limit`. The cursor is bound to the project and current package collection. Returns metadata only. |
| `POST /api/v1/projects/{projectId}/speech/model-packages/{modelPackageId}/install` | One bounded multipart ZIP plus the exact manifest fingerprint, optional installation revision, explicit restricted-local-use acknowledgement, 1–1,000-character reason, and 1–160-character idempotency key. Details are in the model-upload section. |
| `POST /api/v1/projects/{projectId}/speech/model-packages/{modelPackageId}/repair` | Same multipart and evidence bounds as install; a valid repair must supply the current installation revision. |
| `POST /api/v1/projects/{projectId}/speech/model-packages/{modelPackageId}/actions` | JSON body under 65,536 bytes. Body package ID must equal the path ID. Action is exactly `verify`, `activate`, `deactivate`, `repair`, or `remove`, with exact manifest/revision preconditions, reason, and idempotency key. JSON `repair` fails with source-required (or fixture-unavailable); repair bytes use the multipart endpoint. |
| `GET /api/v1/projects/{projectId}/pronunciations/entries` | Common `cursor`/`limit`. Optional `expectedDictionaryRevision` (at least 1) and 64-hex `expectedDictionaryFingerprint` must be supplied together. Desktop always supplies both. The cursor also binds the current dictionary fingerprint. |
| `POST /api/v1/projects/{projectId}/pronunciations/entries` | JSON body under 65,536 bytes and the pronunciation bounds above. Scope is exactly `project`, `narrator`, `character_role`, `chapter`, `scene`, or `custom`; project scope forbids `scopeId`, while every other scope requires one. Representation is `provider_neutral`, `ipa`, or `provider_specific`, with the corresponding conditional fields. |
| `POST /api/v1/projects/{projectId}/pronunciations/entries/{entryId}/decisions` | JSON body under 65,536 bytes. Exact entry and dictionary revision/fingerprint preconditions; decision is `approve`, `reject`, or `request_changes`; rationale is at most 4,000 characters. |
| `GET /api/v1/projects/{projectId}/audition-sessions` | Common `cursor`/`limit`; optional `roleId` is 1–128 characters. The cursor binds the project, role filter, and current session collection. |
| `POST /api/v1/projects/{projectId}/audition-sessions` | JSON body under 65,536 bytes. Role ID and every evidence ID are at most 128 characters; provider/model/voice/producer versions are at most 40; every fingerprint is exact 64-hex. The nested evidence `projectId` must match the route. The project cap is 2,000 sessions. |
| `POST /api/v1/projects/{projectId}/audition-sessions/{auditionSessionId}/scripts` | JSON body under 65,536 bytes. Session ID must match the path. Text is at most 4,000 code points and the private stored record at most 16,384 bytes. A source span is ordered, non-empty, and at most 4,000 code points. Manuscript kinds require exact document/revision/span binding; synthetic kinds forbid that claim. Maximum 20 scripts per session. |
| `POST /api/v1/projects/{projectId}/audition-sessions/{auditionSessionId}/normalization-preview` | JSON body under 65,536 bytes. Session ID must match the path; text, hash, revision, accepted-normalization, and pronunciation-scope bounds match script creation. The authenticated response may include bounded original/replacement fragments so the user can inspect transformations. |
| `POST /api/v1/projects/{projectId}/audition-sessions/{auditionSessionId}/generate` | JSON body under 65,536 bytes; returns `202`. The hash-only preview contract is version `1.0.0`, binds IDs/revisions/fingerprints and provider controls, fixes output to 24 kHz mono `pcm_s16le_wav`, and carries no script text. |
| `GET /api/v1/projects/{projectId}/audition-clips` | Common `cursor`/`limit`; optional `auditionSessionId` and `roleId` are each 1–128 characters. The cursor binds both filters and the current clip collection. Returns artifact/QC/provenance metadata, never audio bytes or a filesystem path. |
| `GET /api/v1/projects/{projectId}/audition-clips/{clipId}/audio` | Required query: 1–128-character `auditionSessionId` and `audioArtifactId`; `expectedClipRevision >= 1`; exact clip fingerprint and artifact SHA-256; `byteSize` 45–25,165,824. The service requires every value to match the project-owned current clip before returning `audio/wav`. |
| `GET /api/v1/projects/{projectId}/audition-review-decisions` | Common `cursor`/`limit`; required `gateId` is 1–48 characters at the query boundary and then restricted to the exact five gate IDs; optional `roleId` is 1–128 characters. `per_role_audition_review` requires a role, while narrator, character, pronunciation, and voice-readiness aggregate gates forbid one. Returns immutable human and system decisions newest first from both per-role/aggregate audition and separate Voice Readiness storage. The cursor binds the exact project/gate/nullable-role scope and current history identity. |
| `POST /api/v1/projects/{projectId}/audition-reviews/{gateId}/{reviewId}/decisions` | JSON body under 65,536 bytes. Exact review revision/evidence fingerprint, closed decision union, 1–4,000-character rationale, optional 1–128-character superseded decision ID, and idempotency key. Gate/review ownership and current audio integrity are revalidated server-side. |
| `POST /api/v1/projects/{projectId}/audition-cache/clear` | JSON body under 65,536 bytes. Exact project revision, 1–1,000-character reason, and idempotency key. It accepts no path or storage key and operates only on verified project-owned cache records. |

## Cursor semantics

Phase 3B cursors are opaque URL-safe base64 encodings of a versioned offset and
a collection-binding fingerprint. Consumers must return `nextCursor` unchanged.
The service rejects malformed cursors, negative or out-of-range offsets, and a
cursor reused across a different project, collection, or filter. It also rejects
a cursor after relevant collection identity changes:

- workspace-role binding includes the project, exact cast-snapshot identity,
  role-generation identity, dictionary identity, assignment and rights counts,
  and session, clip, and review generation identities;
- model-package binding includes the project, collection total, and latest ID;
- pronunciation binding additionally includes the current dictionary
  fingerprint;
- session binding includes the role filter;
- clip binding includes the session and role filters; and
- review-decision binding includes the gate and nullable role scope, latest
  history identity, and total across the applicable audition or Voice Readiness
  decision storage.

Pages use deterministic ordering with an ID tie-breaker. Responses contain
`pageSize`, `total`, and `items`; `nextCursor` is absent on the last page. A
cursor is not a durable snapshot or a client-generated continuation token. If
the service returns `INVALID_CURSOR`, the caller reloads from the first page.
Review history reports a well-formed cursor whose latest decision or total has
changed as `409 AUDITION_REVIEW_HISTORY_CURSOR_STALE`; that caller also restarts
from the first page.

The 300-role scale fixture requires exactly two workspace role pages at the
maximum `roleLimit=200`. It proves that a workspace cursor is rejected when
reused for another project and after the bound session/clip collection
generation changes. Exact current-authority validation separately fails closed
for audition actions if snapshot-selected assignment or leaf/temporal rights
evidence drifts: trusted Phase 3A session/generation/review authority is removed,
although the bounded metadata page may still render. Assignment/rights revision
generations are not themselves cursor fields.

## Model-package upload limits

The current allow-listed Kokoro package manifest contains exactly five files and
92,887,010 expanded bytes. The implemented service limits are:

- one `.zip` upload and at most five text form fields;
- archive-file bytes at most 93,935,586 (the exact manifest expanded size plus
  1 MiB);
- complete multipart body at most 94,001,122 bytes (archive cap plus 64 KiB
  overhead allowance), enforced from `Content-Length` when present and while
  streaming regardless of that header;
- private service-owned multipart spooling and UUID archive staging;
- at most 64 ZIP entries including directory entries, derived from twice the
  32-entry package ceiling;
- normalized member paths at most 240 characters and eight components;
- a maximum 200:1 uncompressed-to-compressed ratio for each non-empty file; and
- a generic 536,870,912-byte expanded-inventory ceiling, further narrowed by
  the trusted current manifest to its exact five names, individual byte sizes,
  SHA-256 values, and 92,887,010-byte total.

The desktop native picker accepts one regular non-symlink ZIP and applies a
90 MiB (94,371,840-byte) preflight cap. The service's 93,935,586-byte archive
cap remains authoritative. Duplicate or unknown form fields, an empty archive,
an extra/missing archive member, path traversal, case collision, link/reparse or
special entry, size/hash mismatch, excess compression ratio, or unacknowledged
restricted use fails closed. The renderer supplies neither the selected path nor
the archive bytes; Electron main owns selection and streaming.

## Audition audio and renderer IPC

Published audition audio must be 45–25,165,824 bytes, 100–30,000 milliseconds,
24,000 Hz, mono, 16-bit PCM WAV. Before service delivery, the clip/session/
artifact/revision/fingerprint/hash/size tuple is matched, storage containment is
rechecked without following a link or reparse point, and the file is rehashed and
reinspected. The response is `audio/wav` with `Content-Length` and
`Cache-Control: no-store`.

Electron main makes that authenticated request. It bounds the streamed response
to 25,165,824 bytes, requires `audio/wav` and `no-store`, checks the exact expected
length and SHA-256, parses the RIFF/WAVE chunks, and requires 24 kHz mono PCM16
with non-empty even-length sample data. Only then does the named preload API
return a structured-clone-safe `ArrayBuffer` to the renderer. The renderer creates
a user-initiated, renderer-only Blob URL, never a `file://` URL, and revokes the
prior/current Blob URL when it is replaced or the workspace unmounts. There is no
arbitrary path reader, storage-key reader, autoplay, raw service request, or
long-lived URL capability.

## Errors and private-content rules

Errors use one redacted envelope:
`{"error":{"code", "message", "retryable", "correlationId", "details"?}}`.
The principal status classes are `400` for invalid cursors, host, content length,
or malformed multipart; `401` for missing/invalid launch authentication; `404`
for project-owned resources not found; `409` for stale evidence, changed bytes,
capacity, state, or idempotency conflicts; `413` for JSON-body, private
script-record, or model-upload size breaches; `422` for a structurally or
semantically invalid contract, including an out-of-bound audio declaration or
provider result; `500` for redacted internal/integrity failures; and `503` when
managed runtime or manifest identity cannot be established. Unexpected exceptions log
only the correlation ID and component and return the generic `INTERNAL_ERROR`.

Full manuscript text is never returned by workspace, model, session, or clip
lists and is never placed in job events, logs, model metadata, clip metadata, or
cache keys. The authenticated script-creation and normalization-preview response
may return only the bounded text/fragments the user is actively reviewing.
Pronunciation list/mutation responses contain the project's bounded private
pronunciation values; review-decision history contains its bounded private
rationales. Both remain authenticated application responses. Audio crosses only
the validated named IPC path above. Logs and durable checkpoints use opaque IDs,
hashes/fingerprints, counts, and
typed reason codes; they exclude launch tokens, raw text, pronunciation values,
absolute paths, model/audio bytes, and private cache keys.

## Recovery and limitations

API conflicts and interrupted writes preserve prior effective evidence; they do
not authorize repair by overwriting history. See
[failure recovery](failure-recovery.md),
[database migration 0005 and verified v4 rollback backup](../migrations/0005-phase-3b-local-speech-auditions.md),
and [Phase 3B known limitations](phase-3b-known-limitations.md).
