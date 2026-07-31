# Shared contracts

This package is the dependency-free TypeScript view of the version 1 ingest
and production contracts in `../../schemas/v1`, the Phase 2 whole-book
story-intelligence contracts in `../../schemas/v2`, and the Phase 3A governed
voice-casting contracts in `../../schemas/v3`. It contains data only: no
storage, provider, networking, synthesis, or UI behavior belongs here.

Persisted data must be validated against the JSON Schemas at service boundaries.
TypeScript types do not replace runtime validation. Schema-breaking changes
require a new schema directory and a deliberate migration.

`HumanCorrection` and `ApprovalDecision` values are immutable events. Append a
new superseding event; do not mutate or delete the earlier event. Speaker
correction requests carry an expected revision so the service can reject stale
writes.

Phase 2 freezes the approved source, extraction, Import Review, story,
analysis profile, producer, and runtime-agent versions into each analysis run.
Generated entities preserve machine and effective fingerprints, bounded exact
evidence, confidence, warnings, and provenance. Specialized IDs such as
`characterId`, `sceneId`, and `dialogueLineId` must equal the common
`entityId`; this cross-field equality is enforced by boundary validators
because TypeScript and JSON Schema cannot compare two arbitrary values.

`AnalysisCorrectionSelection` is the authoritative persisted
category-to-collection-to-patch mapping.
`AnalysisCorrectionRequestSelection` is its request counterpart; structure
boundary requests submit only the frozen source identity and selected offsets,
and the service derives the canonical span digest transactionally. Their
closed patch types prohibit generic field mutation and keep human authority
append-only. Phase 2 correction requests and persisted corrections require a
nonblank, bounded human reason. Cursor endpoints bind
every opaque cursor to the
project, run, snapshot, collection, and normalized filter fingerprint.
Dialogue rows carry a closed speaker state that must agree with effective
attribution. Every Phase 2 gate action requires a nonblank bounded rationale,
and review reads return the complete immutable latest human decision or an
explicit null so restart and supersession can be verified.

Phase 3A is additive and does not redefine the version 1 `VoiceProfile` or
`CastingAssignment`. `CastingVoiceProfile`, `VoiceProviderDescriptor`,
`VoiceModelDescriptor`, `VoiceCatalogRevision`, and `VoiceRightsRecord`
describe provider-neutral catalog metadata without credentials, manuscript
content, audio, or a person likeness. `ProductionRole`, `CastingRun`,
`CastingCandidate`, `CastingConflict`, `CastAssignment`,
`CastingCorrection`, and the three casting review contracts freeze all Phase 2
preconditions and make machine proposals distinct from durable human
selection and locks.

For derived character roles, `characterId` is the stable effective Character
Registry identity and is distinct from the analysis `phase2EntityId`.
`roleImportance` and language/locale/performance requirements project approved
evidence conservatively; absent or conflicting evidence is not inferred.
Candidate `conflictWarnings` is a bounded projection of the candidate's actual
persisted conflict rows.
Candidate, compatibility-assessment, and conflict DTOs expose immutable
`baseEvidenceFingerprint` separately from `outputFingerprint`.
`outputFingerprint` binds the exact public DTO projection (excluding that
fingerprint field), so rejection and conflict-disposition projections cannot
silently reuse a machine-evidence digest that covered a different shape.

Casting review decisions have a closed authority pairing: human actors may
author `approved|changes_requested|rejected`, while system actors may author
only `invalidated`. Durable assignment invalidation remains effective across a
catalog reversion and requires explicit human reselection and reapproval; the
system cannot grant approval.

The governed profile is `governed-voice-casting-v1@1.0.0`, produced by
`voice-casting-orchestrator@1.0.0`, and uses `voice-rights-policy-v1`. Its
SHA-256 is pinned in `voice-casting.ts`. Catalog and role pages default to 50
records and reject limits above 200. Runs accept at most 300 roles and 5,000
voice profiles, evaluate at most 50 pre-reduction candidates per role, publish
at most 12 final candidates per role, and bound explanations to 2,000 Unicode
code points. More than two assigned roles per voice profile triggers the
configured reuse conflict. Unknown and prohibited rights are ineligible for final approval;
restricted rights require a human acknowledgement. Conflict similarity is
metadata-only and never claims acoustic analysis.
