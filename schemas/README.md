# Contract schemas

`v1/definitions.schema.json` is the canonical JSON Schema 2020-12 bundle for the
version 1 domain. Each `*.schema.json` beside it is a stable entry point for one
public entity or runtime contract.

Schema versions are part of persisted data. A breaking change requires a new
version directory and an explicit migration; changing a version 1 meaning in
place is not permitted. Additive changes must remain compatible with stored
version 1 documents.

Human corrections and approval decisions are immutable event records. Storage
code must append a superseding human record rather than update or delete an
earlier record. Runtime agents may propose new analysis, but must not supersede
or change a human-authoritative value.

Run the dependency-free structural checks from the repository root:

```text
node --test schemas/tests/schema-structure.test.mjs
```

Full instance validation should be performed at every service boundary with a
JSON Schema 2020-12 validator selected by the owning application package.
