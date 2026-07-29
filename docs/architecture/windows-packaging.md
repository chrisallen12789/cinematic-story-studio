# Windows Packaging

## Packaging target

The production deliverable is a 64-bit Windows Electron application installed by a mature packager (initially `electron-builder` with NSIS), containing a PyInstaller-built local-service executable and compatible managed FFmpeg/ffprobe binaries. Large speech models are separate explicit managed assets; Docker is not installed or required.

Phase 0 must build an unpackaged Electron application and stage the service. It does not claim a signed production installer or release.

## Artifact layout

```text
application/
  Cinematic Story Studio.exe
  resources/app.asar
  resources/service/cinematic-story-service.exe
  resources/tools/ffmpeg.exe
  resources/tools/ffprobe.exe
  resources/contracts/<versioned schema>
  resources/licenses/
```

Executables/native modules that cannot run inside ASAR are unpacked into immutable install resources. Paths are resolved from `process.resourcesPath`, verified beneath the install root, and never taken from the working directory or `PATH` in production. Writable databases, projects, logs, cache, staging, models, and backups live under the appropriate `%LOCALAPPDATA%\Cinematic Story Studio` data roots; exports are user-selected.

## Build pipeline

1. Install pinned Node/Python dependencies from lockfiles.
2. Lint, type-check, and run unit/integration tests.
3. Generate/verify OpenAPI/JSON Schema and TypeScript contract parity.
4. Build renderer, preload, and main for production with source-map/privacy policy.
5. Build the Python service from a pinned spec; include only needed modules/data and perform import/startup smoke tests.
6. Stage the pinned FFmpeg distribution and required license notices; verify hashes, architecture, and capabilities.
7. Assemble the unpacked app; launch smoke-test desktop-to-service handshake, health, SQLite, and shutdown on Windows.
8. For a release candidate, generate SBOM/provenance, scan dependencies/secrets/malware, sign executables and installer, verify signatures, install/uninstall in a clean Windows VM, and retain checksums.

No signing key, provider credential, source fixture beyond synthetic content, database, model, audio, or personal path enters an artifact. Build/test scripts do not download large models.

## Install and launch behavior

The NSIS target is initially per-user install without elevation unless a reviewed requirement demands otherwise. It creates Start-menu entries and offers a desktop shortcut. Launch has no console window and needs no PowerShell, Python, Node, Docker, FastAPI, FFmpeg, or manual port configuration from the user.

Electron main starts only its packaged service, passes the per-launch token via inherited pipe, and supervises it as specified in [desktop-runtime.md](desktop-runtime.md). Service and desktop are one compatibility unit and update together.

## Database and application upgrades

The installed application checks contract/service compatibility before migration. A migration:

- obtains the application/project lock;
- verifies disk space and creates a bounded backup;
- applies numbered forward migrations transactionally where possible;
- runs integrity/compatibility checks;
- retains the old copy until successful startup.

Failure leaves the original usable and presents recovery; it never partially downgrades. Downgrade support is not assumed. Major format upgrades require export/backup and an explicit migration design.

## Windows hardening

- Enable Electron sandbox/context isolation and production CSP; disable remote debugging/DevTools by default.
- Hide child windows, constrain inherited handles/environment, use safe argument arrays, and terminate only the owned process group.
- Restrict DLL/executable resolution to packaged/system locations; do not load from project/cache/current directories.
- Set private app/project directory permissions suitable for the current user and do not broaden ACLs.
- Sign all production executable content with a consistent trusted publisher and timestamp; Windows SmartScreen reputation is an operational release concern.
- Test long/Unicode paths, non-admin accounts, antivirus scanning/locked files, sleep/resume, multiple displays, interrupted install/upgrade, and clean uninstall.

## Uninstall and user data

Uninstall removes installed binaries/shortcuts but preserves user projects and exports by default. A separately confirmed cleanup path may remove app-managed settings/cache/logs/projects after enumerating exact scope and explaining that user exports/backups are excluded. Credentials are enumerated by application-owned references and removed only with explicit scope.

## Updates and rollback

Phase 0 has no auto-update. A future updater must use signed metadata and artifacts, protect against rollback/substitution, stage atomically, update desktop/service/tools as one unit, preserve database backup, and provide a bounded rollback path. It may not execute unsigned scripts or require the user to paste commands.

## Release evidence

A Windows release record states commit, dependency locks, contract/database versions, PyInstaller/electron-builder/FFmpeg versions, hashes/SBOM, signing verification, test/build/installer results, install/launch/service/SQLite/FFmpeg/uninstall observations, limitations, and rollback instructions. A built artifact alone is not evidence that it launched.
