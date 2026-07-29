export const SCHEMA_REFS = {
  Project:
    "https://schemas.cinematic-story-studio.dev/v1/project.schema.json",
  ImportedStory:
    "https://schemas.cinematic-story-studio.dev/v1/imported-story.schema.json",
  SourceDocument:
    "https://schemas.cinematic-story-studio.dev/v1/source-document.schema.json",
  Chapter:
    "https://schemas.cinematic-story-studio.dev/v1/chapter.schema.json",
  Scene: "https://schemas.cinematic-story-studio.dev/v1/scene.schema.json",
  StoryBeat:
    "https://schemas.cinematic-story-studio.dev/v1/story-beat.schema.json",
  Character:
    "https://schemas.cinematic-story-studio.dev/v1/character.schema.json",
  DialogueLine:
    "https://schemas.cinematic-story-studio.dev/v1/dialogue-line.schema.json",
  DialogueAttribution:
    "https://schemas.cinematic-story-studio.dev/v1/dialogue-attribution.schema.json",
  VoiceProfile:
    "https://schemas.cinematic-story-studio.dev/v1/voice-profile.schema.json",
  CastingAssignment:
    "https://schemas.cinematic-story-studio.dev/v1/casting-assignment.schema.json",
  PerformanceDirection:
    "https://schemas.cinematic-story-studio.dev/v1/performance-direction.schema.json",
  AmbienceCue:
    "https://schemas.cinematic-story-studio.dev/v1/ambience-cue.schema.json",
  FoleyCue:
    "https://schemas.cinematic-story-studio.dev/v1/foley-cue.schema.json",
  MusicCue:
    "https://schemas.cinematic-story-studio.dev/v1/music-cue.schema.json",
  ProductionTimeline:
    "https://schemas.cinematic-story-studio.dev/v1/production-timeline.schema.json",
  ContinuityRecord:
    "https://schemas.cinematic-story-studio.dev/v1/continuity-record.schema.json",
  RenderJob:
    "https://schemas.cinematic-story-studio.dev/v1/render-job.schema.json",
  RenderManifest:
    "https://schemas.cinematic-story-studio.dev/v1/render-manifest.schema.json",
  QualityControlFinding:
    "https://schemas.cinematic-story-studio.dev/v1/quality-control-finding.schema.json",
  ApprovalDecision:
    "https://schemas.cinematic-story-studio.dev/v1/approval-decision.schema.json",
  RuntimeAgentDefinition:
    "https://schemas.cinematic-story-studio.dev/v1/runtime-agent-definition.schema.json",
  AgentExecutionEnvelope:
    "https://schemas.cinematic-story-studio.dev/v1/agent-execution-envelope.schema.json"
} as const;

export type PublicSchemaName = keyof typeof SCHEMA_REFS;
