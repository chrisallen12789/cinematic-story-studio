# Security Policy

## Reporting a vulnerability

Do not disclose vulnerabilities, credentials, unpublished manuscript text, or private project data in a public GitHub issue. Contact the repository owner through a private channel and include only the minimum information needed to reproduce the issue.

If a credential is exposed, revoke or rotate it before attempting repository cleanup. Public Git history must be treated as permanently disclosed.

## Data handling

Cinematic Story Studio is local-first:

- The backend must bind only to loopback interfaces.
- Story content must not enter logs, telemetry, or cloud requests by default.
- Cloud transmission requires an explicit user action and provider-specific disclosure.
- Provider credentials must use operating-system-backed secure storage and must never be stored in plaintext SQLite records.
- Generated audio, local models, caches, project databases, and manuscripts must remain untracked.

## Supported versions

The project is pre-release. Security fixes apply to the latest commit on the default branch and active development branches only.
