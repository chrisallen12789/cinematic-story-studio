# Repository Operating Constitution

These rules govern every engineering agent and every change in this repository.

## Before changing code

1. Read the relevant product, architecture, security, agent, audio, and decision records.
2. Inspect the nearest `AGENTS.md`; narrower files may add rules but may not weaken this constitution.
3. Confirm that the work does not expose credentials, manuscripts, generated audio, local databases, caches, models, logs, or personal paths.
4. State the behavior and acceptance checks the change will affect.

## Architecture and implementation

1. Preserve imported story text byte-for-byte or character-for-character as appropriate. Rewriting requires explicit user authorization.
2. Use typed contracts and explicit interfaces at every subsystem boundary.
3. Do not bypass desktop, service, storage, provider, agent, or render boundaries.
4. Add dependencies only with a documented need and a maintained, security-conscious choice.
5. Keep local and cloud provider adapters interchangeable; the application must launch without any cloud provider.
6. Keep project, production, provenance, and render manifests versioned and deterministic.
7. Flag uncertain dialogue attribution with confidence and warnings; never invent certainty.
8. A human correction is durable provenance and must never be silently overwritten by automated analysis.
9. Preserve reproducibility through explicit versions, inputs, configuration, seeds when applicable, and stable ordering.
10. The installed desktop application must be usable without PowerShell. Docker is a development option, never an end-user runtime requirement.
11. Use safe subprocess argument arrays, loopback-only networking, validated paths, and redacted structured logs.
12. Never commit generated audio or private user content.

## Runtime production agents

Runtime story-production agents are application components, not autonomous engineering agents. They must have versioned identifiers, accepted inputs, typed outputs, confidence, warnings, human-review requirements, retry and failure policies, provenance, provider/model identity, and cost metadata where applicable. Approval gates are durable and inspectable.

## Verification

1. Add or update tests for every behavior change.
2. Run the relevant formatter, lint, type-check, unit, integration, build, and end-to-end checks.
3. Never claim an audio capability works without an executable test or a recorded, verified manual result.
4. Update documentation when behavior, architecture, security posture, or operating procedures change.
5. Report limitations and unverified behavior precisely, including the next executable verification command.

## Change control

1. Keep commits focused and use conventional commit messages.
2. Scan staged content for secrets and private material before every commit.
3. Leave a clean worktree after published work.
4. Never force-push, rewrite shared history, delete remote branches, enable auto-merge, merge a pull request, or mark your own draft pull request ready.
