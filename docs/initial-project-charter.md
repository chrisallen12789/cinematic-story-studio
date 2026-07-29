# Initial Project Charter

## Mission

Build Cinematic Story Studio as a secure, local-first Windows desktop product that converts authored stories into inspectable, controllable, resumable cinematic audio productions while preserving the author's original text.

## Phase 0 outcome

Phase 0 establishes repository safeguards, architecture and decision records, typed contracts, prototype migration evidence, and a connected desktop-to-local-service vertical slice. The slice proves durable project storage, TXT/Markdown import, basic chapter/scene/dialogue analysis, human speaker correction, background job control, provider health, and an unpackaged Electron build.

## Product principles

- Preserve source text and human corrections.
- Prefer local processing; make cloud use optional and explicit.
- Keep runtime production agents controlled by contracts and approval gates.
- Make interrupted work resumable and renders reproducible.
- Design the installed Windows experience so routine shell commands are unnecessary.
- Treat security, privacy, accessibility, failure recovery, and observability as architecture, not cleanup work.

## Scope boundary

Phase 0 does not render a private audiobook, bundle large speech models, require Docker for production, publish an installer or release, or claim final audio quality. It creates the safe foundation on which those capabilities can be developed and verified.

## Repository status

The repository is public. No open-source license has been selected.
