# Character relationship model

Relationships are directed, versioned, evidence-backed edges between stable
character IDs. The controlled labels are `family`, `friendship`, `romantic`,
`professional`, `adversarial`, `authority`, `dependency`, `alliance`,
`unknown`, and `custom`. An edge records its source range, scene or chapter
scope, change over story time, confidence, warnings, and provenance.

Co-occurrence alone is not relationship evidence. Duplicate-name or unresolved
character endpoints prevent an authoritative edge. Changes such as distrust
becoming alliance are represented by separate scoped evidence rather than by
overwriting the earlier state.

Humans may correct the label, direction, scope, or effective endpoints through
the immutable correction overlay. Historical machine and human states remain
queryable.
