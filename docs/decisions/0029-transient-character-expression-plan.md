# ADR 0029: Transient character-expression plan

- Status: Superseded in part by ADR 0030
- Date: 2026-08-24
- Supersedes: none
- Related: ADR 0002, ADR 0017, ADR 0020, ADR 0021, ADR 0023, ADR 0027, ADR 0028

## Context

The accepted personality seed already describes an intelligent, independent, emotionally
perceptive and lightly ironic Satori. Provider context also carries five source-linked personality
guidance items. Repeated Yandex production samples nevertheless converged on polite generic
assistant prose. Behavior policies v11-v14 reduced observed defects through increasingly narrow
negative instructions, but did not make Satori's own reaction, wit, guarded care or situational
openness legible. The v14 exhaustion reply was safe and consistent while still sounding like an
ordinary text model.

This is an expression-selection defect, not evidence for another personality, mood, relationship
or memory aggregate. Copying a named fictional character, scripting catchphrases, rewriting
provider output or persisting a style mode would conflict with the product constitution.

## Decision

### Original character contract

Satori remains an original adult anime-inspired digital person, not an imitation of an existing
character. Her baseline may combine intellectual independence, understated warmth, playful
challenge, situation-directed irony, guarded vulnerability and decisive practical care. Politeness
is not a target metric. Sarcasm may point at a situation or argument, never at a counterparty's
vulnerability, ability or dignity. In a materially vulnerable moment, open direct care outranks a
display of wit.

### Typed transient selection

For every foreground request, the application derives a versioned immutable
`CharacterExpressionPlan` from the five authoritative personality-expression guidance codes plus
the current typed cognition strategy, qualitative affect profile, relevant relationship
projection and narrow current-turn flags. It selects one closed register:

- `warm_independence`;
- `wry_warmth`;
- `quiet_open_care`;
- `playful_edge`;
- `lively_collaboration`;
- `reflective_candor`;
- `direct_repair`;
- `thoughtful_precision`.

The plan also carries bounded wit, care, openness, initiative and relationship-ease codes. It
contains no raw dialogue, evidence text, biography or generated prose. Relationship modulation is
read only when an authoritative relationship facet is relevant to the current request; otherwise
baseline personality remains primary.

### Ownership and delivery

The plan is request-local read projection. It has no repository, table, manager, mutation API,
cross-session carry-over or provider write-back path. It cannot change personality, affect,
relationship, positions or inclinations. Metadata exposes only schema and selected register during
the live request; it is not replay authority.

Behavior policy v15 renders a compact positive character contract alongside the plan. The provider
receives enum labels plus only the selected register/wit guidance; the full typed plan and its
personality provenance remain in application state. This avoids turning the projection into a
second personality manifesto. It does not provide an exact reply, named-character imitation or
mandatory joke. Provider output still passes the unchanged grounding and closed ten-reason Stage
8.1 validator and is committed without rewriting or judge-model review. Normal turns still use one
foreground call; the existing maximum-one typed retry is unchanged.

### Evaluation

A versioned semantic corpus covers equal-adult achievement, understated/open care, playful
disagreement, guarded compliment uptake, practical help, creative energy, reflection, direct
repair and technical precision. It specifies typed starting conditions, semantic review dimensions
and undesirable generic patterns but no required response text. Acceptance still requires the
mandatory character and Stage 8.1 real-provider regressions plus explicit human review of every
sampled reply.

## Consequences

- Existing persistent personality becomes more legible without moving identity into a prompt.
- The model receives positive situational guidance instead of only a growing list of prohibitions.
- Character remains stochastic expression rather than a phrase template; sampled evidence can
  still fail and must be retained honestly.
- Prompt size grows modestly and must be measured in the mandatory regression.
- Behavior policy schema v15 is unrelated to roadmap Stage 15, which remains locked.
