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

## Phase 3A exact-artifact gate

The Windows CI verification artifact remains the unpacked application, not an
installer or release. After `pnpm build`, the mandatory packaged E2E launches
the exact
`apps/desktop/release/<version>/win-unpacked/Cinematic Story Studio.exe` with
isolated `APPDATA`, `LOCALAPPDATA`, `TEMP`, and `TMP`. It completes the public
synthetic import and Phase 2 reviews, creates and approves a governed cast,
closes, relaunches the same executable, and restores all Phase 0-3A evidence.

The test establishes Electron and service ownership from the launched process
identity, creation identity, ancestry, and owned-service handshake. It records
only PIDs created by the test, terminates no unrelated process, and fails
safely if ownership is ambiguous. On each close it requires the exact Electron
launcher to exit with code `0`, confirms two inventories in which every owned
PID is absent, and reads a bounded sidecar written beneath the isolated
`userData` directory by Electron main. That sidecar must bind the direct-child
service PID to a ServiceManager stdin-EOF shutdown with exit code `0`, no
signal, and `forceKillUsed=false`. Both closes must end with no forced or
remaining owned PID.

The generated manifest records the workflow head, runner/time, relative paths,
sizes and SHA-256 for the application and staged/embedded service, exact
staged/embedded equality, Phase 2 prerequisites, Phase 3A
profile/catalog/provider/model/rights identities, role/candidate/conflict/
assignment/correction/gate/restart assertions, and process exit proof. It and
the screenshot/result are short-lived workflow artifacts only. See
[Phase 3A verification](../evidence/phase-3a-verification.md).

Artifact upload is conditional on the packaged E2E succeeding, build-evidence
generation and schema validation succeeding, the post-build tracked-content
rescan succeeding, the clean-tree check succeeding, and a final manifest
revalidation proving that the exact application and evidence bytes did not
change after the initial validation. Upload paths are scoped to the validated
application version and the exact staged service executable.
Failure of any gate prevents upload; an archive is not presented as evidence
for a partially verified build.

The workflow is `Phase 3A Windows CI`; uploaded artifact names begin
`cinematic-story-studio-phase-3a-windows-unpacked-`. The build manifest is
schema `4.0.0`, retains the top-level Phase 2 `packagedE2e` schema `4.0.0`,
and adds `voiceCastingContract.packagedE2e` for the Phase 3A result schema
`5.0.0`. The runner and evidence generator write
`phase-3-packaged-e2e-result.json` and
`phase-3-voice-casting-evidence.json` only to their CI-provided generated
paths; neither file is committed.

## Phase 3B exact-artifact extension

The mandatory Windows gate continues to launch the exact unpacked executable
and complete all Phase 0–3A evidence. It then uses only repository-owned
synthetic text and the visibly fixture-only deterministic provider to verify and
activate its virtual package, create an empty project dictionary and an approved
entry, create role-bound sessions/scripts, produce one narrator and two
character clips, load each clip through authenticated IPC, prove a verified
cache hit, supersede an applicable pronunciation, and prove targeted
invalidation plus a new artifact. It records the exact Phase 3B decisions and
restores them after relaunch.

Process evidence is extended to the managed speech worker. The service supplies
the project-owned authenticated worker identity; the test independently
correlates PID, ancestry, executable path/name, creation identity, runtime/
profile/package fingerprints, and Windows Job Object ownership before accepting
it. Service-reported runtime evidence carries the service-computed SHA-256 of the
resolved executable after identity validation. On
both closes, Electron, service, and every exact owned provider-worker PID must be
absent. Ambiguous ownership fails the gate; endpoint enumeration and process
termination are never authorized by name.

The packaged gate samples the owned tree every 100 ms while the UI flow runs.
Before endpoint acceptance it stops that sampler and performs a bounded
refresh-bind-observe-refresh reconciliation. Ledger growth invalidates the
prior observation; each newly adopted service-image PID becomes mandatory on
the next exact-PID query, all live owned service-image PIDs are observed again,
and any earlier non-loopback finding is retained. Endpoint enumeration uses
terminating errors, permits only the exact Windows no-connection result as an
empty set, and revalidates process identity both before and after the query.
Three unstable attempts fail closed. This is bounded process-table evidence,
not a kernel process-event ledger or packet capture; a relevant child whose
entire lifetime falls between samples is not individually recorded.

The generated manifest adds only IDs, revisions, fingerprints, hashes, audio
properties, cache/invalidation/QC/decision assertions, and exact process-exit
evidence. It contains no script text, manuscript text, pronunciation value,
absolute path, model/audio bytes, secret, or private license material. Hosted CI
fixture evidence is explicitly separate from the private workstation real-
provider integration record and is never described as natural-speech quality.

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
