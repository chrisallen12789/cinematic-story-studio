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
