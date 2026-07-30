# Story-analysis data model

## Identity and lineage

The Phase 2 graph is rooted at one `StoryAnalysisRun` and one or more immutable
`AnalysisSnapshot` revisions. Run-owned records carry project/run lineage,
frozen input identity, creation time, and supersession information where
applicable. Generated claims additionally carry producer/agent identity and
version and reference one or more `EvidenceSpan` records plus a
`ConfidenceAssessment`. Human corrections and review decisions instead carry
human actor and typed correction/review provenance; they do not claim an agent
identity.

Stable IDs are derived only from frozen source identity, typed entity kind,
canonical offsets, and other declared identity inputs. Display labels,
timestamps, database row order, and mutable human notes do not define machine
entity identity.

## Entity families

| Family | Principal records | Required invariants |
| --- | --- | --- |
| Run | analysis profile, run, agent execution, snapshot | Frozen input references and fingerprints; append-only attempts; only a complete validated run publishes a snapshot |
| Structure | structural unit, chapter, scene, beat | Ordered half-open spans inside approved canonical text; explicit parent/ordinal; machine and human boundaries remain distinguishable |
| Character | identity, alias claim, mention | Stable identity independent of label; duplicate names permitted; alias/merge/split proposals retain evidence |
| Dialogue | line, attribution candidate, effective attribution | Verbatim source slice/hash; bounded candidates; nullable effective speaker; human authority protected |
| View and setting | POV segment, location, scene-location assignment | Evidence-backed ranges; unknown and mixed states explicit; no label-only location merge |
| Time | event, temporal constraint | Narrative order separate from story order; approximate, contradictory, and unknown relations representable |
| Relationship | directed relationship edge and revisions | Evidence and scope required; co-occurrence alone insufficient |
| Performance intelligence | emotional state, dramatic intent | Controlled vocabulary; interpretation separated from evidence; bounded notes |
| Continuity | finding, related-entity links, disposition | Non-destructive; machine finding and human disposition both retained |
| Governance | human correction, review gate, decision | Append-only; expected revision/fingerprint; selective supersession/invalidation |

## Evidence spans

An evidence span contains:

- source-document and extraction identity;
- extraction revision and SHA-256 of the exact half-open spanned text;
- half-open Unicode code-point `start` and `end`;
- evidence role and stable fingerprint; and
- an optional authenticated excerpt projection bounded to 512 code points.

The stored claim does not duplicate the manuscript passage. An excerpt is
derived at read time from the exact approved text, bounded, and never logged.
Phase 1 source mappings are resolved separately and are not serialized on an
`AnalysisEvidenceSpan`.
Reversed, out-of-range, different-revision, or hash-mismatched spans are
rejected.

## Confidence

Every interpretive generated claim has a score in `[0,1]`, a classification,
basis/rule ID, calibration/profile identity, warnings, and whether review is
required. The classifications and thresholds are controlled by
[confidence-and-evidence-policy.md](confidence-and-evidence-policy.md).
Confidence does not authorize a gate.

## Corrections and effective views

Machine rows are immutable. A `HumanAnalysisCorrection` identifies the target
entity/revision and field, previous-value fingerprint, typed corrected value,
actor, reason, time, provenance, and optional superseded correction. Effective
queries overlay the newest applicable human correction in stable order and
return both the machine proposal and effective authority.

Corrections never edit the approved extraction. A correction whose expected
snapshot, target revision, or previous-value fingerprint is stale returns a
conflict and leaves history unchanged.

## Gate evidence

Each `AnalysisReviewDecision` pins the fingerprint of a canonical enumeration
of affected entities/revisions, warning acknowledgements, snapshot revision,
and evidence fingerprint. Historical decisions remain intact when current
evidence changes. Effective gate state is derived by comparing the current
candidate fingerprint with the latest decision for that exact fingerprint.

## Relational rules

Schema v3 uses foreign keys, uniqueness and check constraints, explicit
ordinals, and indexes beginning with `project_id` and `analysis_run_id` for
project/run-scoped queries. Large collection endpoints use stable
`ordinal,id` or `created_at,id` ordering and opaque cursors. The database stores
canonical typed JSON only for bounded values whose schema is independently
validated; it does not store arbitrary agent output blobs as effective data.

The migration and complete compatibility checks are specified in
[database migration 0003](../migrations/0003-phase-2-story-intelligence.md).
