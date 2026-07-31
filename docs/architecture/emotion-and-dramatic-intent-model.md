# Emotion and dramatic-intent model

Phase 2 records delivery-relevant interpretation without rewriting the
manuscript. Scene tone, character emotional state and change, dialogue intent,
tension, urgency, restraint, dramatic function, and uncertain subtext use
controlled values plus bounded operator-safe notes.

Text evidence and interpretation are separate fields. Confidence describes
support for a deterministic rule, not an objective psychological fact. The UI
labels every machine result as a proposal and keeps low-confidence,
contradictory, or unsupported interpretation explicit.

Humans may correct an emotional state or dramatic-intent value through the
append-only overlay. A correction preserves the prior machine value, records
its previous-value fingerprint and provenance, and cannot be reversed silently
by a rerun.
