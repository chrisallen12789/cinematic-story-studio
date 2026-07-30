# Secure Document Ingest

## Scope and invariants

Phase 1 extends the Phase 0 import boundary to TXT, Markdown, DOCX, EPUB, and
text-based PDF. Parsing is local and deterministic for the same bytes, adapter
version, dependency versions, and limits profile. It does not rewrite source
text, perform OCR, execute embedded content, fetch remote resources, invoke a
shell, or start Phase 2 audio work.

The original file is authoritative and immutable. An extraction is a derived,
append-only revision. Analysis consumes only an extraction carrying a current
local-human Import Review approval. A parser error, cancellation, timeout,
stale revision, or missing approval fails closed.

## Boundary and data flow

```text
native picker
  -> Electron main validation and bounded byte transfer
  -> authenticated loopback import request
  -> private unique staging file
  -> source hash, size and format validation
  -> immutable SourceDocument publication
  -> persisted extraction job
  -> parent-supervised, exact-owned spawned parser process
  -> bounded typed IPC and validated extraction result
  -> immutable DocumentExtraction and ParserExecutionRecord
  -> pending ImportReview
  -> local-human approval
  -> analysis job for the approved extraction revision
```

The renderer receives typed metadata and a bounded review preview, never an
arbitrary filesystem path or the service bearer token. The service re-reads the
preserved staging/source file as a regular non-link file, verifies byte count
and SHA-256, and rejects an integrity mismatch before parsing.

## Fixed secure-ingest-v1 parser profile

| Resource | Limit |
| --- | ---: |
| Archive members | 2,048 |
| Archive member name | 512 Unicode code points |
| One archive member | 32 MiB expanded |
| Total archive expansion | 200 MiB |
| Compressed-to-expanded ratio | 100:1 |
| Archive path depth | 20 components |
| Canonical extracted text | 10,000,000 Unicode characters |
| Extracted sections | 10,000 |
| PDF pages | 2,000 |
| Parent-enforced parser wall-clock deadline | 30 seconds |
| Windows parser-process memory | 768 MiB per process |
| Parser result IPC message | 64 MiB |

The exact parser profile includes its ingest-contract version, is canonically
serialized, and is SHA-256 hashed into `limitsFingerprint`. The job payload,
parser execution, extraction evidence, and publication-time validation bind
that same profile fingerprint. A changed profile or adapter does not mutate or
automatically re-extract an existing project; an explicit re-extraction
appends a revision and requires a new review.

The service source ceiling (100 MiB) and Import Review preview bound (8,000
Unicode characters) are separately named boundary limits. They are not
silently included in, or represented by, the parser-profile fingerprint.

The service ceiling and desktop transport ceiling are separate controls. The
service accepts a source up to 100 MiB. The Phase 1 desktop native-import bridge
enforces an 8 MiB byte-transfer cap before the request reaches the service.
The desktop must label that client-side rejection accurately; it must not claim
the service parser limit is 8 MiB.

## Adapter behavior

### TXT and Markdown

The adapter accepts strict UTF-8 and BOM-marked UTF-16 variants already covered
by the Phase 0 contract. It rejects undecodable or binary-control content,
preserves decoded characters without newline or Unicode normalization, and
creates deterministic text/heading sections and offsets.

### DOCX

The adapter treats DOCX as an untrusted ZIP package. It validates every central
directory entry before reading content, rejects encrypted/symlink/device or
unsafe paths, applies all archive budgets, and parses XML through `lxml` with
DTD loading, entity resolution, recovery, network access, and huge-tree mode
disabled. It reads the declared office document relationship and supported
WordprocessingML text structures. Macros, embedded executables/OLE objects,
external relationships, media, and active content are never executed.

### EPUB

The adapter requires the stored `application/epub+zip` mimetype and a valid
container/package document. It follows the declared spine in order, applies the
same ZIP/XML controls as DOCX, extracts supported XHTML text, and reports
omitted or unsupported content. Script is not executed. Remote URLs and
external resources are not fetched.

### PDF

The adapter uses `pypdf` for bounded page-aware text extraction. It rejects an
encrypted document without attempting passwords, rejects a page count above
2,000, and checks cancellation/deadline between pages. A document with no
usable text returns a typed OCR-required failure; no OCR engine is bundled.
Attachments, actions, JavaScript, annotations, and external resources are not
executed or fetched.

