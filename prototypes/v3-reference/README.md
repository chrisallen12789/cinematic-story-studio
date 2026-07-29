# NOT PRODUCTION ARCHITECTURE — REFERENCE IMPLEMENTATION ONLY

This directory is a quarantined, non-executable record of two configuration shapes
observed in the Cinematic Story Studio v3 prototype. It is not an application,
runtime configuration directory, supported provider list, or architecture proposal.

Nothing here may be loaded by production code. The examples exist only to help
identify historical settings that may need an explicit, typed migration.

## Sanitization

- All credential, token, account-identifier, and API-key values are empty.
- Story-specific voice instructions were removed, and the character-specific key
  was generalized.
- No manuscript text, project state, cover art, audio, cache, model, generated
  output, compiled bytecode, personal path, or prototype source code was copied.
- Provider names, models, versions, and defaults are historical observations only.
  They are not assertions of current support or safe defaults.

Production credentials must use operating-system-backed secure storage. Production
provider endpoints must be typed and allowlisted; they must not be accepted from
these examples as arbitrary runtime URLs.
