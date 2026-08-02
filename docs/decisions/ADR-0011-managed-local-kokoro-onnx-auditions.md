# ADR-0011: Managed local Kokoro ONNX auditions

- Status: Accepted for component-only Phase 3B verification; governed product use remains blocked
- Date: 2026-07-31

## Context

Phase 3B needs evidence from one real local speech component in addition to its deterministic
fixture provider. The component must work on Windows without a cloud account, credentials,
synthesis-time network access, voice cloning, or a mutable model download. Model and runtime
identity must be exact and auditable. A governed product provider additionally requires an exact
Phase 3A voice/profile/assignment/rights binding; the current catalog has no binding for Kokoro's
provider-internal `af_heart` voice.

The official Kokoro direction is technically suitable but its common wrappers are not acceptable
as packaged dependencies here. `kokoro-js@1.2.1` depends on `phonemizer@1.2.1`, which embeds an
eSpeak-NG worker and data. `kokoro-onnx@0.5.0` depends on `phonemizer-fork` and
`espeakng-loader`. eSpeak-NG is GPL-3.0-or-later. GPL permits commercial use, but the resulting
redistribution and coupling obligations have not been approved for this repository, which itself
has no root license. The Python wrapper also excludes Python 3.14, which is the verified local
toolchain, and truncates over-limit phonemes.

The original Kokoro v1.0 model card identifies the weights as Apache-2.0, records model SHA-256
`496dba118d1a58f5f3db2efc88dbdc216e0483fc89fe6e47ee1f2c53f18ad1e4`, describes the
training inputs as permissive/non-copyrighted, and explicitly permits deployments including
commercial APIs. It does not provide a complete performer-consent, identity, likeness, or
dataset-chain packet for each named voice. License metadata is therefore not treated as legal
certainty or voice-rights clearance.

## Decision

Implement `kokoro-local-onnx` as a thin, repository-owned provider adapter:

- ONNX Runtime CPU only, pinned to `onnxruntime==1.28.0` (MIT).
- NumPy pinned to `numpy==2.5.1`; its declared Python requirement raises the service runtime floor
  to Python 3.12 or newer.
- English dictionary G2P pinned to `kokorog2p==0.6.7` (Apache-2.0 package metadata), with spaCy,
  eSpeak, Goruut, and all network fallbacks disabled.
- Direct ONNX inference; do not package `kokoro-js`, `kokoro-onnx`, PyTorch, pickle weights,
  eSpeak, a Docker dependency, or another listening service.
- A managed authenticated child worker, one bounded request at a time.
- An authenticated, bounded multipart ZIP upload after explicit restricted-use acknowledgement.
  The service stages it at a generated path below `${data_dir}/model-staging`, installs it through
  `ModelPackageManager` below `${data_dir}/models`, and deletes staging. The renderer never
  supplies or receives a filesystem path. No model is committed, bundled, downloaded
  automatically, or uploaded to ordinary CI artifacts.
- No intended synthesis-time network access. Unknown G2P words produce a typed
  pronunciation-review failure and require a pronunciation override; they never trigger a
  fallback download or engine substitution.
- The Kokoro voice is classified `restricted`/`unknown` for rights governance. It may demonstrate
  a component-only local preview from a repository-owned synthetic phrase, but cannot enter the
  governed audition product path until an explicit Phase 3A voice profile, snapshot-selected cast
  assignment, and rights-record binding exists and human legal/rights review is complete. It
  cannot satisfy production export or production-readiness claims.

The frozen service includes ONNX Runtime's `LICENSE` and `ThirdPartyNotices.txt`, NumPy's package
metadata and license tree, and the `kokorog2p` package metadata/license. Archive-content tests
inspect the built executable for those exact entries. Their inclusion is necessary distribution
evidence, but it does not replace the broader human licensing audit below.

