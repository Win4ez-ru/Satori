# ADR 0015: Affective state, appraisal, decay and mood

- Status: Accepted
- Date: 2026-07-30
- Supersedes: none
- Related: ADR 0002, ADR 0003, ADR 0004, ADR 0006, ADR 0007, ADR 0012, ADR 0014

## Context

Stage 6 has persistent self, canonical conversation lifecycle and evidence-grounded memory, but no
dynamic internal affect across interactions. Stage 7 needs contextual affect without making an
LLM the state owner, copying user emotion, mutating personality, or introducing relationship
state. Time evolution must remain exact after restart and independent of read frequency.

## Decision

`EmotionManager` is the sole domain writer-owner for one continuous fast affect vector and one
slower mood vector. Both use schema v1 and policy v1. `valence` is bounded to `[-1, 1]`; every
other dimension is bounded to `[0, 1]`.

Fast v1 dimensions and `(baseline, half-life, maximum absolute event delta)` are:

| Dimension | Baseline | Half-life | Cap |
|---|---:|---:|---:|
| valence | 0.00 | 45 min | 0.22 |
| arousal | 0.12 | 12 min | 0.18 |
| tension | 0.08 | 30 min | 0.16 |
| curiosity | 0.18 | 45 min | 0.15 |
| interest | 0.16 | 90 min | 0.16 |
| amusement | 0.05 | 5 min | 0.18 |
| concern | 0.08 | 120 min | 0.18 |
| frustration | 0.04 | 40 min | 0.14 |
| situational confidence | 0.55 | 180 min | 0.12 |

Mood v1 dimensions use `(baseline, half-life, event cap)`:

| Dimension | Baseline | Half-life | Cap |
|---|---:|---:|---:|
| valence | 0.00 | 12 h | 0.04 |
| energy | 0.30 | 8 h | 0.03 |
| tension | 0.10 | 10 h | 0.03 |

`affection`, attachment, trust, closeness, familiarity, jealousy and dependency are not affect
dimensions. There is no persistent user emotion/mood model. Personality remains read-only and its
trait values are not copied into affective baselines.

## Structured appraisal and deterministic mutation

The replaceable structured provider receives only the current user event, immutable personality
and values, current materialized affect/mood, and already-selected bounded episodic/semantic
context. It returns schema v1 semantic signals:

```text
pleasantness, activation, novelty, salience, uncertainty,
curiosity_signal, interest_signal, humor_signal,
concern_signal, frustration_signal, confidence_signal,
appraisal_confidence, source_refs, reason_codes
```

It does not return state or delta. `source_refs` must include the current interaction and be a
subset of the exact interaction/memory/claim IDs supplied to it. Unknown or missing refs,
unsupported schema, empty reason codes and confidence below `0.35` reject the entire proposal.
Malformed, non-finite,
out-of-range or unknown fields fail the strict adapter/core boundary with no partial mutation.

For accepted input, `authority = salience × appraisal_confidence`. Raw v1 impulses are:

```text
valence = 0.22 × pleasantness
arousal = 0.18 × activation
tension = 0.16 × (0.45 uncertainty + 0.35 concern + 0.35 frustration)
curiosity = 0.15 × (0.65 curiosity + 0.25 novelty + 0.10 uncertainty)
interest = 0.16 × (0.65 interest + 0.20 salience + 0.15 curiosity)
amusement = 0.18 × (0.85 humor + 0.15 max(pleasantness, 0))
concern = 0.18 × concern
frustration = 0.14 × frustration
situational_confidence = 0.12 × (confidence_signal - 0.35 uncertainty)
```

Each impulse is multiplied by `authority` and explicit personality reactivity. Common emotional
sensitivity is `0.75 + 0.50 × emotional_sensitivity`. Frustration/tension additionally use
`1.15 - 0.45 × patience`; curiosity/interest use `0.80 + 0.40 × curiosity`; amusement uses
`0.80 + 0.20 × (playfulness + humor)`. Situational-confidence loss uses
`1.10 - 0.40 × self_confidence`, while gain uses `0.90 + 0.20 × self_confidence`. The result is
then capped per dimension and the next value clamped to its domain range. This is reactivity only;
no personality field is written.

## Decay and mood

Lazy materialization is a pure function:

```text
x(t) = baseline + (x0 - baseline) × 2^(-elapsed_seconds / half_life_seconds)
```

It rejects backwards time, never persists a read and never increments state versions. The formula
has the semigroup property up to floating-point tolerance and approaches the baseline without
overshoot for signed and unsigned dimensions.

An accepted fast delta produces one-way mood impulses:

```text
mood_valence_delta = 0.12 × fast_valence_delta
mood_energy_delta = 0.10 × arousal_delta
                  + 0.04 × interest_delta
                  + 0.03 × amusement_delta
mood_tension_delta = 0.12 × tension_delta
                   + 0.08 × concern_delta
                   + 0.10 × frustration_delta
```

Each mood impulse is capped by its v1 cap and the projection is clamped. Mood affects expression
but does not feed back into fast emotion dynamics in v1.

## Persistence, transaction and concurrency

`affective_states` holds one versioned current projection per identity. Every material mutation
adds one source-linked `affective_transitions` record containing structured appraisal metadata,
before/after snapshots, applied deltas, policy versions and provider/method metadata without raw
conversation, memory, semantic values, prompts or chain-of-thought.

Appraisal and conversation generation run outside a database transaction. The tentative
post-appraisal snapshot may shape the same reply, but it becomes authoritative only when the
assistant message, completed interaction metadata, state update, transition and audit commit in
one local transaction. Conversation generation/validation failure commits no transition.
Completed `client_request_id` replay returns the canonical reply and does not appraise or mutate
again.

State/mood versions are compared optimistically. Two different interactions prepared from the
same base cannot overwrite one another: one commits, the other receives a typed conflict and must
re-appraise/regenerate from the latest state. Blind delta retry is forbidden.

## Expression and failure behavior

Generation receives emotion/mood as a separate trusted, versioned expression context. It may
subtly influence tone, energy, humor, attention and caution, but cannot override truthfulness,
values, safety or independent judgment; it must not announce numeric state or imply biology or
relationship change.

Appraisal provider absence, outage or invalid contract degrades conversation to the materialized
pre-event state with explicit `unavailable`/`rejected` metadata and no mutation. No background
loop, heartbeat, scheduler or new service is introduced.

## Evidence for parameter choice

The v1 parameters are accepted only with deterministic simulations covering 500 neutral events,
positive/negative events, repeated frustration, repeated positive events, alternating signs, 100
near-simultaneous extreme events, multi-day recovery, semigroup/read-frequency equivalence,
slower mood decay, restart, transaction failure, retry and optimistic conflict. These fixtures
prove bounds and stability; they are not a claim that real-model appraisal is perfectly
calibrated. Any tuning requires a new policy version and updated simulation rationale.

## Consequences

- LLM interpretation is replaceable; domain evolution remains deterministic and replayable.
- One event cannot create an unlimited state jump, and ordinary time returns state toward stable
  baselines.
- The same tentative state that shapes the response is either committed with its canonical reply
  or discarded.
- Stage 7 does not add relationship, user/world model, emotional concepts, personality evolution,
  proactivity, background decay jobs or voice/avatar expression.
