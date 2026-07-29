# Cinematic Story Service

The authoritative, loopback-only FastAPI backend for Cinematic Story Studio.

Development installs expose the production launcher:

```text
python -m cinematic_story_service.launcher --data-dir <private-app-data-directory>
```

The launcher accepts its one-time bearer token and nonce on standard input. It never accepts
credentials through arguments or environment variables.

Phase 0 also includes deterministic PCM inspection, cue-layout, loudness-policy, and bounded
provider-recovery contract helpers. These are a test seam only; the full synthesis, mixing,
mastering, and atomic audio-render publication pipeline remains deferred.
