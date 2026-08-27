# ADR 0030: Relationship-modulated character expression

- Status: Accepted
- Date: 2026-08-25
- Supersedes: ADR 0029 (relationship-modulation and provider-delivery clauses only)
- Related: ADR 0002, ADR 0020, ADR 0021, ADR 0023, ADR 0027, ADR 0028, ADR 0029

## Context

ADR 0029 introduced a typed request-local `CharacterExpressionPlan`, but allowed relationship
modulation only when the current request explicitly required an authoritative relationship facet.
That gate protects damaged relationship state from becoming global hostility, yet it also prevents
ordinary replies from expressing the legitimate positive difference between a fresh, developing
and established relationship. The separate Stage 8 relationship projection already permits
greater ease and personal warmth in an established positive relationship without changing truth,
autonomy or grounding.

This is a read-projection calibration problem. It is not evidence for a new relationship axis,
style aggregate, initiative counter or persistent character mode. Discussed percentages such as a
`50 -> 85` initiative range are product intuition, not an implemented probability contract.

The v15 plan also exposed only register and wit as usable positive guidance. Care, openness and
initiative remained opaque labels, and there was no closed way to tell the provider what reaction
belongs to Satori or what new meaning the answer should add. Late scenario-specific reminders then
overrode the earlier plan and collapsed generation back into generic acknowledgement or empathy.

## Decision

### V2 semantic expression contract

`CharacterExpressionPlan` schema v2 adds two closed request-local fields:

- `owned_reaction` selects Satori's current orientation, such as guarded approval, sober concern,
  engaged skepticism, accountable regret or focused confidence;
- `semantic_move` selects the grounded contribution, such as marking a hard-won result, connecting
  an explicit completion/depletion contrast, responding to explicit vulnerability, testing a
  current claim, advancing a shared idea, repairing a mistake or acknowledging repetition.

These fields are enums, not generated prose, personality state, affect state, a position or a
memory. The selector may consume only existing typed strategy/affect/relationship projections and
narrow deterministic current-request signals: explicit request, canonical user-grounded
completion/depletion contrast and exact repeated-turn detection. Assistant history cannot ground
completion. Negation, conditional language and material uncertainty cannot be promoted into an
achievement.

Behavior policy v16 renders positive guidance for every selected reaction, move, register, wit,
care, openness, initiative and relationship-ease code. A late reminder may reinforce the same
selected move but cannot introduce a scenario that the typed plan did not select. The manifest may
expose only these closed request-local codes and their schema; they remain non-comparable replay
metadata, not canonical state. No required reply, phrase rewriting or judge model is introduced.

### Ordinary positive modulation

For each foreground request, the transient character-expression selector may consume the existing
qualitative relationship expression profile even when the current request has no explicit
relationship facet. Only qualitative delivery may change:

- `fresh_undeveloped_neutral` preserves baseline warmth, openness and curiosity while avoiding
  assumed familiarity, intimacy or a shared conversational rhythm; guarded wit and understated
  care may remain more visible than personal warmth;
- `developing_neutral` permits a little more ease, conversational confidence and person-specific
  attention without claiming established closeness or shared history;
- `established_positive` permits greater personal ease, confident continuity, warmer directness
  and more response-local initiative when the current cognition strategy and dialogue context
  already allow it.

The projection may qualitatively modulate the existing care, openness, initiative and
relationship-ease codes. It cannot change the response stance, material uncertainty, factual or
memory grounding, independent judgment, safety, verbosity bounds or permission to disagree.
Numeric relationship axes, evidence, transitions and provenance remain outside generation.

### Damaged relationship boundary

`guarded_only_when_relationally_relevant` continues to affect character expression only when the
current subject makes the relationship state relevant. An unrelated technical, factual or
practical request remains civil, open and competent. Damaged trust or comfort cannot cause global
hostility, punishment, silent treatment, degraded help or withdrawal from unrelated topics.

### Initiative boundary

Initiative in this ADR means contribution inside the current foreground reply: for example, making
one concrete observation, advancing a shared idea or taking the next conversational step already
licensed by the current typed strategy. No per-turn coin flip, percentage target, persistent
initiative history or engagement optimization is introduced.

Out-of-band initiation, observer-driven contact, scheduled messages, topic opening without a
current user turn, quiet-hours policy and rate limits remain Stage 19. This ADR does not authorize
them.

### Preserved ADR 0029 contracts

Except for the relationship gate and provider-delivery/metadata details superseded above, all other
ADR 0029 decisions remain in force: Satori is an original character; the plan is typed, immutable
and request-local; it contains no raw dialogue, evidence text, biography or generated reply; it has
no repository, owner, mutation API, carry-over or provider write-back path. Provider output is not
rewritten or judged by another model, the closed ten-reason validator is unchanged and the shared
maximum-one retry remains the only regeneration path.

## Consequences

- Ordinary replies can express gradual positive relational ease without creating another
  personality or relationship source.
- Provider guidance can request a recognizably owned reaction and grounded semantic contribution
  without prescribing Satori's words.
- Freshness and established closeness change delivery, not truth or the existence of shared past.
- Damaged relationship state stays contextually bounded and cannot become a punishment mechanism.
- Evaluation must compare equivalent ordinary turns across fresh, developing, established and
  damaged projections while keeping cognition, affect, memory and user input fixed, and must map
  every v2 enum field without storing a desired assistant reply.
- Initiative distributions remain an evaluation question until a separately approved typed
  current-dialogue contract exists; no `50 -> 85` behavior is claimed here.
