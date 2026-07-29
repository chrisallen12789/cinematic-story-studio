# ADR-0006: Keep Docker Development-Only

- **Status:** Accepted
- **Date:** 2026-07-29

## Context

The prototype reaches Kokoro through Docker, which is useful for development but unsuitable as a routine dependency for non-technical Windows users. Requiring Docker adds installation, daemon, networking, resource, licensing, and failure complexity.

## Decision

Retain Docker-based Kokoro as an explicitly development-only provider adapter. Health detection is bounded and degrades to `unavailable` without blocking application startup or project work. Production composition does not start Docker, expose container controls, or instruct users to install it.

Design the same speech-provider contract for a future bundled/managed local Kokoro-compatible runtime. Large models are installed through an explicit verified managed flow, not bundled accidentally or downloaded in automated tests. Docker may also support optional developer integration environments, never end-user storage/runtime.

## Consequences

- Developers can validate useful prototype behavior while production UX remains one-click.
- Docker and future bundled runtimes may differ and each needs its own executable contract tests.
- Phase 0 cannot claim packaged Kokoro speech; it claims only truthful health detection for the development adapter.
- Packaging must later solve native runtime/model size, licensing, integrity, updates, and hardware capability.

## Rejected alternatives

- **Require Docker Desktop:** violates product UX and increases attack/operational surface.
- **Remove Kokoro until packaging is solved:** discards a useful development path and migration evidence.
- **Download a model silently on first launch:** unexpected network/storage/licensing/privacy behavior and unsuitable CI.
