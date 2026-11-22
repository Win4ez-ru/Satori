# ADR 0033: Late compact character delivery

- Status: Accepted
- Date: 2026-08-27
- Supersedes: ADR 0030 (provider-rendering placement and wording only)
- Related: ADR 0002, ADR 0021, ADR 0027, ADR 0029, ADR 0030, ADR 0031, ADR 0032

## Context

Checkpoint 14.2 already derives a typed request-local `CharacterExpressionPlan` v2 from canonical
personality guidance and bounded cognition, affect, dialogue and relationship projections. Normal
production composition nevertheless remained on accepted behavior policy v10, so the plan was
computed but neither rendered nor exposed in the manifest. Satori's foreground reply therefore
could remain a generic assistant response even though the application had selected guarded
approval, situation-directed wit or open care.

Candidate v16 did render the plan, but placed eight guidance dimensions and their internal enum
labels in the early baseline-character message while repeating scenario-specific instructions in
a late identity reminder. Three bounded Yandex sessions rejected that realization: the model
produced generic praise or explanation, exposed a placeholder and inferred disappointment that the
user had not stated. A later one-call OpenAI probe using production v10 passed transport limits but
again produced generic congratulation and unsolicited productivity advice.

This is a provider-facing realization problem, not evidence for another personality source,
persistent style state or generated-text rewrite. The user explicitly authorized the follow-up on
2026-08-27. Stage 15 remains locked, and no paid provider call is authorized by this decision.

## Decision

Behavior policy v17 becomes the production-composition candidate. It preserves every v16
grounding, disclosure, affect, relationship and self-consistency rule, while reducing the durable
character principle to concrete stable behavior: Satori speaks as an intelligent independent
equal, owns a position and reaction, may hide care behind a dry barb, and can become direct in a
genuinely vulnerable moment. The general brevity principle describes only response substance and
does not repeat internal plan labels.

`CharacterExpressionPlan` remains schema v2, immutable and request-local. Its selector, closed
enums, inputs, manifest metadata and lack of persistence are unchanged. For policy v17, the early
baseline-character message contains only stable canonical voice. A new pure renderer converts the
already selected plan into a compact Russian realization brief immediately before the current
turn's final disclosure/identity contract. The brief:

- describes the owned reaction and semantic contribution as observable writing choices;
- gives only the selected wit, initiative and qualitative relationship boundaries needed now;
- contains no enum values, reply text, stock phrase or existing-character imitation;
- explicitly prevents a model from naming or explaining the chosen style;
- remains trusted transient guidance and has no write-back path.

The late brief and current-turn reminder share one developer message. This keeps message count
unchanged and makes the dynamic direction adjacent to the user turn without weakening the system
policy. Historical v15/v16 builders retain their original verbose projection for reproducible
evaluation artifacts; they are not rewritten retroactively.

The existing ten-reason self-consistency validator, shared maximum-one retry, output bounds,
grounding gate and canonical commit path remain unchanged. No output phrase rewriting, judge LLM,
new validator reason, autonomous contact or numeric initiative policy is introduced.

## Consequences

- Normal composition now exposes the existing typed character decision instead of silently
  discarding it.
- Provider instructions are shorter and outcome-oriented while the durable personality source
  remains canonical typed state outside the provider.
- Human character quality is still a sampled property. Offline contracts can prove projection,
  isolation and invariants but cannot accept a stochastic provider's wording.
- Any paid v17 OpenAI or Yandex sampling requires separate explicit authorization and must preserve
  every public reply for direct human review.
- Stage 15 remains locked and is not started by this decision.
