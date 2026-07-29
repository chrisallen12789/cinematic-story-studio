# Phase 0 verification evidence

Verified on Windows on 2026-07-29. The screenshot and every exercised project use only
`fixtures/synthetic-story/sample-story.md`; no private manuscript, credential, generated audio, or
user project was used.

## Automated results

| Gate | Result |
| --- | --- |
| Frozen dependency install | Passed with pnpm 11.9.0 and hash-enforced Python requirements |
| Repository and schema tests | 13 passed |
| Python lint and strict typing | Passed across 16 service source files |
| Local-service tests | 73 passed; one upstream Starlette TestClient deprecation warning |
| Desktop lint and TypeScript | Passed |
| Desktop component/unit tests | 21 passed across 4 files |
| Development Electron E2E | 1 passed: create, import, analyze, correct, restart, restore |
| PyInstaller service build | Passed; one-file service starts and answers its controlled bootstrap |
| Unpackaged Electron build | Passed with the bundled service present under `resources/service` |
| Packaged Electron E2E | 1 passed: the actual packaged executable completed the full restart flow |
| Node dependency audit | No known vulnerabilities |
| Python dependency audit | No known vulnerabilities |
| Windows CPython 3.12 lock simulation | Every pinned wheel and hash resolved |

The packaged test launched the built executable with isolated temporary application data, imported
the two-chapter/three-scene fixture, saved a human speaker correction, closed the application,
launched it again, and observed the persisted correction. It then removed only its owned temporary
directory and confirmed that both owned Electron process trees exited.

## Capability probes

- FFmpeg: an explicit-path, shell-free probe detected `8.1.2-full_build-www.gyan.dev` with the
  required decode and PCM-WAV encode capabilities. The executable loudness test also ran.
- Kokoro: the development-only, fixed-loopback `/health` probe reported available on this
  workstation. No story content was sent.
- Cloud language and speech adapters stayed disabled, and the application performed no cloud
  content submission.

## Packaged UI

![Packaged application after restart with a persisted human speaker correction](phase-0-packaged-ui.png)

The unpackaged build output and staged service are intentionally ignored development artifacts.
They are reproducible with `pnpm build` and are not committed.
