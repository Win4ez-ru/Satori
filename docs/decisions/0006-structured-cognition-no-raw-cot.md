# ADR-0006: Structured cognition without raw chain-of-thought

- Status: Accepted
- Date: 2026-07-26

## Context

The system needs observable appraisal, intent and decisions, but raw hidden reasoning is unnecessary, unstable, sensitive and not a reliable domain artifact.

## Decision

Cognition emits concise versioned structures: situation/need mix, appraisal, emotional proposal, internal position summary, intent, response strategy and response claim references for grounding. Store source references, confidence, decisions and reason codes. Do not request or persist raw private chain-of-thought.

## Consequences

Behavior remains debuggable and auditable without coupling product state to hidden reasoning text. Structured summaries may omit detail; tests target decisions and evidence, not invisible thought reproduction.

## Alternatives rejected

Persisting scratchpads/full CoT; opaque one-shot text generation with no structured trace.
