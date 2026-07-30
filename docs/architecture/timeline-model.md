# Timeline and temporal-constraint model

Narrative order and story-world order are separate. Timeline events preserve
their source order. Temporal relations are `before`, `after`, `same_time`,
`overlaps`, `during`, `contains`, or `unknown`; `approximate` is a separate
boolean and contradiction is represented by `status: conflicting`. Flashbacks,
flash-forwards, explicit dates/times, relative phrases, elapsed-time claims,
and returns to the narrative present remain typed evidence.

The baseline never invents an exact date or ordinal merely to complete a
chronology. Unsupported ordering remains unknown; inconsistent constraints
remain present with both sides of their evidence. Each event and constraint
records source range, producer/version, confidence, warnings, and provenance.

Human corrections annotate or resolve ordering through an append-only overlay.
They do not rewrite the event or source text and cannot erase contradictory
machine evidence.
