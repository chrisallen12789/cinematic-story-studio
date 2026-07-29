# Audio Render Pipeline

## Scope

The render pipeline converts approved, revisioned production data into validated scene, chapter, and master artifacts. It is a background-job workflow, not a synchronous route or a set of ad hoc FFmpeg scripts. Phase 0 verifies FFmpeg capability and architectural contracts; it does not claim completed production audio.

## Internal audio model

- Version 1 timeline locations and durations use non-negative integer milliseconds, matching the public `ProductionTimeline.timebase`. The render compiler converts them once to integer sample positions at the manifest's declared sample rate using a recorded rounding rule.
- Every clip declares sample rate, channel layout, sample format, duration in samples, content hash, provenance, and rights/licensing metadata when applicable.
- Intermediate processing uses a lossless format and sufficient headroom (normally 32-bit float WAV at 48 kHz); delivery encoding is a versioned policy decision.
- Render code does not accumulate floating-point seconds or repeatedly convert timebases.
- Dialogue, narration, ambience, Foley/effects, music, and transitions occupy named tracks/buses with deterministic ordering.

Exact loudness, true-peak, silence, fade, channel, and codec targets come from the versioned audio quality/mixing policies under `docs/audio` and the manifest's `policyVersion`. A build may not silently change them.

## Render manifest

A render job first freezes an immutable `RenderManifest` containing:

```text
schemaVersion, projectId, inputRevision, targetKind/targetRef
story/production/approval references and hashes
timeline timebase/sampleRate/channelLayout and canonically ordered tracks/cues
resolved voice/casting/performance/sound/music decisions
provider/model/voice/tool versions and determinism declarations
source asset hashes and generated-asset requests/cache keys
mix policy, loudness policy, export profiles
seed values where supported
required QC rules and publication targets
```

The manifest is validated, canonicalized, and hashed before work starts. Stable keys, integer units, normalized enum values, and explicit list ordering are required. Volatile timestamps, absolute paths, launch IDs, and machine names are metadata outside the content identity.

A provider that cannot reproduce synthesis makes its returned, verified clip an immutable hashed manifest input for subsequent rerenders. The application distinguishes "same manifest and source clips" reproducibility from "regenerate semantically equivalent speech."

## Pipeline stages

1. **Preflight:** verify required approvals, source/artifact hashes, disk budget, FFmpeg features, provider/content-disclosure authorization, and compatible policy versions.
2. **Resolve:** convert production cues into a canonical timeline; detect missing/overlapping/negative/out-of-range clips before synthesis.
3. **Acquire/generate:** retrieve verified cache entries or call typed speech/audio adapters. Results land in private staging and are decoded/probed before caching.
4. **Conform:** decode to the internal lossless format, map channels, resample with a pinned quality configuration, trim only according to explicit cue metadata, and record measured duration.
5. **Lay out:** compile public integer-millisecond cues to integer sample positions once, apply explicit fades/crossfades and transitions, and render per-track stems.
6. **Mix:** apply versioned gain automation, narration/dialogue priority, dialogue ducking, ambience/effect/music policy, limiting, and silence rules. Creative operations remain visible in the manifest.
7. **Master:** perform the policy's measured loudness workflow (including a deterministic multi-pass measurement when required), enforce true-peak/channel/duration constraints, and encode requested profiles.
8. **Quality control:** probe/decode every output and evaluate duration, channels, sample rate, clipping/true peak, loudness, silence boundaries, missing clips, cue placement, discontinuities, and manifest/hash consistency.
9. **Publish:** after required QC passes/waivers, atomically move immutable artifacts into project storage, commit artifact/QC records, and mark the job successful.

Scene and chapter results feed higher-level composition as immutable assets; they are not re-encoded repeatedly when lossless intermediates are available.

## Cache identity

Cache keys are a canonical hash of every output-affecting input:

```text
adapter + adapterVersion + provider/model/voice identity
exact input bytes/text hash + language
performance parameters + seed/determinism mode
output sample/channel/format request
normalization/preprocessing version
```

Credentials, timestamps, absolute paths, correlation IDs, and cost do not affect content identity. A cache hit is used only after the file hash, metadata, and decode/probe checks pass. Cache writes use a unique staging path plus atomic rename; conflicting writers verify identical output or retain one as a distinct non-deterministic artifact.

## FFmpeg boundary

FFmpeg/ffprobe are application-managed tools behind `AudioToolchain`, not shell commands assembled by route or agent code.

- Resolve the executable from a configured development path or verified packaged resource; never search a user-controlled working directory first.
- Invoke directly with an argument array and `shell=false`.
- Validate all enum/range values and map files/filters from typed structures; never splice provider, filename, or story text into a filter expression.
- Use project-scoped generated temporary names. Use safe path APIs and confirm every resolved path remains inside the expected root.
- Bound execution time, output/stderr bytes, input count, thread/resource use, and inherited environment/handles.
- Parse machine-readable probe output with a schema; bound and sanitize errors before logging.
- Pin/test supported FFmpeg versions and include redistribution/license notices in packaging.

If a complex filter graph cannot be represented safely as arguments, write a generated, validated script file inside private staging and pass its path as one argument. The file must contain only values produced by the typed compiler, never raw story/provider text.

## Failure and cancellation

Each stage is a checkpoint boundary. Cancellation signals adapters/processes, removes current staging, and preserves already verified cache inputs; it never publishes a partial master. Retrying revalidates manifest/input hashes and can reuse immutable successful clips/stems.

Provider failure is isolated to the affected cue. Bounded retry follows adapter policy; optional configured fallback requires an explicit compatible casting/policy decision and becomes new provenance. Missing clips, corrupt cache, incompatible FFmpeg, or failed QC produce a failed/blocked job with exact finding codes rather than silence insertion or fake success.

Publishing is atomic at the artifact-record level. If a database commit fails after file staging, startup cleanup detects the unreferenced staging artifact. If a file move fails after reservation, the reservation is rolled back/reconciled without exposing it as finished.

## Required tests

Synthetic generated tones/silence and deterministic fake providers—never private audio or downloaded models—must verify:

- exact cue milliseconds/compiled sample positions and stable manifest/timeline hash;
- channel count and sample rate;
- missing/corrupt clips and cache verification;
- clipping/true peak and policy loudness target/tolerance;
- leading/trailing/inter-scene silence boundaries;
- dialogue ducking and deterministic cue/fade placement;
- cancellation and orphan-process cleanup;
- provider timeout/retry/fallback policy;
- atomic publication and failed-QC non-publication.

Any claim about an actual provider or cinematic quality also requires an executable integration test or a dated, verified manual result identifying versions and non-private evidence.
