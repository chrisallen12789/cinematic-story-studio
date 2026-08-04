# Governed real voice activation architecture

## Decision

Phase 3B.1 is additive and uses database schema v5. No database migration is
introduced. Existing immutable catalog, assignment, correction, installation,
verification, session, clip, review, readiness, and provenance records can
represent the required chain without a schema change.

Historical catalog v1 and casting-profile 1.0.0 records remain immutable. The
current identities are:

| Record | Immutable identity | Fingerprint |
| --- | --- | --- |
| Voice catalog | `governed-local-voice-catalog-v2@2.0.0` | `994a2f77daed881cc4e24201d628ef32a732aa6ee0ff0815745a19772d2828cc` |
| Casting profile | `governed-voice-casting-v1@1.0.1` | `5377949573018b5d3a4f4cd343392155071640364d3ba36be80a1bf4ad58de97` |
| Real provider descriptor | `kokoro-local-onnx@1.0.0` | `1928f3ce835fb32ffd988f1509e05ba4a28449d45a3f04d5582d641ebd657971` |
| Real model descriptor | `onnx-community/Kokoro-82M-v1.0-ONNX@1.0` | `56b5ec0464874cd6f445947846e1ea1f558d0ad38cf01594070ded4c12007024` |
| Real voice profile | `kokoro-local-voice-001@1.0.0` | `dd81588a36a17b429e90ee9b21a80187c10368bab6bd5b8fa584ea01c455a210` |
| Real rights record | `kokoro-local-voice-001-rights-v1@1` | `e801171e684b1125b54bfc4317ae17dac4ca5b92c1500b82b333dc6da357c038` |

The new casting policy has one narrow conditional rule for this exact
restricted, private-audition envelope. It never upgrades pending rights to
verified. Unknown and prohibited rights continue to fail closed.

## Per-role provider resolution

Runtime selection is derived from each assigned voice's governed provider and
model descriptors. A verified active Kokoro installation does not cause
fixture-assigned roles to be sent to Kokoro. Conversely, a missing Kokoro
package does not substitute fixture audio for a real assignment. Provider
selection is exact per role, and an incompatible or unavailable binding is a
visible blocker.

## Activation representation

The create-session request may contain a restricted-activation input with only
the expected inventory fingerprint, expected warning fingerprint, and bounded
reason. The service derives all authoritative fields and stores a canonical
acknowledgement under `AuditionSessionRow.provenance_json.details`. Public
projection exposes only the typed acknowledgement; arbitrary provenance
details, paths, and text remain private.

The acknowledgement composes:

- actor, UTC time, reason, warning text and warning fingerprint;
- provider and model IDs and versions;
- package ID and manifest fingerprint;
- voice profile ID, version, profile fingerprint, provider voice ID, tensor
  relative identifier, tensor size and SHA-256;
- rights record ID, revision, fingerprint, restricted state, unknown consent,
  and false production/redistribution authorizations;
- catalog revision and fingerprint;
- current restricted-rights correction ID and fingerprint;
- model-install acknowledgement event ID;
- current assignment and approved snapshot identities;
- current runtime profile and model-verification fingerprints; and
- one canonical activation fingerprint.

Kokoro sessions require this exact current acknowledgement. Fixture sessions
forbid it. Model-install acknowledgement remains a separate package-management
event and cannot stand in for session activation.

## Listening representation

The decision request optionally carries only the exact clip/artifact binding,
`listened: true`, and disposition. For a Kokoro per-role review the field is
required; elsewhere it is forbidden. The service derives actor, time,
immutability, rationale context, and the canonical attestation fingerprint,
then stores the typed record under
`AuditionReviewDecisionRow.provenance_json.details`.

Approval queries and Voice Readiness verify the current decision and exact
attestation against current clip and artifact fingerprints. A withdrawn or
invalidated decision is appended as immutable history; no review row is
deleted. Any later catalog, rights, assignment, cast snapshot, package,
pronunciation, clip, or artifact drift makes the old attestation unusable.
The schema-v5 decision column remains unchanged: an explicit `undecided`
listening disposition is stored inside the existing human
`changes_requested` non-approval decision envelope. The attestation, UI, and
restart projection retain `undecided` exactly, and readiness remains blocked;
it is never rewritten as `needs_changes` or approval.

Each clip projection carries the immutable per-role review bound to that exact
clip and only that review's latest decision. The service permits a new human
decision on a historical Kokoro per-role review only when the exact session,
activation, clip, artifact, review evidence, and hashes remain intact and no
newer review in that role already has a human decision. The new row must extend
the scope-wide supersession chain. Current review and Voice Readiness queries
still require exact current-review identity, so historical outcomes remain
auditable without regaining authority.

## Private listening package

The local real-product E2E writes an ignored package beneath an approved local
render root. It contains opaque WAV names, a redacted index, and a scorecard.
The index contains neutral profile labels, hashes, duration/format, role
purpose, rights state, and replay instructions, but no manuscript, audition
text, pronunciation value, model byte, tensor byte, database, credential, or
personal path. CI never receives this package.

After proving restart restoration and exact process exit, the gate moves the
isolated `APPDATA` and `LOCALAPPDATA` trees beneath the application-owned,
bounded `%LOCALAPPDATA%\CSS-P3B1\<opaque-id>` root and writes a fixed-data
PowerShell replay launcher into the redacted listening package. A 12-character
lowercase hexadecimal ID bounds path growth and cannot express traversal. The
generator rejects every retained path above 240 characters before mutation;
this prevents the replay move itself from making previously accessible
script, audio, or model files inaccessible to ordinary Windows APIs.

A private contract binds the listening-index SHA-256, package name, state ID,
state-sentinel SHA-256, application version, and exact size and SHA-256 of the
desktop executable, `resources/app.asar` product code, and embedded service
executable. The launcher verifies that contract,
the index and sentinel hashes, the canonical state root, isolated directories,
both executable fingerprints, and the executable working directory before
launch. Binding drift fails closed. No cleanup deletes a stale lock marker;
live ownership is determined by the OS lock and exact process identity, while
tests close only the proven Electron tree and verify its exact service PIDs are
gone. This keeps authenticated in-product replay possible without placing the
private database, model installation, audio, caches, or any personal path in
committed or hosted evidence.

## CI boundary

Hosted CI retains the two-launch deterministic fixture lifecycle. Phase 3B.1
adds synthetic metadata and explicit absent-package fail-closed assertions. A
public manifest must say that human listening is pending, real synthesis is not
claimed, and production export is false. The artifact inventory rejects model
and voice-data extensions and real generated audio.

See the [Phase 3B.1 known limitations](phase-3b1-known-limitations.md) for the
remaining human, rights, catalog-size, and release boundaries.
