# Prototype v3 forensics inventory

**Audit date:** 2026-07-29

**Gate result:** **FAIL for direct migration; PASS for the sanitized reference subset only**

## Scope and handling

The reviewed artifact was the safely extracted `Cinematic_Story_Studio_v3.0/`
directory held outside the repository. Evidence references below are relative to
that directory so no personal filesystem path is recorded.

The review was read-only. No prototype Python, batch, PowerShell, HTML, model, or
audio was executed or imported. The inventory used recursive metadata enumeration,
SHA-256 fingerprints, strict UTF-8 decoding, JSON shape inspection with values
suppressed, source inspection, known-token secret patterns, and binary-header
inspection. Private text and credential values were never copied into this report.
Fingerprints for private-content-bearing files are deliberately withheld because a
public fingerprint can itself confirm possession of a private work.

## Artifact summary

- 29 files, one root plus eight subdirectories, 16,094,043 bytes total.
- 23 text files decode as valid UTF-8.
- Six binary files: one PNG, three WAV files, and two CPython bytecode files.
- Three generated-output directories are empty: `audio_cache/`,
  `Cinematic_Renders/`, and `Rendered_MP3/`.
- No `.env`, database, log, native executable, downloaded model, MP3 render,
  package lock, dependency manifest, test, license, or SBOM is present.
- The artifact is not an Electron application. It is two Python HTTP servers with
  inline HTML/CSS/JavaScript front ends plus Windows launch/install scripts.

| Location | Files | Bytes | Classification |
| --- | ---: | ---: | --- |
| root | 21 | 13,351,994 | source, HTML, JSON, scripts, docs, private data, cover |
| `__pycache__/` | 2 | 143,798 | generated CPython 3.13 bytecode |
| `audio_cache/` | 0 | 0 | empty synthesized-speech cache |
| `cinematic_cache/` | 3 | 611,458 | generated WAV sound cache |
| `Cinematic_Renders/` | 0 | 0 | empty generated-render directory |
| `piper_voices/` | 1 | 277 | model-folder instructions; no model present |
| `projects/` | 1 | 1,986,175 | private project state and duplicated manuscript |
| `Rendered_MP3/` | 0 | 0 | empty legacy generated-render directory |
| `sound_library/` | 1 | 341 | placeholder instructions; no sound assets |

## Complete file inventory

Hash entries are the first 12 hexadecimal characters of SHA-256 for
non-private-bearing files. `withheld` means the hash was computed for audit use but
is not published.

