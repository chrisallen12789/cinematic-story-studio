# Local speech and model-package threat model

## Protected assets

- manuscript-derived preview text and pronunciation decisions;
- model packages and private installation state;
- generated audition audio, cache, hashes, and human decisions;
- service bearer token and child-process bootstrap secrets;
- integrity of Phase 3A cast/rights evidence and Phase 3B readiness evidence.

## Trust boundaries

The renderer is untrusted and isolated. The Electron main process brokers typed IPC and bearer-
authenticated loopback requests. The local service owns storage, durable jobs, package validation,
provider workers, and audio publication. A selected model archive and every model/runtime output
are untrusted until verified. Fixture output is test evidence only.

## Principal threats and controls

| Threat | Control |
| --- | --- |
| Malicious archive traversal, link, reparse, collision, bomb, or unexpected executable | Authenticated bounded ZIP upload to service-generated `${data_dir}/model-staging`; trusted external manifest; exact inventory/size/hash; ZIP special-entry and source-tree link/reparse rejection; atomic install below `${data_dir}/models`; no renderer path |
| Pickle or model-triggered code execution | Exact allow-listed ONNX bytes; fixed CPU runtime; no pickle loader, dynamic package install, or registered custom-operator library. The package verifier does not perform generic static ONNX graph inspection, so ONNX Runtime and the pinned model remain trusted native inputs. |
| Synthesis-time download or exfiltration | No remote loader, cloud endpoint, account, or credential. After bootstrap, the worker replaces Python `socket` construction and denies/counts `connect`, `connect_ex`, `send`, `sendall`, `sendto`, `sendmsg`, `sendfile`, `bind`, `listen`, `accept`, and `create_connection`. An authenticated count is returned on ready, artifact, error, and stop frames. |
| Provider/model substitution | Exact provider/runtime/model/package/voice fingerprints in request, handshake, cache, artifact, and review evidence |
| Shell/argv disclosure or injection | Fixed argv array, `shell=False`; text/token/path-free argv; bounded authenticated stdin/stdout frames |
| Worker impersonation or PID reuse | One-use HMAC handshake plus PID/launcher/parent topology, validated resolved executable path and service-computed executable SHA-256, bounded worker timestamp, creation nonce, protocol/runtime/package identity, and Windows Job Object membership; the hello does not attest the hash and the service does not independently query OS creation time |
| Killing unrelated user processes | Act only on the complete owned process identity; ambiguity fails safely; never enumerate or terminate by name alone |
| Arbitrary audio file read | Authenticated artifact-ID endpoint; project ownership lookup; managed-root containment; link/reparse and size/hash revalidation; no renderer paths or `file://` |
| Manuscript leakage in lists/logs/jobs | Hashes, opaque IDs, bounded spans, and typed codes only; redacted progress events; no text/path/cache-key logging |
| Stale or fabricated approval | Revision/fingerprint write preconditions; append-only decisions; automation may invalidate but never approve |
| Corrupt/partial cache publication | Unique staging, bounded WAV inspection, fsync/atomic rename, DB publication last, validation on every hit |
| Voice cloning, impersonation, or unclear rights | No training/enrollment/reference-audio API; product path rejects Kokoro because `af_heart` has no governed Phase 3A voice/profile/assignment/rights binding; installation/activation cannot grant authority; product use and production export remain blocked pending explicit binding and human legal/rights review |

For playback, audio bytes do cross the single named audition IPC channel after
Electron main performs the authenticated artifact fetch and independently
validates the 24 MiB cap, declared and observed length, SHA-256, MIME/no-store
headers, and PCM WAV structure. Preload returns only that validated
`ArrayBuffer`; the renderer creates a revocable Blob URL. This is not a path or
storage-key capability. The exact route and IPC bounds are recorded in
[Phase 3B API pagination and payload limits](../architecture/phase-3b-api-pagination-and-payload-limits.md#audition-audio-and-renderer-ipc).

The known binding, licensing, and provenance limitations in ADR-0011 are governance blockers.
The private Kokoro command is component-only evidence and does not make the adapter available in
the governed product. Automated signal checks demonstrate file integrity and non-silence only;
they do not establish intelligibility, artistic quality, consent, likeness clearance, or
production fitness.

## Boundary limitations

The socket policy is Python-level defense in depth, not an OS-enforced outbound firewall. Native or
C-extension code can call Winsock without traversing the patched Python methods. A reference to the
original socket class retained before the patch, or a usable inherited/pre-existing handle, can
also bypass it. `close_fds=True`, the minimal child environment, fixed imports, no application
listener, and exact process ownership reduce that exposure but do not eliminate it. A zero denied
count means that none of the patched Python operations was observed; it is not proof that the
process emitted zero packets. Independent OS-level network observation is required for stronger
release evidence.

The filesystem checks reject ZIP special entries and source-tree reparse points, but they do not
prove that an ordinary source file has no other hard links. Exact names and hashes prevent an
unlisted payload from being installed, but the package manager is not a generic Windows device-name
validator. The service-private upload flow and managed-root containment are part of the boundary.
