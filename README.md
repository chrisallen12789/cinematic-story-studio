# Cinematic Story Studio

Cinematic Story Studio is a Windows desktop application for turning authored stories into controlled, resumable cinematic audio productions. The product is being built around an Electron desktop shell, a local Python service, durable SQLite projects, typed production contracts, and interchangeable local or cloud provider adapters.

## Project status

Phase 0 established the secure production foundation. Phase 1 is under draft
review and adds local TXT, Markdown, DOCX, EPUB, and text-based PDF ingestion,
immutable original-source storage, persistent extraction jobs, and a durable
Import Review gate. Analysis remains blocked until a human approves the current
source and extraction revision.

The repository is public. Never commit private manuscripts, credentials, generated audio, project databases, downloaded models, caches, logs containing story text, or machine-specific configuration.

## Development

### Prerequisites

- 64-bit Windows 11 or a supported Windows Server runner.
- Git.
- Node.js 24 and pnpm 11.9.0 installed as normal executables. Corepack is not required.
- 64-bit Python 3.12 from python.org, including `venv` and `pip`. `uv` is not required.

Docker, cloud credentials, speech models, FFmpeg, OCR, Office, LibreOffice,
Java, and signing credentials are not required to install dependencies or run
the project/import slice.

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

### Manual Phase 1 verification

Run the automated gates first:

```powershell
pnpm lint
pnpm typecheck
pnpm test
pnpm build
$env:CSS_E2E = "1"
try {
  pnpm --filter @cinematic-story-studio/desktop exec playwright test tests/e2e/persistence.spec.ts
} finally {
  Remove-Item Env:CSS_E2E
}

$releaseRoot = [IO.Path]::GetFullPath(
  (Join-Path (Get-Location) "apps/desktop/release/0.1.0")
)
$env:CSS_PACKAGED_E2E_EXECUTABLE = Join-Path $releaseRoot "win-unpacked/Cinematic Story Studio.exe"
$env:CSS_PACKAGED_E2E_EVIDENCE_PATH = Join-Path $releaseRoot "packaged-e2e.png"
$env:CSS_PACKAGED_E2E_RESULT_PATH = Join-Path $releaseRoot "packaged-e2e-result.json"
try {
  pnpm --filter @cinematic-story-studio/desktop run test:e2e:packaged
} finally {
  Remove-Item Env:CSS_PACKAGED_E2E_EXECUTABLE
  Remove-Item Env:CSS_PACKAGED_E2E_EVIDENCE_PATH
  Remove-Item Env:CSS_PACKAGED_E2E_RESULT_PATH
}
```

Then run `pnpm dev` and verify:

1. The UI reaches `Backend ready`, and diagnostics show an OS-assigned `127.0.0.1` address without displaying a token.
2. Generate binary public fixtures into an isolated temporary directory with
   `node fixtures/synthetic-story/generate-fixtures.mjs --write-directory <temporary-directory>`.
3. Create project `Synthetic Demo`, import the generated `sample-story.docx`,
   and inspect its source hash, format, parser identity, warning list, bounded
   preview, and preservation status.
4. Confirm analysis is unavailable before approval. Approve the current Import
   Review, begin analysis, and correct an uncertain line to the other character
   with reason `fixture correction`.
5. Close the desktop, confirm its owned service process exits, reopen it, and
   confirm the exact source, extraction revision, approval, analysis, speaker,
   reason, and project revision persist.
6. Repeat with the exact executable under
   `apps/desktop/release/0.1.0/win-unpacked/Cinematic Story Studio.exe`; confirm
   both owned Electron and service identities exit after shutdown.

The automated E2E commands use only repository-owned synthetic fixtures and
isolated application-data directories. Record the commit (`git rev-parse
HEAD`), Windows version, exact commands, service/UI states, hashes/revisions,
persistence result, and owned-process shutdown result. A build directory alone
is not launch evidence. OCR, audio production, cloud parsing, signing, and
installer/release publication remain out of scope.

See [CONTRIBUTING.md](CONTRIBUTING.md) for Python helper usage, staged scans, evidence details, and the non-destructive rollback procedure.

## License

No open-source license has been selected. All rights are reserved unless and until the repository owner adds an explicit license.

## Security

See [SECURITY.md](SECURITY.md). Do not report credentials or private manuscript content in a public issue.
