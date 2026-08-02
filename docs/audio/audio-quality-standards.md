# Audio quality standards

## Status

These are acceptance targets for future executable audio pipelines, not a claim
that Phase 0 meets them. A render passes only when measurements from the exact
rendered asset are stored as quality-control findings and tied to its
`RenderManifest`.

## Canonical production format

- Process and archive the working master as 48 kHz, 24-bit PCM WAV, stereo.
- Use floating-point processing internally and dither only at a bit-depth
  reduction boundary.
- Preserve mono voice sources as mono until spatial placement; do not create
  fake stereo.
- Resample once at a controlled boundary with the resampler and settings
  recorded in the manifest.
- The timeline uses integer milliseconds in version 1 and stable cue ordering.

Alternate user exports may be 44.1 or 48 kHz and mono or stereo when the chosen
profile permits it. MP3 and M4B are delivery derivatives, never the canonical
master.

## Measured acceptance targets

For `cinematic_stereo_v1`:

| Measurement | Target | Blocking condition |
|---|---:|---:|
| Integrated program loudness | -18 LUFS, tolerance ±1 LU | Outside -19 to -17 LUFS |
| Maximum true peak | no higher than -1 dBTP | Any sample path above -1 dBTP |
| Loudness range | 4–12 LU | Outside range without an approved creative exception |
| DC offset | effectively zero | Absolute mean above -60 dBFS |
| Unintended digital silence | none | Missing clip or unexplained gap above 2 seconds |
| Channel/sample rate | manifest value | Any mismatch |

Dialogue intelligibility is the priority. Dialogue should normally remain
within a six-LU short-term window across a scene; exceptions for whispers,
distance, or shouting require direction and QC review. Background beds must not
mask consonants or force the master limiter to control routine dialogue peaks.

Delivery profiles for a specific distributor must be separate, versioned
profiles because distributor requirements can differ. Do not infer compliance
with an audiobook platform from the cinematic master targets.

## Boundary and asset checks

Every scene, chapter, and master test must check:

- finite samples, duration greater than zero, expected channel count and sample
  rate, and decodable start-to-finish content;
- no clipping, NaN/invalid samples, truncated final phonemes, duplicate clips,
  missing dialogue, or unplanned overlaps;
- intentional leading/trailing room tone and no abrupt waveform discontinuity;
- deterministic cue start, duration, fades, ordering, and content hashes;
- provider failure recovery without inserting silence as a false success; and
- voice, pronunciation, timing, ambience, and music continuity against approved
  records.

Test fixtures use short synthetic audio and mocks. Automated tests must not
download models or use private manuscripts.

## QC evidence

Measurements follow ITU-R BS.1770 loudness/true-peak semantics as implemented by
the selected maintained measurement tool. The manifest records tool and version,
filters, output profile, hashes, and configuration fingerprint. Each failure is
a `QualityControlFinding` with category, severity, measured value, threshold,
timeline location, and review requirement.

An unavailable required measurement is a blocker. Listening review complements
measurement; it never replaces missing technical evidence. No documentation,
UI, or agent may describe an audio capability as working until an executable
test or recorded manual verification supports it.

## Phase 3B audition integrity profile

Short Phase 3B auditions are source previews, not production masters, and use a
separate `audition-pcm-wav-integrity` profile: RIFF/WAVE, uncompressed signed
16-bit PCM, 24 kHz, mono, nonzero frames, at most 30 seconds, and at most
24 MiB. The service records frame count, duration, byte size/SHA-256, sample
rate/channels/width, peak dBFS, silence ratio, clipped-sample count, blocking
codes, warning codes, tool/profile version, and provenance before atomic
publication or cache acceptance.

The version 1 profile uses deterministic, boundary-exclusive warning and
blocking thresholds:

| Measurement | Warning | Blocking |
|---|---:|---:|
| Duration | below 500 ms or above 20,000 ms | below 100 ms or above 30,000 ms |
| RMS level | below -40 dBFS | all frames silent |
| Silent-frame ratio | above 700,000 ppm (70%) | all frames silent |
| Integer samples at either PCM limit | one or more samples | above 1,000 ppm (0.1%) |
| Provider output profile | any channel, sample-rate, or sample-width mismatch | any such mismatch |

Values exactly on the 500 ms, 20,000 ms, 70%, or 0.1% warning/blocker
thresholds remain on the non-escalated side. The clipped-sample count is the
exact number of interleaved integer samples equal to the signed PCM minimum or
maximum, rather than a Boolean indicator. Silence is measured per frame using
the inspector's fixed -60 dBFS threshold. These thresholds and the hard format,
size, and duration limits are included in the profile fingerprint.

Malformed, empty, oversized, excessive-duration, silent, excessively clipped,
or path/hash/metadata-inconsistent output is blocking. Clipping risk, low
level, high silence ratio, unusual in-bounds duration, and provider-profile
mismatch remain explicit warnings; they are never silently repaired. These
checks establish format and signal integrity only.
They do not establish speech intelligibility, naturalness, voice likeness,
artistic fit, consent, commercial clearance, or production readiness. A human
must listen before an audition decision. The private Kokoro result is component-
only evidence; the governed product has no Phase 3A voice/profile/assignment/
rights binding for `af_heart`, so product use remains unavailable until that
binding and separate legal/rights review exist.
