# Confidence and evidence policy

## Classification

Phase 2 uses one exact confidence classification:

| Classification | Score |
| --- | ---: |
| `unknown` | exactly `0` |
| `low` | greater than `0` and less than `0.75` |
| `medium` | at least `0.75` and less than `0.85` |
| `high` | at least `0.85` and at most `1` |

The score is a rule-specific assessment of evidentiary support, not a
statistical guarantee. The record also includes a basis/rule ID, analysis
profile and calibration identity, review requirement, and warnings. A score
cannot substitute for evidence, resolve a contradiction, or approve a gate.

## Evidence requirements

Every generated claim must reference at least one valid evidence span unless
its type is explicitly a whole-run validation or absence finding. An
absence-based finding references the bounded scope searched, the deterministic
rule, and the input fingerprint instead of inventing a source passage.

Evidence must:

- resolve into the exact approved canonical text;
- match source/extraction identity, revision, and text SHA-256;
- use half-open Unicode code-point offsets;
- describe its role, such as explicit tag, heading, mention, temporal phrase,
  action beat, or conflicting fact; and
- remain within the profile's span and excerpt limits.

Claims with conflicting evidence preserve each side. Low or unknown dialogue,
identity, structural boundary, POV, relationship, temporal, or continuity
results require human review.

## Interpretive language

Machine output and UI copy use proposal language: “evidence suggests,”
“candidate,” “possible,” “uncertain,” or “unresolved” as applicable. Emotional
state, dramatic intent, subtext, relationship, POV character, and continuity
interpretations are not presented as objective facts.

No field intended for verbatim text may contain a summary. Dialogue and
narration text is always sliced from approved canonical text and verified by
hash. Bounded explanatory notes are stored separately and cannot replace
source text.

## Human authority

A human correction may accept a value, replace it, preserve it as unknown, or
lock it. The machine proposal and its confidence remain inspectable. Later
agents receive protected corrections as inputs and may emit contradictory
evidence as a warning, but may not silently replace the effective value.

## Logging and diagnostics

Logs, job events, metrics, CI output, and build evidence may contain only
opaque IDs, counts, hashes, versions, classifications, rule IDs, states,
durations, and stable redacted codes. They never contain evidence excerpts,
manuscript text, human correction values copied from the source, or personal
paths. Packaged proof records only the SHA-256 of each fixed repository-owned
synthetic correction reason and cross-links gate hashes to the tested profile,
run, and snapshot; it does not serialize the reason or correction patch.
