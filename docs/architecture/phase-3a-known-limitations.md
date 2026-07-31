# Phase 3A known limitations

Phase 3A is a governed casting-planning baseline. It is not an audition,
speech, legal-clearance, or production-readiness claim.

- The only enabled catalog is the deterministic repository-owned synthetic
  fixture. Its two providers, five models, fourteen profiles, and rights
  records exercise metadata and governance; none is a real or executable voice
  service.
- No synthesis interface is invoked. There is no audio sample, audition,
  playback, waveform, embedding, model download, cloud request, credential
  flow, or provider account integration.
- Compatibility uses declared metadata and deterministic rules. It can miss
  artistic, cultural, linguistic, narrative, or performance considerations.
  Scores are not calibrated artistic probabilities and never assign a voice.
- Metadata similarity is not acoustic similarity. The system can flag shared
  locale/presentation/texture/pitch groups but cannot establish whether two
  voices sound alike or sufficiently distinct.
- Profile fields such as age presentation, accent/dialect, vocal presentation,
  and texture are catalog declarations, not inferred attributes or facts about
  a performer. The product does not verify identity, likeness, or authenticity.
- `verified` rights means the recorded evidence was verified under the current
  policy. The service does not provide legal advice, continuously monitor
  provider terms, validate a license document, resolve jurisdictional
  questions, or guarantee commercial/distribution permission.
- Restricted-rights acknowledgement records a human decision against exact
  evidence. It does not waive, amend, or satisfy the restriction itself.
  Unknown and prohibited rights remain ineligible for final approval.
- Automatic role generation is a deterministic projection of approved Phase 2
  collections and cannot infer unsupported narrator shifts, crowd speech,
  quoted material, or internal thought. A human can append an explicit custom
  role to a succeeded casting run, but that role is scoped to that run, has no
  Phase 2 entity or implicit manuscript source, and intentionally records zero
  dialogue/narration/word workload with null chapter/scene ranges. It does not
  backfill manuscript workload or change the approved Phase 2 snapshot.
- Character-role identity is the stable effective Phase 2 Character Registry
  identity. Role importance and language/locale/performance requirements are
  projected only from approved evidence. Missing or conflicting evidence stays
  conservatively `supporting`, `und`, empty, or null; Phase 3A does not infer it
  from a name or manuscript prose.
- Approximate workload is planning metadata. It is not a duration, performance
  time, billing, or pronunciation estimate.
- Catalog/profile changes are immutable revisions. Affected selected evidence
  creates durable assignment invalidation and dependent system decisions that
  remain invalid after catalog reversion, but the application does not
  automatically remap an assignment to a “similar” replacement voice.
- Correction carry-forward is allowed only when stable role and evidence
  fingerprints remain compatible. Changed source, Phase 2 snapshot, catalog,
  or role identity can require explicit recasting.
- The version-1 durable checkpoint covers the validated bounded casting result.
  Phase 3A does not claim per-role resume from an arbitrary instruction inside
  each compatibility loop.
- Scale validation covers the governed ceilings of 300 roles, 5,000 profiles,
  50 pre-reduction candidates, 12 final candidates per role, 10,000 conflicts,
  200 immutable corrections per run, and bounded
  pagination. It is a regression boundary, not a universal wall-clock, memory,
  or hardware SLA.
- Phase 3A list repositories materialize their explicitly capped ordered
  collection inside the local service before slicing a page. Cursors bind the
  exact row-ID set and fail stale after mutation, but runtime keyset SQL is not
  implemented or claimed. The migration tests' EXPLAIN results prove index
  structure only.
- SQLite content is protected by Windows account/device controls but is not
  application-encrypted. General diagnostics are redacted; a compromised local
  account, administrator, kernel, or process memory is outside the Phase 3A
  threat boundary.
- There is no in-place schema-v4-to-v3 downgrade. Recovery uses a separately
  verified v3 backup copy with a matching older application.
- The unpacked CI application is verification evidence, not a signed installer
  or public release. No tag, release, update feed, or production signing is
  created.

Explicitly out of scope are speech synthesis, audition audio, voice cloning,
real provider/model acquisition, cloud transmission, cloud credentials,
Windows Credential Manager, playback, waveforms, clip rendering,
pronunciation dictionaries, line-level performance direction, music, Foley,
ambience, mixing, audio QC, WAV/MP3/M4B export, installer signing, automatic
updates, public release, payments, marketplaces, Phase 3B, and Phase 4.
