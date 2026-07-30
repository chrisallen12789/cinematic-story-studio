# Runtime Story-Production Agent System

## Definition and constraints

Runtime production agents are versioned application components that transform approved project artifacts into typed proposed artifacts. They are not Codex engineering agents, background daemons with broad authority, or open-ended autonomous loops.

Agents:

- run only when selected by a persisted orchestration/job plan;
- receive immutable typed inputs and a cancellation/deadline context;
- access providers, storage, and tools only through scoped ports;
- produce schema-validated outputs, confidence, warnings, provenance, provider/model/cost metadata, and a declared human-review requirement;
- cannot approve their own work, bypass gates, mutate source, overwrite a correction, or publish a render directly.

## Common contracts

```text
AgentDefinition {
  id, version, purpose, inputSchemaVersion, outputSchemaVersion,
  requiredCapabilities[], retryPolicy, failurePolicy,
  defaultHumanReview, compatibility
}
AgentRunInput {
  runId, projectId, inputArtifactRefs/hashes, protectedDecisions[],
  configurationRef/hash, approvalRefs[], deadline, idempotencyKey
}
AgentRunOutput<T> {
  runId, status: succeeded|partial|failed|cancelled, output?: T,
  confidence?, warnings[], humanReview: required|recommended|not_required,
  provenance, provider/model?, cost?, checkpoint?
}
```

Confidence is a calibrated value in `[0,1]` with method/calibration status where meaningful; it is never a substitute for warnings or approval. `not_applicable` is used for deterministic validations. Warnings have code, severity, evidence/source spans, and whether review is required.

Every definition declares bounded retries by error class, checkpoint boundaries, cleanup, and whether failure blocks downstream work, permits partial review, or allows an explicitly selected fallback. Agent code cannot invent a retry loop.

## Agent registry

| Stable agent ID | Purpose | Accepted typed inputs | Typed output | Default review/gate |
| --- | --- | --- | --- | --- |
| `manuscript-ingest` | Validate/extract an immutable source without rewriting it | `SourceDocument`, importer config | `ImportedStory` plus extraction warnings | Required: import review |
| `story-structure` | Identify chapters, scenes, beats, locations, mood/action evidence | Approved `ImportedStory`, protected structure corrections | `Chapter[]`, `Scene[]`, `StoryBeat[]`, structure findings | Required: scene segmentation review |
| `character-dialogue` | Identify characters/dialogue and attribute speakers with uncertainty | Story/structure revisions, protected character/attribution corrections | `Character[]`, `DialogueLine[]`, `DialogueAttribution[]` | Required: character and dialogue-attribution reviews |
| `casting-director` | Propose voice profiles/assignments from approved character needs and available voices | Approved characters, provider voice descriptors, constraints | `VoiceProfile[]`, `CastingAssignment[]` | Required: casting approval |
| `performance-director` | Propose narration/dialogue performance direction | Approved story/attributions/cast, style constraints | `PerformanceDirection[]` | Required: performance-direction approval |
| `sound-designer` | Plan ambience, Foley, effects, and transitions | Approved scenes/direction, asset capabilities/policy | `AmbienceCue[]`, `FoleyCue[]`, effect/transition cues | Required: sound-design approval |
| `music-supervisor` | Propose restrained, rights-aware music cues | Approved structure/direction, music policy/assets | `MusicCue[]` plus rights/policy warnings | Included in sound-design approval unless configured separately |
| `continuity` | Detect story, voice, performance, timing, and sound continuity conflicts | All current approved/proposed production artifacts | `ContinuityRecord[]` | Blocking findings require relevant upstream re-review |
| `mix-master` | Compile an approved deterministic render plan and invoke render boundary | Approved timeline inputs, policies, verified assets | `ProductionTimeline`, `RenderManifest`, artifact refs/measurements | First-scene, chapter, and final-master approvals |
| `quality-control` | Evaluate structural/audio policy and manifest conformance | Manifest, artifacts, policies, provenance | `QualityControlFinding[]`, QC summary | Blocking findings prevent approval/publication |

Registry details and implementation locations live in `docs/agents/agent-registry.md`; this architecture is the controlling boundary.

