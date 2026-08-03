# Phase 3B.1 governed real local voice activation

## Status and boundary

Phase 3B.1 connects the already allow-listed Kokoro/ONNX runtime to the existing
Auditions & Pronunciation workspace for private, local comparison. It does not
authorize production export, full-book rendering, commercial distribution,
marketplace resale, cloning, or imitation of a real person. Phase 3C and Phase
4 remain out of scope.

The required status at the automated boundary is:

> Implementation and automated verification complete; human listening decision pending.

Signal and file-integrity checks do not establish intelligibility, naturalness,
artistic quality, consent, likeness clearance, commercial clearance, or
production readiness. Only a human can record the listening disposition for an
exact generated clip.

## Governed inventory

The immutable current catalog is
`governed-local-voice-catalog-v2@2.0.0`, fingerprint
`994a2f77daed881cc4e24201d628ef32a732aa6ee0ff0815745a19772d2828cc`.
It preserves all 14 historical synthetic fixture profiles and appends one
technically compatible real local profile:

- profile: `kokoro-local-voice-001`
- neutral display label: `Local Voice 001`
- provider voice ID: `af_heart`
- provider: `kokoro-local-onnx@1.0.0`
- model: `onnx-community/Kokoro-82M-v1.0-ONNX@1.0`
- model package: `kokoro-82m-v1.0-onnx-q8-af-heart`
- package fingerprint:
  `03702762c09a71ee54b7ea3bfa4939d1c622b01d68709e2180a39ca62ec264b0`
- voice tensor: little-endian float32, shape `[510, 256]`, 522,240 bytes,
  SHA-256 `d583ccff3cdca2f7fae535cb998ac07e9fcb90f09737b9a41fa2734ec44a8f0b`
- rights state: restricted
- consent state: unknown
- commercial and redistribution classification: restricted
- production export eligible: false

Only facts supported by the pinned provider material are retained. The product
does not infer a person's identity, biography, age, ethnicity, nationality,
profession, personality, or likeness. Any provider-declared presentation
category is labeled as provider-declared metadata, not an independently
verified personal fact.

The exact package exposes one compatible voice. The product therefore cannot
provide six distinct real candidates. Local verification uses that one eligible
profile for two narrator-purpose clips and four character-purpose clips and
records the inventory limitation explicitly. Repeated bytes or machine signal
properties are not represented as evidence of voice diversity or artistic
fitness.

## Restricted activation

Every real session displays this exact warning:

> Private local audition only. This voice is not cleared by Cinematic Story Studio for production export, commercial distribution, marketplace resale, cloning, or real-person imitation.

Its UTF-8 SHA-256 is
`13b8747ea2ced9de9cc1d0f67b5c018b25de7de02359a1480744db4a37939645`.

Creating a real session requires a new acknowledgement and a bounded reason.
The service, not the renderer, supplies the actor and timestamp. The immutable
acknowledgement freezes the warning, provider, model, package, tensor, voice,
catalog, rights record, current Phase 3A restricted-rights correction, current
model-install acknowledgement, current assignment and cast snapshot, runtime
profile, and model verification. Acknowledgement authorizes only the displayed
private audition; it is not legal clearance, consent, quality approval, or
commercial approval.

Stale IDs, revisions, or fingerprints fail closed. Fixture sessions reject the
real-voice acknowledgement instead of silently accepting or ignoring it.

## Casting and reapproval

The Auditions workspace never writes a cast assignment. To replace a fixture
voice with `Local Voice 001`, the user must return to the Casting workspace and:

1. load candidates for the governed role;
2. select `Local Voice 001` through the immutable `select_voice` correction;
3. lock the new assignment revision;
4. acknowledge the exact restricted rights warning;
5. resolve any explicit reuse conflict; and
6. reapprove the narrator, character, and complete-cast gates as applicable.

This creates new history and a new approved cast snapshot. It never rewrites a
prior assignment or silently restores a prior approval. Catalog, rights,
assignment, snapshot, pronunciation, package, or runtime drift invalidates only
the dependent evidence selected by the existing invalidation rules.

