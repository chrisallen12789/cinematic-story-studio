# ADR-0002: Use Electron for the Windows Desktop Shell

- **Status:** Accepted
- **Date:** 2026-07-29

## Context

The product needs a polished, accessible Windows UI, native file/dialog/lifecycle integration, automatic local-service startup, and a normal installer. The team needs TypeScript/React productivity while containing the security risk of web content with desktop privileges.

## Decision

Use Electron, React, TypeScript, and Vite. Electron main owns native capabilities, launch-token/service supervision, credential operations, and authenticated service calls. A sandboxed renderer communicates only through a versioned, runtime-validated, allow-listed preload API.

Enable context isolation, sandboxing, restrictive CSP/navigation, and disable Node integration, remote modules, production remote debugging, and generic IPC. The renderer never receives backend credentials or direct filesystem/process access. Package initially with electron-builder/NSIS.

## Consequences

- One UI stack supports desktop workflow and strong component/end-to-end testing.
- Electron adds application size, update cadence, and Chromium/Node security maintenance.
- Security depends on keeping a narrow main/preload boundary and promptly updating Electron.
- Native modules and bundled service/tool paths require Windows packaging/smoke tests.

## Rejected alternatives

- **Browser UI to a manually started service:** violates one-click/offline desktop experience and broadens local-server exposure.
- **Tauri now:** smaller footprint, but adds Rust/tooling/integration risk before the vertical slice; may be reconsidered with measured needs.
- **Python GUI toolkit:** tighter service language match but weaker fit for the planned React UI/design ecosystem and typed preload boundary.
