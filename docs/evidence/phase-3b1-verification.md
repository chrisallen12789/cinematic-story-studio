# Phase 3B.1 verification record

## Status and evidence boundary

Required stopping status after final frozen-head local and hosted closure:

> Implementation and automated verification complete; human listening decision pending.

Phase 3B.1 activates one narrowly governed Kokoro/ONNX path for restricted,
private local voice auditions. It does not establish naturalness, artistic
quality, performer consent, commercial-use clearance, production readiness, or
export eligibility. No private human-listening decision is committed or
uploaded, and Phase 3B.1 produced and records no full-book rendering. Phase 3C
and Phase 4 were not started.

Three evidence classes remain separate:

1. Hosted GitHub Actions uses the deterministic fixture provider and synthetic
   Phase 3B.1 metadata. It proves the real provider fails closed when the model
   package is absent. Hosted CI neither downloads nor uploads the real model,
   voice tensor, or generated real-provider audio.
2. Local automated verification uses the exact ignored Kokoro package and the
   exact locally built desktop executable. It proves real ONNX inference through
   the desktop product path, authenticated playback, restart restoration,
   cache behavior, targeted invalidation, process ownership, and clean exit.
3. Human listening and its append-only clip decisions are private local
   evidence. Automated signal checks and playback automation are not human
   listening and cannot approve a voice. Mixed historical outcomes do not
   authorize a current review or aggregate readiness.

Generated real-provider models, voice tensors, audio, databases, caches,
private manifests, isolated desktop state, and listening packages are ignored
private artifacts and are not committed or uploaded. The short-lived CI
artifact contains the built application and sanitized CI evidence files,
including `build-evidence.json` and fixture E2E results; it contains no real
model, tensor, audio, or private listening evidence.

## Revision and fixed controls

| Field | Value |
| --- | --- |
| Branch | `codex/phase-3b1-governed-real-voice-activation` |
| Verified base | `main` merge commit `567b647c970aefac335193baa3e8f2427419e4e1` |
| Application version | `0.1.0` |
| Database schema | Version 5; no Phase 3B.1 convenience migration |
| Governed catalog | `governed-local-voice-catalog-v2@2.0.0` |
| Catalog fingerprint | `994a2f77daed881cc4e24201d628ef32a732aa6ee0ff0815745a19772d2828cc` |
| Governed inventory fingerprint | `cb5657779b22d422cd7d8b9b81e09491aae1a82795e9e6af8a781c5f4c47c9bc` |
| Real provider / adapter | `kokoro-local-onnx@1.0.0` / `1.0.0` |
| Runtime | `onnxruntime-cpu@1.28.0` |
| Exact model package | `kokoro-82m-v1.0-onnx-q8-af-heart` |
| Package manifest fingerprint | `03702762c09a71ee54b7ea3bfa4939d1c622b01d68709e2180a39ca62ec264b0` |
| Verified expanded package | 5 files; 92,887,010 bytes |
| Governed profile | `kokoro-local-voice-001@1.0.0`; neutral label `Local Voice 001` |
| Voice tensor | Float32 little-endian `[510, 256]`; 522,240 bytes; SHA-256 `d583ccff3cdca2f7fae535cb998ac07e9fcb90f09737b9a41fa2734ec44a8f0b` |
| Output boundary | 24 kHz, mono, PCM16 RIFF/WAVE; private audition only |

The exact governed package inventory contains one technically eligible real
profile, and that profile remains restricted and production-ineligible. Six
listening clips therefore do not imply six distinct voices. The exact source-
by-source license, provenance, and rights conclusions are recorded in
the [Phase 3B.1 voice-rights and provenance review](../security/phase-3b1-voice-rights-provenance.md).
Those conclusions bind exact immutable evidence and artifact hashes. Missing
evidence remains restricted or unknown; it is never promoted to verified
rights, consent, or commercial clearance.

## Governed activation and casting evidence

Executable service and desktop tests cover the complete authority chain:

- exact catalog revision and immutable voice inventory;
- model inventory, file size/hash, tensor hash, tensor shape, and package
  verification;
- neutral governed voice-profile creation without invented identity claims;
- current rights-record and provenance binding;
- explicit restricted-audition warning acknowledgement bound to its complete
  authority tuple;
- stale acknowledgement rejection after catalog, rights, package, assignment,
  cast-snapshot, runtime, verification, or pronunciation drift;
- Phase 3A assignment correction, conflict resolution, and required reapproval;
- current approved-cast snapshot binding;
- per-role provider resolution for narrator-purpose and character-purpose
  auditions; and