| Path | Bytes | SHA-256 prefix | Classification | Disposition |
| --- | ---: | --- | --- | --- |
| `__pycache__/cinematic_story_server.cpython-313.pyc` | 73,767 | `bbf19c1b2230` | generated binary bytecode | Exclude |
| `__pycache__/voice_hub_server.cpython-313.pyc` | 70,031 | `924691561e6d` | generated binary bytecode | Exclude |
| `book.json` | 1,521,475 | withheld | private manuscript dataset | Exclude |
| `cinematic_cache/c6a4e45d2c4a737bbb39cfe515f75707cc830c10cd4d86ff8ccf8eb07957cd6c.wav` | 261,590 | withheld | generated audio/cache binary | Exclude |
| `cinematic_cache/dc7eeca44ada78f45b88471873d7f2d4f030d3d2339346a29190954b43c11aa6.wav` | 261,590 | withheld | generated audio/cache binary | Exclude |
| `cinematic_cache/fdb8ad114c902985aba28cc224040852e454b217ce67edd0c2c5dbf4f7b246b2.wav` | 88,278 | withheld | generated audio/cache binary | Exclude |
| `cinematic_config.json` | 496 | `5e8ef325b113` | runtime configuration, no secret fields | Sanitize to reference example |
| `cinematic_index.html` | 29,329 | `84a42ac2469` | HTML5/CSS/JavaScript application source | Exclude; capability reference only |
| `cinematic_story_server.py` | 51,235 | `e36ffb992bb6` | Python application/server source | Exclude; rewrite |
| `cover.png` | 4,434,748 | withheld | 1600×2560 private/proprietary cover binary | Exclude |
| `current_project.txt` | 19 | withheld | machine-local/private project state | Exclude |
| `deepgram_voices.json` | 16,340 | `923bc859fc53` | 103-entry provider voice catalog | Exclude; provenance and staleness unknown |
| `INSTALL_CINEMATIC_DEPENDENCIES.bat` | 425 | `1a0e51f47e8` | unpinned dependency/install script | Exclude |
| `INSTALL_FFMPEG_FOR_MP3.bat` | 350 | `1ec3521b6b4c` | unpinned FFmpeg install script | Exclude |
| `INSTALL_OPTIONAL_AWS_SUPPORT.bat` | 251 | `2f07c372a68a` | unpinned optional dependency script | Exclude |
| `kokoro_voices.json` | 7,573 | `2c6cba45c279` | 54-entry local voice catalog | Exclude; provenance and staleness unknown |
| `Legacy_Standalone_Browser_Player.html` | 7,193,761 | withheld | HTML/JavaScript plus embedded manuscript and PNG | Exclude |
| `Legacy_Voice_Hub_v2.2.html` | 33,580 | withheld | legacy HTML/JavaScript with story-specific audition text | Exclude |
| `OPEN_ME_FIRST.bat` | 69 | `9007dfb36515` | launcher wrapper | Exclude |
| `piper_voices/README.txt` | 277 | `9fe401b8ad52` | model-folder instructions | Exclude |
| `PLATFORM_ARCHITECTURE_v3.md` | 3,329 | `172c15adc4eb` | historical architecture notes | Exclude; concepts mapped separately |
| `projects/elias-throne-sample.json` | 1,986,175 | withheld | private project, analysis, and manuscript copy | Exclude |
| `README_CINEMATIC_STUDIO.txt` | 3,495 | `b99df04483cb` | historical operator documentation | Exclude |
| `README_FIRST.txt` | 3,495 | `b99df04483cb` | byte-identical duplicate documentation | Exclude |
| `sound_library/README.txt` | 341 | `f092bdf52bcc` | placeholder/licensing note | Exclude |
| `START_CINEMATIC_STORY_STUDIO.bat` | 970 | `bb38947826b` | Docker/Python launcher | Exclude |
| `START_CINEMATIC_STUDIO_MANUALLY.ps1` | 226 | `471acad52fe2` | PowerShell launcher | Exclude |
| `voice_hub_config.json` | 1,288 | withheld | plaintext credential slots and provider configuration | Sanitize to reference example |
| `voice_hub_server.py` | 49,540 | `f392f43d9805` | Python provider/server source | Exclude; rewrite |

## Languages and implementation shape

| Technology | Evidence | Role |
| --- | --- | --- |
| Python 3 | two `.py` files; type hints use modern built-in generics and unions; bundled bytecode is tagged `cpython-313` | HTTP servers, import/analyze, providers, synthesis, render |
| HTML5/CSS/JavaScript | three self-contained `.html` files; optional chaining, Fetch, Web Speech, Web Locks/local storage APIs | studio UI and two legacy players |
| JSON | six files | configs, provider catalogs, private book, private project |
| Windows batch | five `.bat` files | installation and startup |
| PowerShell | one `.ps1` plus dynamically generated SAPI scripts in Python | startup and Windows speech |
| FFmpeg filter syntax/SSML/XML | embedded in Python | procedural sound, mixing, encoding, Azure speech |
| Markdown/plain text | one `.md` and five `.txt` files | historical notes and folder instructions |

There is no TypeScript, Electron, Node package, SQLite schema, typed API schema,
desktop process boundary, migration system, or test harness in the prototype.
Importing either Python module has filesystem side effects; importing
`cinematic_story_server.py` can also seed a private project.

## Dependencies, services, and scripts

| Dependency or service | How it is referenced | Reproducibility/safety classification |
| --- | --- | --- |
| Python standard library | direct imports | no minimum Python version declared |
| `pypdf` | optional import for PDF | installed with unpinned `pip --upgrade` |
| `boto3` | optional import for Polly | installed with unpinned `pip --upgrade` |
| FFmpeg and ffprobe | subprocess argument arrays | installed by unpinned Winget package; version not recorded |
| Docker Desktop/CLI | default launcher | indefinite wait loop; end-user runtime dependency |
| Kokoro FastAPI image | Docker image using a mutable `latest` tag | no digest, signature, version, or SBOM |
| Piper executable and ONNX models | configurable executable and recursive model directory | no models included; no integrity or license metadata |
| Windows `System.Speech` | dynamically generated PowerShell | Windows-only; unsafe interpolation reviewed separately |
| Browser Web Speech | legacy HTML | playback only; cannot provide renderable audio bytes |
| Cloud APIs | OpenAI, ElevenLabs, Azure, Google, AWS, Deepgram, Murf, Cartesia, PlayHT | optional but no pinned API contract, cost metadata, retry policy, or provenance |

