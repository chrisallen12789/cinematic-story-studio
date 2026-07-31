# Phase 2 known limitations

Phase 2 is a governed deterministic baseline, not a claim of human-level
literary understanding or production readiness.

- Rule-based analysis can miss implicit identity, irony, subtext, unmarked
  dialogue, experimental structure, or chronology that lacks explicit textual
  signals. Unknown, ambiguous, contradictory, and low-confidence results remain
  reviewable instead of being forced.
- The initial service accepts at most 150,000 words and 250,000 persisted
  analysis entities per run. Profile page, evidence, candidate, warning, and
  exact-text limits are documented in
  [analysis-performance-and-pagination.md](analysis-performance-and-pagination.md).
- No cloud analysis, cloud speech provider, local model download, or semantic
  enrichment is enabled. Provider-neutral future boundaries do not establish a
  working provider capability.
- Approved TXT, Markdown, DOCX, EPUB, and text-based PDF extraction remains the
  input boundary. OCR, image-only manuscripts, and source rewriting remain out
  of scope.
- A verified intermediate structure-stage artifact can resume after structure,
  and a verified full analysis checkpoint can resume publication. Work
  interrupted before the structure artifact, or during a later stage whose
  output is not yet checkpointed, reruns the remaining deterministic bounded
  analysis; Phase 2 does not claim per-entity resume inside every stage.
- Human corrections are overlays. A correction that no longer resolves to the
  same stable target/evidence requires explicit remapping on a later run.
- Character registry IDs and correction carry-forward are stable only for
  exactly compatible project-story inputs. A changed source or extraction
  creates new machine proposals and requires explicit identity/correction
  remapping; Phase 2 does not claim automatic cross-revision registry matching.
- There is no in-place schema-v3-to-v2 downgrade. Recovery uses a separately
  verified pre-migration v2 backup with a matching older application.
- Phase 2 itself does not implement casting, voice selection, pronunciation
  production, speech synthesis, ambience/Foley/music, mixing, audio QC, export,
  installer signing, updates, or releases. Phase 3A may consume an approved
  Phase 2 snapshot only through a separate governed contract; its limitations
  are documented in
  [Phase 3A known limitations](phase-3a-known-limitations.md).