- fail-closed behavior without every exact current prerequisite.

The acknowledgement authorizes only the displayed restricted private audition.
It is not a legal opinion, consent record, quality decision, commercial-use
approval, production clearance, or export authorization.

## Real local product-path evidence

The committed local gate is:

```powershell
pnpm --filter @cinematic-story-studio/desktop run test:e2e:packaged:phase3b1-real
```

The gate accepts no model URL, performs no download, and uses only repository-
owned synthetic text. It derives the exact version-scoped unpacked executable,
requires a clean source tree, opens the model ZIP through the native desktop
dialog boundary, and exercises the application rather than a disconnected
provider utility.

A durable implementation-checkpoint run, bound in its ignored evidence to its
exact source-head SHA, generated real Kokoro audio through the desktop product
path. This section records checkpoint behavior rather than final-head closure;
the draft pull request report binds final results to their exact head and
evidence fingerprints. The checkpoint created one invalidation probe, six
listening-purpose real audition clips, and one verified cache repeat. The six
listening clips were valid 24 kHz mono PCM16 WAV files with durations between
2.850 and 4.150 seconds. The cache repeat reused the exact verified first
artifact with no provider dispatch. All six clips were restored after restart
and loaded through authenticated desktop playback.

That run also proved:

- exact package, catalog, voice profile, rights record, acknowledgement,
  assignment, approved snapshot, runtime, tensor, dictionary, and activation
  bindings;
- seven real provider dispatches plus one verified cache hit;
- targeted pronunciation and activation invalidation without silent provider,
  model, voice, or historical-evidence substitution;
- one owned managed Kokoro worker with authenticated handshake, graceful
  shutdown acknowledgement, exit code 0, Python socket denial, and zero
  observed non-loopback endpoints in the owned-PID inventory;
- exact Electron, service, and provider-worker ownership by executable identity
  and process ancestry;
- every exact owned PID gone after shutdown;
- no force-kill, no remaining owned PID, and no unrelated process inspection or
  termination; and
- a private listening package containing six opaque WAVs, a redacted index,
  scorecard, and replay launcher, plus separately retained isolated desktop
  state beneath the bounded application-owned local-data root.

## Replay closure boundary

The first generated launcher moved its retained state from a short temporary
root into a deep repository sibling. Exact managed script, WAV, model-directory
and model-file paths then reached 289, 286, 279 and 305 characters. The files
still existed through Windows extended paths, but ordinary Python `pathlib`
access failed during private script-storage reconciliation. The embedded
service exited with the fixed `CSS_ERROR startup_failed` diagnostic. Electron
then aborted the same startup generation and incorrectly replaced that concrete
early-exit result with `SERVICE_START_CANCELLED`.

The corrected design preserves the failed package/state and uses a separate
copy beneath `%LOCALAPPDATA%\CSS-P3B1\<12-lowercase-hex-id>`. Generation rejects
any path above 240 characters before moving state. The private replay contract
binds the listening index, opaque state sentinel, desktop executable,
`resources/app.asar` product code, and embedded service by exact SHA-256 and
size; the launcher verifies every binding
before start. Dedicated Windows replay begins with no relevant process, proves
the exact project, clips, authenticated playback and private decisions after a
restart, exercises live-duplicate and stale-lock-marker behavior, and closes
only the exact owned Electron/service tree.

The committed local replay gate is:

```powershell
$env:CSS_PHASE3B1_PRIVATE_REPLAY_PACKAGE = "<absolute-ignored-package-directory>"
pnpm --filter @cinematic-story-studio/desktop run test:e2e:packaged:phase3b1-replay
```

An authorized one-time recording run also sets
`CSS_PHASE3B1_RECORD_PRIVATE_DECISIONS=1`; the frozen restart-verification run
omits it. Both the package and its private decision input remain ignored and
local.

Process-scoped endpoint inventory and the worker's Python socket denial are
bounded defense-in-depth evidence; they are not a complete firewall or packet
capture.

The private package path is ignored. The audited Git index and `HEAD` do not
contain its audio, database, cache, state, generated manifests, or personal
filesystem location, and the CI artifact policy rejects those materials.

## Automated test closure

The final frozen-head sequence is run only after all source and documentation
changes are committed. Focused suites repeat tests already present in aggregate
results and therefore are not additive totals.

