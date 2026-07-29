# Cinematic Story Studio

Cinematic Story Studio is a Windows desktop application for turning authored stories into controlled, resumable cinematic audio productions. The product is being built around an Electron desktop shell, a local Python service, durable SQLite projects, typed production contracts, and interchangeable local or cloud provider adapters.

## Project status

This repository is in Phase 0. The first goal is a secure production foundation and a connected vertical slice: create a project, import a synthetic story, inspect chapters, scenes, narration, dialogue, and characters, correct a speaker assignment, persist that correction, and observe resumable background analysis.

The repository is public. Never commit private manuscripts, credentials, generated audio, project databases, downloaded models, caches, logs containing story text, or machine-specific configuration.

## Development

### Prerequisites

- 64-bit Windows 11 or a supported Windows Server runner.
- Git.
- Node.js 24 and pnpm 11.9.0 installed as normal executables. Corepack is not required.
- 64-bit Python 3.12 from python.org, including `venv` and `pip`. `uv` is not required.

Docker, cloud credentials, speech models, FFmpeg, and signing credentials are not required to install dependencies or run the Phase 0 project/import slice.

### Install and run

From the repository root:

```powershell
pnpm install
pnpm dev
```

`pnpm install` installs the pnpm workspace, creates `apps/local-service/.venv`, and installs `apps/local-service[dev]` in editable mode. It does not download models or require secrets. `pnpm dev` starts the Electron development process. Electron creates a fresh launch token, sends it to its single owned loopback service over inherited standard input, and retains it only in main-process memory. The token is not printed, placed in the environment, or written to disk.

The supported root commands are:

| Command | Result |
| --- | --- |
| `pnpm install` | Install locked Node dependencies and prepare the Python virtual environment. |
| `pnpm dev` | Run the desktop, renderer, and Electron-owned local service. |
| `pnpm lint` | Check desktop TypeScript, Python, helper syntax, repository policy, and diff whitespace. |
| `pnpm typecheck` | Type-check contracts, desktop TypeScript, and the Python service. |
| `pnpm test` | Run schema/tooling tests, service Pytest, and desktop Vitest without cloud credentials. |
| `pnpm build` | Build contracts, a PyInstaller service, renderer/main/preload, and an unpackaged electron-builder directory. |

The development build is written to `apps/desktop/release/0.1.0/win-unpacked`. It is an unsigned, unpackaged CI/development artifact, not a release installer.

### Manual Phase 0 verification

Run the automated gates first:

```powershell
pnpm lint
pnpm typecheck
pnpm test
pnpm build
```

Then run `pnpm dev` and verify:

1. The UI reaches `Backend ready`, and diagnostics show an OS-assigned `127.0.0.1` address without displaying a token.
2. Create project `Synthetic Demo` and import `fixtures/synthetic-story/sample-story.md`.
3. Confirm two chapters, at least three scenes, Mira Vale and Tovin Rook, narration, ordered dialogue, and an uncertain speaker for “We can still turn back.”
4. Correct that uncertain line to the other character with reason `fixture correction`.
5. Close the desktop, confirm its owned service process exits, reopen it, and confirm the source hash, analysis, correction, reason, and project revision persist.
6. Close development mode and launch `apps/desktop/release/0.1.0/win-unpacked/Cinematic Story Studio.exe`; confirm the unpackaged app reaches the same ready state and shuts down without leaving its service running.

Record the commit (`git rev-parse HEAD`), Windows version, exact build command, observed loopback address without its token, service/UI states, persistence result, and owned-process shutdown result. A build directory alone is not launch evidence.

See [CONTRIBUTING.md](CONTRIBUTING.md) for Python helper usage, staged scans, evidence details, and the non-destructive rollback procedure.

## License

No open-source license has been selected. All rights are reserved unless and until the repository owner adds an explicit license.

## Security

See [SECURITY.md](SECURITY.md). Do not report credentials or private manuscript content in a public issue.
