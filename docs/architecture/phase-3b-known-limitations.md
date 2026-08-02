# Phase 3B known limitations

- The real Kokoro adapter has a private component-only verification path; it is not available as
  a governed product audition provider. No Phase 3A voice profile, snapshot-selected cast
  assignment, or rights record binds provider-internal voice `af_heart`. Installation,
  verification, and activation cannot close that gap. An explicit governed binding plus human
  legal, consent, identity/likeness, provenance, and rights review is required before product use.
  The upstream model metadata declares Apache-2.0, but the selected voice's performer consent,
  identity and likeness clearance, complete dataset chain, and commercial-use clearance have not
  been independently established.
- The `kokorog2p` package and its bundled English lexicon have Apache-2.0 metadata; redistribution
  provenance and NOTICE obligations still require human review.
- The component-only Kokoro command supports only English (`en`/`en-US`). Unknown words fail into
  pronunciation review; eSpeak, spaCy, Goruut, remote, and implicit fallbacks are disabled. This
  is not a claim that English Kokoro synthesis is available in the governed product.
- The `unicode_composition` normalization edit recognizes NFC substitutions for one Unicode
  scalar at a time; it does not promise full grapheme-sequence NFC composition. A decomposed
  multi-scalar sequence remains byte-for-byte visible, and unsupported combining marks remain a
  provider-specific review warning rather than being silently rewritten.
- The model package is not bundled or downloaded automatically. The user must install exact
  allow-listed local bytes through the authenticated bounded ZIP-upload flow into private
  application storage. The renderer never supplies a local filesystem path.
- Package activation proves the exact inventory, sizes, and hashes only. It does not statically
  inspect the ONNX graph, initialize the runtime, validate the voice tensor, or run inference;
  those functional checks begin with the first component initialization/synthesis. Activation
  also does not establish the missing governed voice/assignment/rights binding.
- The worker denies and counts common Python socket construction, client, server, and send paths,
  including UDP `sendto`. This is not an OS firewall: native/C code, a pre-patch socket reference,
  or a usable inherited handle can bypass the Python patch. A zero count is not packet-level proof.
- The protocol has no separate pre-dispatch reservation frame. Governed profiles use a 120-second
  idle bound and the service rechecks exact liveness before dispatch; an exit during acquisition is
  persisted as exact owned-process evidence, records zero dispatch, and fails retryably without
  rebinding until an explicit retry. Any future multi-worker/concurrent dispatcher must add a
  bounded authenticated reservation rather than relying on the current single-worker ordering.
- Source-tree reparse points and ZIP special entries are rejected, but an otherwise regular source
  file is not proven to have a link count of one. Exact inventory/hashes and service-private
  storage, not a generic hard-link or Windows device-name detector, are the controlling checks.
- ONNX CPU output is not promised byte-identical across different processors or runtime builds.
  Each artifact is still exactly hashed and bound to its runtime provenance.
- The local service now requires Python 3.12 or newer because the pinned `numpy==2.5.1` runtime
  declares that compatibility floor. The packaged Windows application carries its own runtime;
  source-development environments older than Python 3.12 are unsupported.
- Fixture PCM is deterministic but is not speech-quality evidence and cannot be used for export.
- PCM peak/non-silence checks do not prove intelligibility or subjective quality. Human listening
  remains necessary.
- Auditions are limited to short, bounded mono PCM clips. Full-book synthesis and production audio
  are outside Phase 3B.
- The service contracts support all six governed script kinds, including exact source-bound
  manuscript excerpts. The Phase 3B desktop authoring surface intentionally exposes only the
  repository-owned synthetic script because the current typed project detail does not provide a
  safe general narrator/excerpt span selector. Non-synthetic script authoring is API-only until a
  later typed source-span selection surface is approved; the service still rejects invented or
  mismatched source bounds.
- The cache is private per project. It is not shared across projects even when generation inputs
  otherwise match.
- A relevant evidence change makes existing approval stale but does not delete immutable history.
- Docker may be used for isolated development experiments only; it is not the managed product
  provider.
- There is no production release, installer, signing, auto-update, or distribution claim in this
  phase.