The exact conversion is
[`onnx-community/Kokoro-82M-v1.0-ONNX`](https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX)
at immutable commit `1939ad2a8e416c0acfeecc08a694d14ef25f2231`. It is a third-party ONNX
conversion referenced by Hexgrad's official JavaScript implementation, not a Hexgrad-origin
artifact. Its canonical source classification is `maintainer_referenced_conversion`: the official
upstream model repository is [`hexgrad/Kokoro-82M`](https://huggingface.co/hexgrad/Kokoro-82M),
while Hexgrad's maintainer-owned `kokoro.js` README selects the separate conversion repository at
[`hexgrad/kokoro@dfb907a0.../kokoro.js/README.md`](https://github.com/hexgrad/kokoro/blob/dfb907a02bba8152ca444717ca5d78747ccb4bec/kokoro.js/README.md).
That reference does not make the conversion maintainer-authored or official.

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `onnx/model_quantized.onnx` | 92,361,116 | `fbae9257e1e05ffc727e951ef9b9c98418e6d79f1c9b6b13bd59f5c9028a1478` |
| `voices/af_heart.bin` | 522,240 | `d583ccff3cdca2f7fae535cb998ac07e9fcb90f09737b9a41fa2734ec44a8f0b` |
| `config.json` | 44 | `df34b4f930b23447cd4dc410fabfb42eb3f24e803e6c3f97d618fb359380a36f` |
| `tokenizer.json` | 3,497 | `77a02c8e164413299b4b4c403b14f8e0e1c1b727db4d46a09d6327b861060a34` |
| `tokenizer_config.json` | 113 | `be1cb066d6ef6b074b3f15e6a6dd21ac88ff3cdaedf325f0aaed686c70f75d20` |

The complete allow-listed package is 92,887,010 expanded bytes. Every file, the exact inventory,
and the expanded total are verified before activation. Activation does not parse the ONNX graph or
run inference. The selected execution provider, model input names, and voice tensor shape are
validated when the backend is first initialized; output shape/finite/bound checks occur per
inference.

The canonical manifest also binds the provider/runtime versions, `windows`/`x64` platform,
source classification and official upstream, commercial/attribution state, pinned Python runtime
dependencies, compatibility constraints, active revocation state, and immutable conversion and
maintainer-reference provenance. Its fingerprint is
`03702762c09a71ee54b7ea3bfa4939d1c622b01d68709e2180a39ca62ec264b0`.

## Security boundary

The local service launches only a fixed worker module with an argv array and `shell=False`.
Manuscript text, pronunciation values, user paths, tokens, and secrets never appear in argv. A
random one-use token and nonce are sent over inherited stdin. The service verifies an HMAC-bound
handshake containing worker/launcher/parent PIDs, resolved executable path, bounded worker
timestamp, creation nonce, Job Object assignment, protocol version, provider/runtime/model
versions, package fingerprint, and Python socket-denial count. The hello does not claim an
executable hash. After validating the resolved executable identity, the service hashes those
resolved bytes itself; it does not independently query an OS process-creation timestamp. Frames,
stderr, startup, request, cancellation, retry, and idle shutdown are bounded.

The worker creates no application listener. After bootstrap it replaces Python socket construction
and denies/counts the usual connect, send (including UDP), server, and accept paths; counts travel
in authenticated responses. This is defense in depth, not an OS-enforced network sandbox: native/C
network calls, original socket references retained before the patch, and usable inherited handles
are residual limits. A zero count is not proof of zero packets. Output is written into unique
private staging, inspected as bounded PCM WAV, hashed, and atomically published. Shutdown targets
the exact owned process Job Object; it never enumerates or terminates by process name.

## Consequences and unresolved review

This decision provides reproducible component-only local preview evidence without claiming a
governed product provider or production voice. Installation and activation alone do not make
Kokoro usable by a cast: an explicit Phase 3A profile/assignment/rights mapping for `af_heart`
remains required.
ONNX Runtime increases the packaged service size. Model bytes remain a separate local
installation. CPU output may be reproducible on a fixed runtime but is not described as
byte-identical across different CPUs until measured.

Before any production distribution or export eligibility:

1. Counsel must review the Kokoro model/data and the selected voice's consent/identity/likeness
   chain.
2. The packaged `kokorog2p` lexicon provenance and NOTICE obligations require a distribution
   audit.
3. The application itself needs an owner-approved licensing strategy.
4. Human listening must evaluate intelligibility and artistic suitability; signal metrics cannot
   make that judgment.

These limitations are deliberate blockers, not warnings that automation may waive.
