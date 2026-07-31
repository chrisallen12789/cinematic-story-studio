# Runtime agent registry

## Scope and control model

These agents are versioned application components in the story-production
runtime. They are not Codex engineering agents and they do not communicate or
act outside the local orchestrator. The orchestrator supplies immutable input
snapshots, validates output, persists provenance, and advances work only through
the approval gates in [approval-gates.md](approval-gates.md).

The application must launch and remain usable with every cloud provider
disabled. A registry entry names a capability, not a vendor. Provider adapters
may use deterministic code, a local model, or an explicitly authorized cloud
model.

## Required execution contract

Every registry entry is a `RuntimeAgentDefinition`; every attempt produces an
`AgentExecutionEnvelope` and a typed output, including failed and cancelled
attempts. The version 1 schemas are in `schemas/v1`, and the TypeScript views are
in `packages/contracts`.

Each execution envelope requires:

- `agentId`, semantic `agentVersion`, `purpose`, immutable accepted-input
  snapshots, and `outputSchemaRef`;
- status, attempt number, start/end times, confidence with its stated basis,
  structured redacted warnings, and explicit human-review requirements;
- retry and failure policies copied from the definition used for that attempt;
- provenance with input fingerprint and source entity revisions;
- provider/model/version, local-or-cloud execution location, and a
  configuration fingerprint; and
- cost metadata. Local or deterministic work records known zero cost. Unknown
  provider cost is represented as unknown, never omitted.

Output is a proposal until validated and promoted by the orchestrator. Agents
cannot write canonical records directly. A warning that requires review blocks
the next dependent gate. Unknown dialogue speakers remain `null`; uncertainty
must not be converted into invented certainty.

## Registry

All initial versions are `1.0.0`.

| Agent (`id`) | Purpose | Accepted version 1 inputs | Typed output | Human review and gate | Retry / failure | Default provider capability |
|---|---|---|---|---|---|---|
| Manuscript Ingest (`manuscript-ingest`) | Validate a supported source, preserve it exactly, and create import records. | `Project`, import request bytes and digest | `ManuscriptIngestOutput` (`SourceDocument[]`, `ImportedStory`) | Always: import review | One attempt; fail fast, retain the untouched source and redacted failure record | Deterministic local importer; extraction adapter for DOCX/EPUB/PDF |
| Story Structure (`story-structure`) | Propose chapter, scene, and beat boundaries without rewriting text. | `ImportedStory`, `SourceDocument[]` | `StoryStructureOutput` (`Chapter[]`, `Scene[]`, `StoryBeat[]`) | Always: scene segmentation review | Two attempts only for transient adapter errors; preserve partial proposals and require review | Deterministic local parser first; optional language-analysis adapter |
| Character and Dialogue (`character-dialogue`) | Propose characters, verbatim dialogue lines, and evidence-backed speakers. | `ImportedStory`, approved `Chapter[]`, `Scene[]`, `StoryBeat[]` | `CharacterDialogueOutput` (`Character[]`, `DialogueLine[]`, `DialogueAttribution[]`) | Character and dialogue-attribution reviews; every unresolved or low-confidence attribution is blocking | Two attempts for transient failures; preserve partial results, leave unknown speaker `null` | Local analysis capability by default; optional language provider |
| Casting Director (`casting-director`) | Propose a licensed voice profile and stable casting assignment for each character. | Approved `Character[]`, available `VoiceProfile[]`, active `ContinuityRecord[]` | `CastingDirectorOutput` (`VoiceProfile[]`, `CastingAssignment[]`) | Always: casting approval | Two catalog/provider attempts; preserve proposals, never substitute an unapproved voice | Local voice catalog; optional speech-provider catalog |
| Performance Director (`performance-director`) | Propose delivery, emotion, pacing, pauses, and pronunciation direction. | Approved dialogue attribution and casting; scenes and continuity | `PerformanceDirectorOutput` (`PerformanceDirection[]`) | Always: performance-direction approval | Two transient attempts; preserve partial directions, block synthesis for missing required direction | Local rules or optional language provider |
| Sound Designer (`sound-designer`) | Propose deterministic ambience and Foley cues that support, not obscure, story. | Approved scenes/performance direction, continuity, asset catalog | `SoundDesignerOutput` (`AmbienceCue[]`, `FoleyCue[]`) | Always: sound-design approval | Two transient attempts; skip unavailable optional assets with warning, fail required cues to review | Local rules/library first; optional sound-generation provider |
| Music Supervisor (`music-supervisor`) | Propose restrained music cues with explicit rights status. | Approved scenes/performance direction, continuity, asset catalog | `MusicSupervisorOutput` (`MusicCue[]`) | Included in sound-design approval; unknown or restricted rights always block | Two transient attempts; omit music rather than use an unverified asset | Local licensed library first; optional music provider |
| Continuity (`continuity`) | Detect conflicts and propose stable voice, pronunciation, performance, and sound records. | Current approved production entities and prior `ContinuityRecord[]` | `ContinuityOutput` (`ContinuityRecord[]`) | Any conflict with a human-authoritative record requires review at the affected gate | One deterministic pass; preserve all conflicts, never resolve a human lock automatically | Deterministic local comparison; optional semantic matcher |
| Mix and Master (`mix-master`) | Build the deterministic timeline, render approved scope, and record all inputs. | Approved cues/direction/casting, `ProductionTimeline`, approval decisions | `MixMasterOutput` (`ProductionTimeline`, `RenderJob`, optional `RenderManifest`) | First-scene, chapter, and final-master gates according to render scope | Three attempts for transient provider/process failures; resume checkpoints only when input fingerprint matches; fail closed on missing clips | Local FFmpeg renderer; speech providers only through approved adapters |
| Quality Control (`quality-control`) | Measure technical output and report continuity or approval defects. | `RenderManifest`, rendered-asset measurements, approvals, continuity | `QualityControlOutput` (`QualityControlFinding[]`) | Blockers and errors require review; final master requires zero open blockers | One complete measurement pass; an unavailable required measurement is a blocker, not a pass | Deterministic local measurement tools |

The composite output names above are exported by
`@cinematic-story-studio/contracts/runtime-agent`; their members are validated
against the public entity schemas.

## Phase 2 whole-book analysis subset

The Phase 2 registry refines the earlier composite structure and
character-dialogue concepts into eleven bounded local executions. The
controlling IDs, outputs, retry/cancellation policy, and gate impact are in
[story-analysis-runtime-agents.md](story-analysis-runtime-agents.md). This is
an additive refinement of the existing governed model: no refined agent gains
direct database/provider/tool authority, and later casting/audio agents remain
disabled and out of scope.

## Confidence and warning rules

Confidence is about the proposed output, not provider availability. Each score
includes a basis and may include per-field JSON-pointer scores. Scores below
`0.75` for boundaries, character identity, or dialogue attribution require a
warning and human review. A score never authorizes an approval.

Warnings use stable codes, severity, a redacted message, related entity
references, and `requiresHumanReview`. Warning text must not contain manuscript
passages, credentials, local paths, or provider payloads.

## Durable corrections and reproducibility

`HumanCorrection` records are append-only, immutable, and locked against
automation. A later human may append a superseding correction; an agent may only
make a separate proposal. Storage must reject an automated update when the
effective field is human-authoritative.

Retries reuse the same immutable input snapshot and configuration fingerprint.
Changed inputs create a new execution, not another attempt. Stable ordering,
provider/model versions, seeds, costs, warnings, and all execution ids become
part of the render provenance.