## Output and provenance

Each successful extraction records:

- immutable source and source revision;
- declared and detected format, media type, original byte count and SHA-256;
- adapter ID/version and exact parser dependency versions;
- start/end time, measured duration, status, and retry classification;
- the centralized `secure-ingest-v1` limits profile and its fingerprint;
- exact canonical text and SHA-256;
- ordered, bounded sections and source locations;
- typed warnings, structural summary, and page count when applicable;
- evidence fingerprint and extraction revision lineage.

Failed or cancelled attempts do not publish an extraction result; their
separate durable parser-attempt row retains bounded, redacted failure
code/message metadata.

The review projection may truncate its preview but never changes or substitutes
the canonical extracted text. General logs and machine-readable CI evidence
contain hashes, counts, versions, states, and stable codes only—never source
excerpts, absolute paths, secrets, or database contents. The short-lived
packaged UI screenshot may show only the repository-owned synthetic preview,
never private content.

## Idempotency, concurrency, and cancellation

An import idempotency key is scoped to the project and request fingerprint.
Replaying the same key and bytes returns the existing durable result. Reusing a
key for different bytes or parameters returns a conflict. New bytes append a
source revision. Adapter identity, parser version, and the centralized limits
profile/fingerprint are bound into the extraction job payload, parser execution
record, extraction evidence, and publication-time validation. Changing one of
those inputs does not itself mutate or automatically re-extract an existing
project. An explicit re-extraction appends a pending extraction revision,
invalidates the old effective gate, and opens a new pending review only after
that extraction publishes successfully.

Extraction runs as persisted work outside a database transaction. The parent
starts one parser attempt with the `spawn` multiprocessing context and passes
only immutable extraction input plus one-byte start control and typed pipe
endpoints; it does not pass a database connection or repository. The
30-second monotonic wall-clock acceptance deadline includes process startup,
ownership handshake, child parsing/serialization, and voluntary exit of the
actual parser target. A terminal result is not accepted while that target
remains alive, and a bounded message that finishes decoding after the deadline
is rejected. The 64 MiB byte cap bounds parent-side IPC decode/validation work;
the design does not claim that synchronous parent JSON decoding is preempted at
an exact millisecond.
Cancellation or deadline terminates the exact owned process tree, waits for
confirmed exit, and prevents publication. Publication then rechecks
project/source revisions in one transaction. Cancellation or failure removes
only owned staging work and never deletes the prior active source, extraction,
approval, analysis, or correction.
After an unclean service exit, startup removes only the exact recognized
`projects/<project-id>/staging/<uuid>/source.upload` shape for durable projects.
An empty UUID staging directory is also removed. Symlinks, reparse escapes,
non-UUID entries, and directories containing any unknown file are left
untouched so recovery fails safely instead of recursively deleting content.

The child reports its actual target PID before receiving permission to parse.
On Windows, it self-assigns to a parent-created named Job Object; the parent
independently verifies membership, retains an exact process handle, and applies
kill-on-close plus a 768 MiB per-process memory ceiling to both a possible
PyInstaller launcher and the actual parser target. The parent accepts at most
64 MiB of strict JSON/Pydantic result IPC and revalidates hashes, limits,
timestamps, identifiers, finite numeric values, sections, source locations,
manifest, provenance, and parser execution before publication.

This boundary is defense in depth, not a complete OS sandbox. The child uses
the same Windows account and is not launched with a low-integrity token. The
Job Object memory/deadline boundary does not contain a native-library
memory-corruption exploit. Adapters expose no network client and record
`networkAccessPermitted: false`, but Phase 1 does not install an OS-enforced
outbound-network sandbox. Restricted parser settings, no shell invocation,
passive extraction, dependency pins, and the process boundary remain necessary
controls.

## Failure recovery

Stable error categories distinguish unsupported media, type mismatch, unsafe
archive path, archive/resource limit, malformed package/XML, encrypted PDF,
OCR required, integrity failure, cancellation, deadline, revision conflict, and
internal parser failure. Messages are bounded and redacted.

On restart, durable queued/running extraction work follows the background-job
reconciliation rules. Terminal source, extraction, parser, review, and
downstream analysis records are restored by revision, and recognized abandoned
upload staging is removed before requests are accepted. A retry creates another
attempt. Re-extraction appends a revision; successful publication then opens a
new pending review.