Phase 2 expands story analysis into the controlled local subset documented in
`docs/agents/story-analysis-runtime-agents.md`. Its stable version-1 agents are
`story-structure`, `story-beats`, `character-identity`,
`dialogue-attribution`, `point-of-view`, `story-setting`,
`story-timeline`, `character-relationships`,
`emotion-dramatic-intent`, `story-continuity`, and
`analysis-synthesis`. They execute under this same envelope and do not create
an independent orchestration authority.

## Orchestration

The orchestrator builds a versioned directed acyclic plan from registry definitions:

```text
ingest
  -> story structure
  -> character/dialogue
  -> casting
  -> performance
  -> sound design + music
  -> continuity
  -> mix/master
  -> quality control
```

Independent nodes may run in parallel only when their declared inputs are frozen and publication does not conflict. Each node is a persisted job step with input hash, definition version, attempt, state, progress, checkpoint, and output artifact reference. Orchestration decisions are deterministic for the same plan inputs and registry version.

At a review gate, the orchestration job enters `blocked_for_approval` (represented as a durable orchestration step, not a worker thread held open). Approval pins an artifact ID/revision/hash and unblocks eligible successors. Rejection/changes requested creates an inspectable branch back to the owning step; no result is deleted.

## Approval gates

| Gate ID | Required artifact | Approval effect |
| --- | --- | --- |
| `import_review` | Source/import revision and extraction warnings | Permits story analysis. |
| `story_structure_review` | Chapter, scene, beat, and POV evidence fingerprint | Reviews structure independently; it does not approve another Phase 2 gate. |
| `character_registry_review` | Character, mention, and relationship evidence fingerprint | Reviews registry identity independently; casting remains unavailable in Phase 2. |
| `dialogue_attribution_review` | Attribution set revision and unresolved warnings | Pins speaker decisions for performance. |
| `whole_book_analysis_review` | Complete Phase 2 snapshot, remaining interpretive warnings, timeline/relationship/emotion/continuity evidence | Marks the current story-intelligence snapshot eligible for later production planning; it does not authorize casting or audio in Phase 2. |
| `casting_approval` | Casting assignment revision and provider/voice identities | Permits provider speech requests. |
| `performance_direction_approval` | Direction revision | Permits speech generation/render planning. |
| `sound_design_approval` | Sound/music cue revisions and rights warnings | Permits cue acquisition/render planning. |
| `first_scene_render_approval` | First-scene manifest/artifact/QC revisions | Permits broader chapter rendering. |
| `chapter_approval` | Chapter manifest/artifact/QC revisions | Permits inclusion in final master. |
| `final_master_approval` | Final manifest/artifact/QC revisions | Marks master approved for export; never overwrites it. |

Decisions are append-only and identify actor, artifact revision/hash, presented warnings/disclosures, reason, and timestamp. Revocation is another decision and blocks later work that depended on the approval. Approval of revision N never implicitly approves N+1.

The first three Phase 2 gates are independently reviewable. All three are
prerequisites only for `whole_book_analysis_review`. Historical terms such as
“scene segmentation review” and “character review” are conceptual aliases, not
persisted Phase 2 gate IDs.

## Human corrections and conflict handling

Protected decisions are passed into every relevant agent input. An agent must:

1. preserve the protected value;
2. report contradictory evidence as a warning with source spans;
3. optionally propose an alternative separately;
4. require explicit human supersession to change it.

If source/project revisions change while a run executes, publication detects the mismatch and marks output stale/branchable; it never replaces the current projection. Human and automated artifacts remain traceable to their exact base revisions.

## Provider and policy control

An agent requests a capability; the orchestrator/provider registry resolves an allowed adapter only after local/cloud policy, credentials, disclosure, cost, and availability checks. The chosen provider/model/voice/version and metering are output provenance. Cloud absence blocks only nodes that truly require it; deterministic/local alternatives remain usable when declared.

Agent prompts/templates, parsing logic, schemas, and model settings are versioned. Provider output is untrusted: validate schema, length, references, source spans, enum values, hallucinated entities, and unsafe content before it becomes a proposed artifact. Source text in prompts/responses remains private project data and never enters general logs.

## Reproducibility and audit

For every run retain definition/version, input refs/hashes, protected decisions, configuration/template hash, provider/model identity, parameters/seed, output hash, warnings/confidence, attempts/errors, cost metadata, approvals, and timestamps. Canonical outputs use stable ordering.

Agents may be non-deterministic; the system records that fact and freezes their returned validated artifact. Replaying a later pipeline step uses the frozen artifact unless the user explicitly requests regeneration.
