# Provider Adapters

## Boundary

Speech, language analysis, music/effects, and future external capabilities are accessed only through registered provider adapters. Core project, review, job, and local deterministic parsing features do not depend on a provider being installed or reachable.

An adapter implements typed capability-specific ports rather than one unbounded "prompt" interface:

```text
ProviderAdapter
  descriptor() -> ProviderDescriptor
  health(context) -> ProviderHealth
  capabilities() -> Capability[]

SpeechProvider
  listVoices(query, context) -> VoicePage
  estimate(request, context) -> CostEstimate
  synthesize(request, context) -> ProviderArtifact
  reconcile(providerRequestId, context) -> ProviderOperationStatus

AnalysisProvider / AudioAssetProvider
  estimate(...)
  execute(typedRequest, context) -> typed result/artifact
  reconcile(...)
```

`InvocationContext` carries correlation/job/attempt IDs, deadline, cancellation signal, idempotency key, approved content-disclosure decision, credential reference, and resource/cost policy. It does not expose a database handle or renderer.

## Descriptor, health, and result

`ProviderDescriptor` includes stable provider/adapter IDs and versions, `local|cloud|development`, capabilities, configuration schema, content categories, network destinations/policy links, credential needs, determinism/seed support, cancellation/reconciliation support, and licensing notes.

Health is one of:

- `available`: required local/runtime/network/credential checks passed;
- `degraded`: usable with a declared limitation;
- `unavailable`: dependency/network/runtime is absent or failed;
- `unauthorized`: required credential is absent/rejected;
- `disabled`: user/policy disabled the adapter.

A health result includes checked time, short expiry/staleness, sanitized version/capability metadata, and an allow-listed reason/remediation code. It never includes a credential, provider response body, story content, full URL with query, container output, or personal path. Health checks are bounded and content-free.

Every successful invocation returns:

```text
adapter/provider/model/voice identity and versions
provider request ID when safe and useful
input/configuration hashes
determinism/seed declaration
verified artifact or typed output
warnings and quality metadata
usage/cost units, currency, estimate/actual status
started/completed times
```

## Credentials and configuration

Secret values are written/read/deleted through a `CredentialStore` implemented with Windows Credential Manager or an equivalent OS-protected mechanism. SQLite stores an opaque credential reference, provider ID, non-secret settings, and last health state only. Renderer IPC cannot read a secret back; it can set, replace, test, or revoke through narrow main/service operations.

Environment credentials are development/test opt-in only, never packaged defaults, and are redacted by field name/value scanning. Configuration examples contain placeholders. Revocation invalidates in-memory clients and health caches.

## Cloud consent and privacy

Cloud adapters are disabled until configured. Before each new provider/content category/purpose combination is transmitted, the service requires a durable, current `ContentDisclosureDecision` identifying:

- provider and destination class;
- data categories (for example source excerpt, character metadata, voice direction, or generated audio);
- purpose and requested scope;
- policy/retention link/version presented;
- user decision, time, and project scope;
- expiration/revocation rules.

The adapter receives only the minimum approved content. Consent to a health check is not consent to transmit story data. Changing provider, category, purpose, or materially changing policy requires another decision. Logs/telemetry never receive transmitted content.

## Reliability and cost

- Requests have explicit connect/operation deadlines, bounded response size, and cancellation.
- Retry only idempotent/reconcilable operations for declared transient errors, with bounded exponential backoff and jitter.
- Use provider idempotency keys when supported. After an ambiguous timeout, reconcile before resubmitting paid/non-idempotent work.
- Rate/concurrency limits are per adapter and do not block local health/project APIs.
- Cost estimates and configured per-job/project limits are checked before execution; actual metering is recorded when returned.
- A fallback provider is a policy/casting decision, never an invisible exception handler. Its identity becomes provenance and may require approval again.
- Circuit breaking may mark an adapter degraded/unavailable but may not convert failure into fabricated output.

Provider errors map to stable categories such as `unauthorized`, `rate_limited`, `timeout`, `unavailable`, `invalid_request`, `policy_blocked`, `content_rejected`, and `invalid_output`. Raw SDK/provider messages are sanitized and retained only in an explicitly private diagnostic channel when policy permits.

## Initial adapters

- **Deterministic/fake adapters:** test-only, fixed typed results, never enabled in production composition.
- **Local deterministic analyzer:** Phase 0 chapter/scene/dialogue baseline; no network or credential.
- **Docker Kokoro:** development-only health/speech adapter. Absence is `unavailable`; Docker is never started implicitly in packaged builds.
- **Bundled local speech runtime:** future production adapter with the same speech contract; models are installed/verified through an explicit managed flow, not downloaded in tests.
- **Cloud speech/analysis adapters:** optional future implementations with disclosure, credentials, cost, and provenance.

Phase 2 whole-book story intelligence uses only the deterministic local
runtime-agent implementations. Provider-neutral analysis capability interfaces
remain architectural extension points, but no cloud adapter, local model
download, credential flow, network destination, or semantic-provider fallback
is enabled by the Phase 2 composition root. A future implementation must
preserve the exact approved-input, evidence, confidence, correction, gate,
privacy, and provenance contracts and requires separate executable evidence.

Phase 3A adds versioned `VoiceProviderDescriptor` and
`VoiceModelDescriptor` catalog metadata without enabling the `SpeechProvider`
invocation surface. Descriptors separately declare runtime/catalog
availability, synthesis implementation, network/credential requirements,
languages, output metadata, rights-metadata capability, execution location,
license classification, health, deprecation, and provenance. Catalog
availability never means synthesis is available.

The repository-owned fixture contains one available local development
descriptor and one disabled cloud-capable descriptor. Both report
`synthesisImplemented: false`; no model implementation, transport, credential
operation, provider SDK, content disclosure, or manuscript-bearing request is
composed. The disabled descriptor and remote model are governance test data
only. Exact fields, identities, and claim limits are in
[voice catalog and casting architecture](voice-casting.md) and
[voice rights and consent](../security/voice-rights-and-consent.md).

FFmpeg is a managed application tool behind `AudioToolchain`, not a creative provider, but uses the same typed health/result/error discipline.

## Adapter verification

Contract tests run against every adapter implementation with a fake credential store/transport and verify descriptors, health states, timeout/cancel, idempotency, error mapping, redaction, output validation, provenance, and cost fields. Real-provider tests are opt-in, never run with untrusted pull-request code, never print secrets/content, and must be separable from the offline CI gate.