## Product path

Real synthesis follows the authenticated application path:

`Electron renderer -> validated IPC -> authenticated loopback service -> governed audition job -> owned provider worker -> verified Kokoro/ONNX package -> staged WAV -> integrity/QC -> atomic publication -> authenticated playback`

Requests are bound to the exact cast assignment, approved snapshot, voice,
rights record, activation, package, tensor, normalization plan, pronunciation
plan, runtime instance, and output profile. The provider accepts at most 509
content tokens and fails rather than truncating or chunking silently. Output is
24 kHz mono PCM16 WAV within the existing duration and byte limits.

The worker remains a managed child process with an authenticated handshake,
argument-array launch, `shell=false`, Windows Job Object ownership, bounded
deadlines and protocol frames, no silent retries, and no permitted network
access during synthesis. Network observations are process-scoped endpoint and
Python socket-denial evidence; they are not described as a complete OS firewall
or packet capture.

## Comparison and human decision

The workspace labels fixture and real clips separately, does not autoplay, and
supports load, play, pause, seek, replay, stop, and a bounded comparison set.
For a real per-role decision, the user must explicitly confirm listening to the
exact clip. The attestation freezes clip ID, clip revision, clip fingerprint,
audio-artifact ID, artifact SHA-256, disposition, actor, timestamp, and an
attestation fingerprint.

The only valid mappings are:

- approve -> acceptable
- request changes -> needs changes
- request changes -> undecided (the existing non-approval review envelope;
  the exact listening disposition remains `undecided`)
- reject -> unacceptable

Fixture and aggregate reviews reject a listening attestation. Readiness accepts
a real-role approval only while its exact acceptable attestation and all bound
evidence remain current. Automated product-path verification deliberately
leaves the human listening decision pending.

## Evidence separation

- Hosted CI uses deterministic fixture audio and synthetic Phase 3B.1 metadata.
  It proves the real provider fails closed without model bytes and proves no
  model, tensor, or real generated audio enters the artifact.
- Local automated verification uses the exact ignored package and exact built
  application to create real narrator-purpose and character-purpose clips,
  prove authenticated playback, restart restoration, cache behavior,
  invalidation, ownership, and graceful exit.
- Human listening evidence is a separate private ignored package. It is never
  committed or uploaded.

## Reproducible local gate

The gate accepts no model URL and performs no download. Prepare one ZIP from
the already verified ignored `local-models/kokoro-phase3b` directory at the
fixed ignored path `local-models/kokoro-phase3b-package.zip`. Preserve an
existing ZIP until its status is understood; do not overwrite it merely to
rerun the gate. Build the exact Windows application, freeze the source as a
clean commit, and run:

```powershell
pnpm --filter @cinematic-story-studio/desktop run test:e2e:packaged:phase3b1-real
```

The runner derives the exact `release/<version>/win-unpacked/Cinematic Story
Studio.exe`, Git head, evidence filenames, fixed ignored ZIP, and fixed ignored
render root. It refuses a dirty tracked or untracked source tree while leaving
ignored models, audio, databases, caches, manifests, and prior evidence
untouched. The first two launches preserve the public deterministic packaged
gate. Optional launches three and four install the selected ZIP through the
native Electron dialog boundary, generate real local clips, restore them, and
prove exact owned-process exit. The completed private package is written below
`local-renders/phase3b1-real-product-path`. It includes a generated relative-path
PowerShell launcher and retains the matching isolated desktop state in a
separate ignored sibling directory so the exact authenticated clips remain
replayable in the Auditions workspace after the automated process-exit proof.
That private state may contain a local database, model installation, audio, and
caches; it is never committed or uploaded. A failed staging or replay-state
directory is preserved for diagnosis.

See the [Phase 3B.1 known limitations](../architecture/phase-3b1-known-limitations.md)
and [Phase 3B.1 verification record](../evidence/phase-3b1-verification.md).
