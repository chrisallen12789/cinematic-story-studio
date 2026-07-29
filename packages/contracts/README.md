# Shared contracts

This package is the dependency-free TypeScript view of the version 1 contracts
in `../../schemas/v1`. It contains data only: no storage, provider, networking,
or UI behavior belongs here.

Persisted data must be validated against the JSON Schemas at service boundaries.
TypeScript types do not replace runtime validation. Schema-breaking changes
require a new schema directory and a deliberate migration.

`HumanCorrection` and `ApprovalDecision` values are immutable events. Append a
new superseding event; do not mutate or delete the earlier event. Speaker
correction requests carry an expected revision so the service can reject stale
writes.
