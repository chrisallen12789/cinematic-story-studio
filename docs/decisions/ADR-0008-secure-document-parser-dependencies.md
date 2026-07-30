# ADR-0008: Secure document parser dependencies

- Status: Accepted for Phase 1
- Date: 2026-07-29

## Context

DOCX and EPUB need standards-aware ZIP/XML processing, and PDF needs
page-oriented text extraction. Writing complete XML and PDF parsers in the
application would enlarge the security surface and produce weaker format
coverage. Dependencies must be license-compatible, actively maintained,
exactly pinned, hash locked, buildable in the Windows service artifact, and
usable without a downloader, cloud service, shell, or mutable runtime plugin.

## Decision

Use:

- `lxml==6.1.1` (BSD-3-Clause) for DOCX/EPUB XML with entity resolution,
  network, DTD loading/validation, recovery, and huge-tree mode disabled;
- `pypdf==6.14.2` (BSD-3-Clause) for bounded, text-only, page-aware PDF
  extraction.

The versions are exact in `pyproject.toml` and `requirements.in`; the generated
`requirements.lock` includes SHA-256 hashes. PyInstaller explicitly collects
their submodules. Windows CI installs the frozen lock, asserts the installed
versions, runs parser/security/migration tests, builds the exact embedded
service, and exercises that service through packaged Electron E2E.

Python's standard `zipfile` provides package enumeration and bounded member
reads. The application, not the library defaults, owns path, entry type,
member/expansion/ratio, structure, deadline, section, text, and page policies.

## Alternatives considered

- `python-docx` is capable and MIT licensed, but the application still needs
  low-level package validation and strict relationship/XML control. Adding it
  would duplicate parsing layers without removing `lxml`.
- `mammoth` is BSD-2-Clause but targets conversion to HTML and explicitly does
  not sanitize untrusted input. HTML conversion is unnecessary for canonical
  story text and would add a rendering/sanitization boundary.
- `EbookLib` was rejected because its AGPL licensing is not appropriate for
  this public desktop application and its higher-level API would not replace
  package hardening.
- A home-grown PDF parser was rejected as unsafe and incomplete. OCR engines
  and PDF rendering toolchains are out of Phase 1.
- Runtime downloads or mutable parser plugins were rejected. They would break
  offline operation, provenance, build reproducibility, and public-repository
  protections.

## Consequences

The service artifact grows and dependency review must cover two additional
packages. Parser behavior is versioned in extraction provenance; upgrading
either pin requires regenerated locks, security regression tests, a new
adapter/version decision, and new Import Review for re-extracted documents.
Library selection alone is not a sandbox, so application budgets and safe
configuration remain mandatory.
