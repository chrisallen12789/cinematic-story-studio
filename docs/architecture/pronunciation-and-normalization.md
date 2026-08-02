# Pronunciation and normalization

Pronunciation entries are append-only revisions with typed `ipa`,
`provider_neutral`, or `provider_specific` values. Every revision records the
written and normalized lookup forms, language/optional locale, scope and scope
ID, case-sensitivity, whole-word/phrase rule, priority, human actor, reason,
verification state, provenance, fingerprint, and a stored predecessor
supersession link; bounded API projections derive the successor link.
Provider-specific entries require an exact provider ID and bounded compiled
value, apply only to that provider, and receive the same control/markup
validation as the provider-neutral value. Raw SSML, provider control markup,
forbidden control characters, and ambiguous ties are rejected. Human review decisions can approve,
reject, or request changes; a human-created entry can supersede an earlier
entry. Automated code never approves an entry.

The authoritative precedence is:

1. exact scene;
2. exact chapter;
3. explicit custom scope;
4. narrator or character role;
5. project.

Within a scope, exact locale beats language fallback, then explicit priority. Equal winners fail
closed. A compiled plan records exact source spans plus entry IDs and revisions.

Two fingerprints intentionally coexist:

- the global dictionary revision/fingerprint records the complete request-time provenance and
  detects an in-flight stale request;
- the effective pronunciation-plan fingerprint includes only entries applied to this role and
  text. It drives cache reuse and targeted invalidation.

Therefore changing an unrelated entry changes the global dictionary evidence but does not revoke
an unaffected clip or approval. Changing an applicable entry preserves the old clip as historical
evidence, invalidates dependent cache/current-review evidence, and requires a new artifact and
human decision.

Normalization is also reviewable. Persisted edit evidence records source and
destination spans, original/replacement SHA-256 values, reason, provider-
requirement and human-approval flags, and the decision state; it does not retain
the edit text in the transformation JSON. The authenticated bounded preview can
reconstruct and expose original/replacement text for inspection. Transport-safe
changes such as line endings and forbidden whitespace are required. Typographic
quote, dash, and ellipsis changes are optional and occur only when their edit
IDs are explicitly accepted. The frozen plan binds source and normalized
hashes; list endpoints expose hashes and spans rather than manuscript text.

Unsupported-character classification is bound to the exact provider ID and the pinned
`audition-text-normalization@1.0.0` profile. The deterministic fixture accepts every valid Unicode
scalar because it hashes text rather than interpreting language. The component-only Kokoro profile
accepts printable ASCII, Latin letters, newlines, and an explicit reviewed punctuation set; every other
distinct code point is emitted once, in numeric order, as an exact `U+XXXX` warning. A plan with
any such value carries `NORMALIZATION_REVIEW_REQUIRED` and must not be treated as review-clean.
The classifier never deletes or substitutes the character. More than 32 distinct unsupported code
points, an unknown provider/profile tuple, invalid Unicode scalar input, or an unbounded decision
list fails closed. Provider ID, profile identity, exact unsupported-code-point array, and warnings
are all inputs to the immutable normalization-plan fingerprint. This component profile does not
make Kokoro available to the governed product; the missing Phase 3A voice/profile/assignment/
rights binding and legal/rights review remain blockers.
