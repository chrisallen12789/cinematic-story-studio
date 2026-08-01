# Local speech provider architecture

Phase 3B adds short, governed speech auditions. It does not add full-book synthesis, mixing,
mastering, export, voice cloning, or a cloud speech service.

Two adapters implement the same bounded contract:

- `deterministic-pcm-wav-fixture` creates deterministic repository-owned PCM for tests. It is
  never a production voice and provides no evidence of naturalness or intelligibility.
- `kokoro-local-onnx` runs a separately installed, exactly verified Kokoro ONNX package through a
  managed local worker for a private component-only verification command. It is not currently a
  usable governed product provider: provider-internal voice `af_heart` has no matching Phase 3A
  voice profile, snapshot-selected assignment, or rights-record binding. Installation,
  verification, or activation does not bridge that authority gap.

Every product audition request freezes provider/adapter/runtime/model/package identity, exact
Phase 3A cast authority and temporally applicable leaf rights, assignment revision,
normalized-text hash, effective pronunciation plan, provider controls, output profile, and
producer version. There is no silent provider, model, voice, or execution-provider substitution.
The private real-provider command deliberately does not create or consume that Phase 3A binding,
so its output cannot enter the governed audition workflow.

The provider interface accepts a typed request and returns either a typed failure or a staged PCM
artifact plus provenance. It never accepts a URL, shell command, arbitrary executable, arbitrary
output path, raw SSML, or unbounded text. Only the owning service can publish a successful clip.

The runtime profile is fingerprinted and centralizes these current bounds:

- one concurrent synthesis request;
- four thousand input code points and at most 509 model content tokens;
- requests above either bound fail with `SPEECH_TEXT_TOKEN_LIMIT`; this phase
  does not chunk or truncate synthesis text;
- 24 kHz, mono, 16-bit PCM auditions;
- 30 seconds and 24 MiB maximum per audition artifact;
- a 30-second startup deadline plus bounded request, cancellation, stderr,
  protocol-frame, and idle deadlines;
- zero internal runtime retries: each durable job attempt dispatches the provider at most once.

Runtime profiles are append-only evidence records. The current `1.0.1` fixture and Kokoro
profiles use distinct public and record IDs with the 30-second startup deadline. An upgraded
schema-v5 database retains and validates any issued `1.0.0` profile with its original 10-second
deadline and fingerprint; those legacy rows remain available to resolve historical runtime
evidence, but new work and current health select only the exact `1.0.1` record and fingerprint.

A retryable failure is retried only as a new durable job attempt. That attempt appends a distinct
provider-request row, reacquires an authenticated runtime, and records its own single dispatch.
Verified cache hits are lookup-only: they bind the source provider request, retain a null runtime
instance on the lookup row, and record a provider dispatch count of zero.

The dispatch count is a one-way write-ahead invariant. Every authenticated synthesize-frame write
is preceded by a durable count of one plus the exact runtime identity and start timestamp. A count
of zero therefore proves that no provider dispatch occurred. A count of one records a committed
dispatch attempt; it does not by itself prove provider execution or completion. The returned
artifact, integrity checks, and QC evidence establish successful completion.

Kokoro inference explicitly requests `CPUExecutionProvider`. When the backend is first initialized,
it checks the runtime's selected provider, the expected `input_ids`, `style`, and `speed` input
names, and the selected voice tensor's exact little-endian float32 shape. It validates the waveform
returned by each inference before emitting WAV bytes. Unknown G2P terms fail with a typed
pronunciation-review error rather than being sent to a fallback engine.

Package activation and the inexpensive provider-health query verify the trusted package bytes and
that the pinned Python modules are discoverable. They do not construct an ONNX session or run an
inference, establish voice rights, or make the component available to a governed cast. The
provider remains classified `restricted`; the first component synthesis is the first backend-
initialization and functional-runtime check. Governed use remains fail-closed until an explicit
Phase 3A voice/profile/assignment/rights binding exists and legal, consent, likeness, provenance,
and rights review is complete.

Docker remains an optional development-only health probe. It is not the product runtime and may
not satisfy the managed-process, loopback, package, offline, or shutdown evidence.

## Bounded private real-provider verification

This command verifies the adapter/worker component only. It is not a desktop or service product-
path audition, and success does not create the missing governed Phase 3A binding.

After placing the exact allow-listed package at the ignored
`local-models/kokoro-phase3b` directory, run this command from the repository root:

```powershell
pnpm service:python apps/local-service/scripts/verify_real_speech_provider.py --acknowledge-restricted-local-use
```

The acknowledgement flag is mandatory. The command accepts no text, model URL, model path, or
output path. It verifies the complete `KOKORO_LOCAL_ONNX_MANIFEST`, starts the authenticated
managed worker with fixed deadlines and network access disabled, synthesizes one fixed
repository-owned phrase with one fixed provider-neutral pronunciation entry, validates the PCM
WAV and signal bounds, and requires exact owned-process exit evidence.

On success, it atomically renames one complete run directory beneath the ignored
`local-renders/phase3b-real-provider` root. That directory contains the WAV and redacted JSON
evidence. The JSON records only relative paths and hashes for the fixed text and pronunciation
plan; it does not contain the raw phrase, pronunciation value, hostname, username, or an absolute
path. The command does not download a model, contact a cloud service, establish human listening,
or provide a governed voice/profile/assignment/rights binding, consent, quality, commercial-
clearance, legal-certainty, product availability, or production-readiness evidence.