| Gate | Required and recorded boundary |
| --- | --- |
| Frozen installs and Python lock reproduction | Pinned Node/pnpm versions; CPython 3.12.10; `--require-hashes`; editable `--no-build-isolation --no-deps`; exact lock diff; `pip check` |
| Lint and type checks | Repository checks, ESLint, strict TypeScript, and strict mypy |
| Schema/tooling | Deterministic public schemas, fixtures, voice catalog, build evidence, and repository policy |
| Full backend and full desktop | Entire repository-owned backend and desktop suites; exact counts reported separately from focused reruns |
| Focused regressions | Phase 2, Phase 3A, Phase 3B, and Phase 3B.1 suites |
| Development Electron E2E | Complete synthetic persistence path in development Electron |
| Build and frozen service | Exact staged PyInstaller service and version-scoped unpacked Electron application; frozen-service runtime tests against those bytes |
| Packaged Electron E2E | Complete deterministic Phase 0-3B lifecycle against the exact built executable |
| Real local product path | Exact ignored package, actual ONNX inference, six private clips, authenticated playback, restart, cache, invalidation, process ownership, and private listening-package creation |
| Private replay closure | Exact launcher and package/state/app/service bindings; bounded state path; no-process, duplicate and stale-marker cases; project/clip/playback/decision restoration; exact owned-process exit |
| Repository/security closure | Tracked-content scan, clean-tree check, Git object check, and pinned Gitleaks scan |

An interrupted test is never counted as passed or failed. A result is reused
only when its source commit, inputs, package fingerprints, executable hashes,
and evidence fingerprints are identical. Final local counts, warnings, skips,
exact application/service hashes, and process tuples are reported in the draft
pull request closure report. GitHub Actions run/job IDs, artifact identity and
digest, and the generated sanitized build manifest are reported separately
after the pushed head finishes; they cannot be known self-referentially inside
the commit they verify.

The aggregate Windows skips remain explicitly classified: tests requiring
Windows symlink privilege may skip when the account lacks that privilege, and
tests requiring the exact frozen service are rerun against that service in the
separate frozen-service gate. Installed FFmpeg capability is reported as its
own local check rather than silently changing a hosted or aggregate count. The
known upstream Starlette TestClient/current `httpx` deprecation warning remains
separate from failures.

## Dependency and security evidence

Dependency evidence has two scopes:

- local and Windows CI reproduce the committed Node lock and hash-locked Python
  environment with pinned runtimes and fail on a changed Python lock or broken
  installed requirement; and
- the pull-request-only GitHub Dependency review workflow examines dependency
  changes and fails at `moderate` severity.

No unpinned vulnerability scanner or mutable downloader is introduced. This
record does not relabel a local `pnpm audit` or Python advisory scan as hosted
CI evidence. Repository secret scanning uses checksum-pinned Gitleaks and
redacts values; the repository content scanner rejects models, audio,
databases, caches, credentials, personal paths, and other prohibited material.

## Mandatory human-listening checkpoint

The public automated product-path evidence deliberately leaves its listening
decision count at zero. Subsequent human outcomes and their exact actor/time,
clip/artifact hashes, and rationale remain in ignored private state rather than
this repository. Mixed acceptable and needs-changes evidence keeps Voice
Readiness blocked; it does not establish generalized naturalness or quality,
consent, commercial clearance, production readiness, or export eligibility.
Corrective audition evidence and its human decision remain pending.

The stopping state is therefore:

> Implementation and automated verification complete; human listening decision pending.

## Documentation map

| Topic | Record |
| --- | --- |
| Product scope, activation, casting, UI, local gate | [Phase 3B.1 governed real local voice activation](../product/phase-3b1-governed-real-voice-activation.md) |
| Architecture, evidence bindings, and CI boundary | [Governed real-voice activation](../architecture/governed-real-voice-activation.md) |
| Catalog and inventory | [Synthetic voice catalog](../../fixtures/synthetic-voice-catalog.json) and versioned contract schemas |
| Rights and provenance matrix | [Phase 3B.1 voice-rights and provenance review](../security/phase-3b1-voice-rights-provenance.md) |
| Rights/consent policy | [Voice rights and consent](../security/voice-rights-and-consent.md) |
| Process and worker lifecycle | [Speech worker lifecycle](../architecture/speech-worker-lifecycle.md) |
| Audition governance and invalidation | [Audition sessions, cache, and governance](../architecture/audition-sessions-cache-and-governance.md) |
| Known limitations | [Phase 3B.1 known limitations](../architecture/phase-3b1-known-limitations.md) |
| Verification | This record |
