# ADR-0003: Use a Bundled Python FastAPI Local Service

- **Status:** Accepted
- **Date:** 2026-07-29

## Context

Story/document processing, audio tooling, local models, and provider SDKs have strong Python support. The desktop needs a stable typed boundary rather than embedding Python or duplicating domain behavior in Electron. A network service can create local exposure if binding/authentication is careless.

## Decision

Implement the authoritative local backend in Python with FastAPI and Pydantic, packaged as a PyInstaller executable. Electron spawns it as an owned child. In production it binds only `127.0.0.1` on port `0`, receives a per-launch 256-bit token through an inherited control pipe, and authenticates every `/api/v1` route. Electron main proxies typed calls; CORS is disabled.

The service owns use cases, validation, project persistence, jobs, agent orchestration, provider/tool adapters, and render coordination. Routes do not directly query storage or call providers/tools. Development may use an explicit non-default dev token on loopback only.

## Consequences

- Python capabilities are isolated behind OpenAPI/JSON Schema contracts and independently testable.
- The desktop and service must negotiate compatible versions and be packaged/released together.
- Startup, port discovery, authentication, supervision, shutdown, and orphan recovery are product code.
- PyInstaller hidden imports/native dependencies and antivirus behavior require Windows build testing.

## Rejected alternatives

- **Embed Python in Electron:** complicated lifecycle/ABI/security boundary with weaker isolation.
- **Run Uvicorn manually or require installed Python:** violates installed one-click operation.
- **Expose a fixed unauthenticated localhost port:** vulnerable to local web pages/processes and port conflicts.
- **Remote/cloud backend by default:** violates local-first privacy and offline operation.
