# Continuity finding model

Continuity findings are evidence-backed, non-destructive review records. The
governed categories are possible duplicate character, identity contradiction,
alias conflict, dialogue-speaker conflict, chronology conflict, location
conflict, attribute conflict, unexplained object-state change, unexplained
character-state change, POV discontinuity, scene-boundary uncertainty,
unresolved reference, extraction uncertainty, and `other`.

Every finding records category, severity, confidence, evidence on each side,
related entities, bounded explanation, suggested review action, status,
producer/version, warnings, and provenance. Absence-based findings record the
bounded scope and rule searched instead of fabricating an evidence passage.

Human dispositions are `confirmed_issue`, `intentional`, `false_positive`,
`deferred`, `corrected`, and `unresolved`. A disposition is an immutable
correction overlay and survives restart and compatible reruns. It does not
delete the machine finding.
