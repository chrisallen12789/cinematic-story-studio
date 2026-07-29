# Cinematic mixing policy

## Creative hierarchy

The spoken story is always primary:

1. narration and dialogue intelligibility;
2. intentional performance and silence;
3. story-relevant Foley;
4. location ambience;
5. restrained music.

An effect may be realistic and still be wrong if it competes with a word,
changes the meaning, or exhausts the listener. Silence is a valid design choice.
The sound and music agents propose cues; only approved cues enter a render.

## Deterministic session layout

Use stable track order: narration, character dialogue ordered by stable
character id, Foley, ambience, music, then buses. Cue placement uses timeline
milliseconds, explicit duration, gain, pan, fades, asset hash, and placement
fingerprint. Equal timestamps are ordered by cue class then cue id. No renderer
may depend on directory enumeration, wall-clock time, or provider response
order.

The render manifest records all assets, entity revisions, approvals,
provider/model versions, processing configuration, seed, FFmpeg/tool versions,
and output profile. A retry may reuse a checkpoint only when the complete input
fingerprint matches.

## Dialogue and dynamics

- Establish voice clip gain before bus compression; avoid using a final limiter
  to repair inconsistent speech synthesis.
- Use gentle dialogue-bus control and retain expressive dynamics. Heavy
  broadband compression requires explicit listening review.
- Side-chain ambience and music from the dialogue bus. Start with 3–6 dB of
  transparent ducking, short look-ahead/attack, and a release long enough to
  avoid pumping; tune per scene.
- Prefer spectral or arrangement changes over deeper ducking when masking
  persists.
- Preserve at least 3 dB of mix-bus headroom before loudness normalization.
- The final limiter is a safety stage and must respect the quality standard's
  true-peak ceiling.

Numeric settings are versioned profile defaults, not permission to skip
measurement or listening review.

## Ambience, Foley, music, and transitions

Ambience establishes space and may bridge edits, but must not become a constant
wall. Loop seams require crossfades and listening review. Foley should be
selective, synchronized, and narratively useful; do not sonify every described
action.

Music requires verified or user-attested rights. Unknown or restricted rights
block approval. Avoid continuous underscoring by default. Music should enter and
exit at motivated boundaries, avoid dialogue-dense frequency and rhythmic
space, and duck under speech. Generated music records provider, model, prompt
fingerprint, seed where supported, and rights status without logging private
story text.

Scene transitions use room tone, tail management, and equal-power crossfades
where appropriate. Chapter joins are checked both in isolation and continuous
playback. Do not truncate reverberation, final consonants, or intended silence
to meet a target duration.

## Failure and review

Missing required clips, decode errors, mismatched format, clipping, unknown
rights, stale approvals, or fingerprint mismatch fail the render. Optional
ambience/music may be omitted only when policy marks it optional and a warning
is recorded; omission cannot masquerade as the approved mix.

The first-scene gate establishes the production's voice balance, spatial style,
density, transitions, and listening comfort before batch rendering. Chapter and
final-master gates repeat technical QC and continuous listening checks. Changes
after approval invalidate dependent renders and require new evidence.
