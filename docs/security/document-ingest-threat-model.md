# Document Ingest Threat Model

## Assets, actors, and trust boundaries

Protected assets are unpublished source bytes/text, project databases and
backups, human review/correction history, credentials, service authentication,
the user's filesystem/processes, and application availability. An attacker may
control every byte and filename of an imported TXT, Markdown, DOCX, EPUB, or
PDF, including misleading extensions, malformed metadata, nested package
paths, XML, PDF object graphs, and extreme resource claims.

Trust boundaries are the native picker to Electron main, main to authenticated
loopback service, upload to private staging, parent service to spawned parser
over bounded typed IPC, extraction to SQLite/publication, and service metadata
to renderer/log/CI evidence. The local Windows account and installed
application dependencies are trusted in Phase 1; a fully compromised account
or kernel is out of scope.

## Threats and controls

| Threat | Phase 1 control | Failure behavior |
| --- | --- | --- |
| Renderer supplies an arbitrary local path | Native picker capability remains in Electron main; renderer sends only allow-listed IPC data | Reject before filesystem access |
| Extension or MIME spoofing | Compare declared format, extension, magic, and package structure | Typed media/type mismatch |
| ZIP Slip, drive/absolute/backslash/`..` path or oversized member name | Pure archive-relative path validation; 512-code-point member names; depth 20; no extraction outside owned staging | Reject package before member read |
| Symlink, device, encrypted, duplicate, or ambiguous archive entry | Validate central directory entry type/flags and normalized names | Reject package |
| ZIP bomb or oversized member | 2,048 members, 32 MiB/member, 200 MiB total, 100:1 ratio, 100 MiB source | Reject before/during bounded read; preserve the immutable source and failed attempt but publish no extraction, review, or analyzable story |
| XML entity expansion, DTD, XInclude, network entity | `lxml` entity resolution/network/DTD/huge-tree/recovery disabled; forbidden constructs rejected | Typed unsafe/malformed XML |
| EPUB script or remote tracking/fetch | Parse passive text only; do not execute script or fetch remote/external resources | Omit with warning or reject malformed package |
| DOCX macro/OLE/executable/media behavior | Only allow-listed package relationships and WordprocessingML text are interpreted | Ignore/flag unsupported content; never execute |
| PDF JavaScript, action, attachment, external reference | Extract page text only through `pypdf`; no action/attachment handling or network | Ignore/flag; never execute/fetch |
| Encrypted or image-only PDF | No password guessing; no OCR | Typed encrypted or OCR-required failure |
| PDF object/page or extracted-text exhaustion | 2,000 pages, 10,000,000 characters, 10,000 sections, deadline checks | Typed resource/deadline failure |
| Parser overrun or stalled native/library call | Parent-enforced 30-second wall-clock acceptance deadline includes spawn, ownership handshake, child parse/serialization, and actual-target exit; Windows Job Object owns the launcher/target and applies kill-on-close plus 768 MiB per-process memory | Parent terminates the exact owned process tree, confirms exit, records a typed deadline failure, and publishes no extraction |
| Forged, oversized, or malformed parser IPC | One-byte start control, 4 KiB ownership envelope, 64 MiB result envelope, strict discriminated JSON/Pydantic messages, deadline checks before and after decode, and parent-side semantic/hash/range validation | Reject as protocol/deadline failure, terminate owned tree, publish nothing; the byte bound limits synchronous parent decode work but is not an exact-millisecond preemption claim |
| Partial/corrupt publication | Parse outside transaction; verify source hash; one revision-checked publication transaction | No visible partial extraction/story |
| Approval bypass | Analysis requires effective local-human approval tied to source/extraction revisions and fingerprint | Pending/rejected/stale analysis request fails closed |
| Idempotency confusion | Project-scoped key is bound to request/source/config fingerprint | Replay returns original; changed request conflicts |
| Sensitive diagnostics/evidence | Allow-listed counts, hashes, IDs, versions, states and codes only | Content/path-bearing errors are redacted |
| Parser supply-chain drift | Exact pins and hash-locked Python install; PyInstaller collection; CI version assertion and dependency review | Build/test fails |

## Adversarial verification

Deterministic public generators cover traversal, excessive ZIP members,
high-compression data, malformed DOCX/EPUB, EPUB remote references and script,
encrypted/image-only/truncated/2,001-page PDF, and a source one byte above
100 MiB. Boundary tests also exercise member, expanded-size, path-depth,
extracted-character, section, deadline, and cancellation behavior. Tests prove
external EPUB references/scripts are omitted, parser records set
`networkAccessPermitted: false`, each adapter runs behind the spawned-process
boundary, terminal output is rejected until the actual target exits
voluntarily, and deadline/cancellation tests prove exact owned-target
termination. Failures must not expose source excerpts or absolute paths.

The committed binary fixtures are canonical base64 generated from the public
synthetic story. CI regenerates and compares bytes, validates schema contracts,
and scans tracked content. No private manuscript, database, credential, audio,
model, or generated application binary is fixture input.

## Residual risk and deferred controls

Python adapters execute in a spawned child under the parent service. On
Windows, a named Job Object provides exact process-tree ownership,
kill-on-close, and a 768 MiB per-process memory ceiling. This is not a
low-integrity/AppContainer process and does not contain a native-library
memory-corruption exploit. The parser code exposes no network client and does
not fetch document resources, but there is no OS-enforced outbound-network
sandbox. A fully restricted token or stronger sandbox remains future defense
in depth.

Phase 1 does not sanitize a document for browser display; it extracts text and
renders it as application data through React. It does not verify digital
signatures, recover damaged files, handle passwords, extract layout-perfect
formatting, or OCR images. Files above 8 MiB that remain within the 100 MiB
service ceiling are unavailable through the desktop's current transfer bridge.
