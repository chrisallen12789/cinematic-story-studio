# Story-analysis threat model

## Assets and trust boundary

Protected assets are the approved manuscript text, source/extraction hashes,
project database and backup, analysis graph, human corrections and gate
decisions, launch token, filesystem/process state, and application
availability. Analysis runs inside the authenticated local service and
consumes only one exact approved extraction.

The renderer, API input, imported text, deterministic parser rules, stored
agent output, cursors, correction values, evidence offsets, and historical
database content are treated as untrusted until validated. Phase 2 adds no
cloud or model runtime and makes no new outbound content path.

## Threats and controls

| Threat | Control | Failure |
| --- | --- | --- |
| Analyze an unapproved or changed extraction | Transactional source/extraction/review/fingerprint preconditions before run creation and publication | Typed stale/gate conflict; no run publication |
| Forged or out-of-range evidence | Exact extraction/text hash plus bounded half-open offset, entity, and scope validation | Reject claim/output |
| Agent fabricates unsupported entity | Evidence-required schemas, confidence/warning policy, bounded synthesis validation | Reject output or retain explicit unresolved warning |
| Automation overwrites human authority | Immutable machine rows, append-only corrections, previous-value fingerprint, protected-correction inputs | Conflict; preserve both histories |
| Approval bypass or stale reapproval | Human-only append endpoint, snapshot/fingerprint binding, prerequisite gates, no agent decision authority | Pending/rejected/stale gate blocks |
| Cross-project ID/cursor use | Project/run foreign keys, scoped repository lookup, cursor query fingerprint | `404`/validation conflict without disclosure |
| Manuscript leaks in logs/evidence | Allow-listed structured fields; no bodies, excerpts, corrected source values, filenames, prompts, or raw exceptions | Redacted stable error |
| Large result exhausts service/UI | Profile entity limits, bounded fan-out/windows, pagination, excerpts, response caps, cancellation/checkpoints | Typed limit/cancellation; old snapshot remains current |
| Quadratic identity/relationship work | Normalized indexes, bounded neighborhoods/candidate sets, scale tests and query-plan assertions | Limit/performance test failure |
| Malformed typed JSON becomes effective | Strict Pydantic/TypeScript/schema validation; unknown mutation fields rejected | `400`/`422`; no write |
| Restart publishes partial work | Durable stage fingerprints plus a verified structure artifact and complete pre-publication checkpoint; final integrity transaction | Interrupted run remains non-current |
| Database migration accepts forged v2 | Frozen structural signature and verified backup before mutation | Read-only startup failure |

## Evidence excerpts

Stored evidence is source identity plus offsets and fingerprints.
Authenticated project/run-scoped entity pages may derive evidence excerpts of
at most 512 Unicode code points and exact-text projections of at most 16,384
Unicode code points. No list response returns the complete manuscript. Job
events, logs, diagnostics, and CI manifests contain no excerpt, exact text, or
correction value; screenshots may show only the public synthetic fixture.
Packaged evidence includes only fixed synthetic-reason digests and requires
every gate digest to match the tested canonical profile, run, and snapshot
before and after restart.

## Residual risk

Deterministic rule-based analysis may be incomplete, culturally biased, or
semantically wrong. Confidence values are not calibrated probabilities.
Human review is required and ambiguity remains explicit.

Phase 2 uses the same Windows account and service process as Phase 1. It is not
a low-integrity sandbox and does not defend against a compromised Windows
account, native dependency exploit, malicious local administrator, or memory
inspection. The absence of cloud calls reduces transmission risk but does not
encrypt SQLite at rest; Windows account/device protection and BitLocker remain
the documented controls.