Script behavior:

- `OPEN_ME_FIRST.bat` delegates to the main launcher.
- `START_CINEMATIC_STORY_STUDIO.bat` starts Docker Desktop, waits without a
  deadline, starts or creates a Kokoro container, publishes port 8880, waits again,
  then launches the Python studio on loopback port 8766.
- `START_CINEMATIC_STUDIO_MANUALLY.ps1` optionally starts the existing container
  and launches Python.
- The install scripts mutate the user Python environment and install FFmpeg with no
  version or hash pinning.
- The standalone Voice Hub server uses loopback port 8765. No script supplies a
  stop, update, uninstall, test, or integrity-check workflow.

## Configuration and secret classification

`cinematic_config.json` contains four sections: server port, director selection and
model/storage flag, sound provider/model/mix defaults, and render rate/pause/loudness
defaults. It contains no credential field.

`voice_hub_config.json` contains settings for OpenAI, ElevenLabs, Azure, Google,
AWS, Deepgram, Murf, Cartesia, PlayHT, a local OpenAI-compatible service, and Piper.
Twelve cloud/account secret-shaped fields were present and empty. One local
OpenAI-compatible `api_key` field was non-empty but is consistent with a local
placeholder/sentinel, not a demonstrably live credential. Its value is not
recorded. Known-format scans found no OpenAI, AWS, Google, GitHub, Slack, JWT,
private-key, or credential-in-URL token.

This is not approval of the config design: the server reads and writes all such
values in plaintext. The security review treats that as a release blocker.

## Private content, media, cache, model, and output classification

- `book.json` contains 40 chapters and 5,516 text segments totaling 511,631
  characters. It is a private manuscript.
- The project JSON contains 40 scenes and 5,516 events. Its ordered event text is
  exactly equal to the manuscript segment text, and each scene source is the
  corresponding joined manuscript content. It is a second full manuscript copy.
- `Legacy_Standalone_Browser_Player.html` embeds a third exact copy of all 5,516
  text segments and a byte-identical base64 copy of `cover.png`.
- `Legacy_Voice_Hub_v2.2.html` contains short story-specific audition material.
- `cover.png` is private/proprietary artwork and is not a reusable application
  asset.
- `cinematic_cache/` holds three 44.1 kHz, 16-bit, mono PCM WAV artifacts totaling
  6.93 seconds. They were not listened to; directory and code-path evidence classify
  them as generated sound cache. All generated audio is excluded regardless.
- `audio_cache/` is empty; code shows it would store synthesized speech indefinitely.
- No Piper/ONNX model is present. Model instructions and an external mutable Docker
  image reference are present.
- Both render directories are empty, and no MP3/M4B exists in the artifact.
- The two `.pyc` files are generated, opaque deployment artifacts and are excluded.

## Sanitized reference subset

Only these non-executable files entered the repository:

| Repository file | Sanitization | SHA-256 |
| --- | --- | --- |
| `prototypes/v3-reference/README.md` | newly authored quarantine notice | `f2fc484fef44ae1b2cf0558418b057d2eb6d82a0934695a7fb338847f30fc9bf` |
| `prototypes/v3-reference/cinematic_config.example.json` | reformatted historical non-secret settings | `cb4f5b617c21ffea2a37929d1b41253cc391e07c6497c9d5d73ff8c878e4b13a` |
| `prototypes/v3-reference/voice_hub_config.example.json` | all secret/account values and story-specific instructions cleared; one story-specific key generalized | `9834c8592a5ddfd71c71c1d65b1b9efe62b570df048bf27c85fbdcb4fa2d3cc7` |

No prototype source was judged safe for direct reuse. Provider catalogs were also
excluded because their origin, license, freshness, and completeness are not
documented. Useful behaviors are mapped for clean-room reimplementation in
`prototype-v3-migration-map.md`.
