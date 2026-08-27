# ADR 0020: Persistent counterparty relationship model

- Status: Accepted
- Date: 2026-08-09
- Related: ADR 0003, ADR 0004, ADR 0012, ADR 0015, ADR 0016, ADR 0019

## Context

Canonical history, episodic/semantic memory and affect do not answer the same question as a slow
relationship: how Satori's accumulated stance toward one counterparty has changed across
independent interactions. Deriving that stance from message volume, one declaration or retrieved
memory would create an unsafe feedback loop. Running another Qwen call before every reply would
also regress the Stage 7.7 foreground latency contract.

## Decision

### Aggregate, target and ownership

`RelationshipState v1` is one aggregate per `(satori_identity_id, counterparty_id)`. The local
single-user runtime uses the configured opaque counterparty `local-default`; this is structural
partitioning, not a Stage 9 User Model or authentication identity. `RelationshipManager` is the
only mutation owner. Conversation, memory, affect, provider adapters and CLI receive no write
capability.

The authoritative vector is `familiarity`, `trust`, `comfort`, `closeness`,
`intellectual_respect`, and `affection`, each in `[0, 1]`. Initial values are
`(0, .5, .5, 0, .5, 0)`: low evidence rather than distrust, discomfort or disrespect. There is no
love, romance, attachment, jealousy, dependency or exclusivity primitive. Affection is general
warmth and never authorizes reciprocal love, obedience or weaker boundaries.

### Evidence, maturity and appraisal

Only a new canonical completed user message/interaction is a root. Assistant output, affect,
retrieved memory, semantic claims and an LLM proposal are not new evidence. A terminal decision is
unique by source interaction; a meaningful transition is also unique by that interaction.

Evidence maturity is
`0.65 × min(qualified_interactions / 40, 1) + 0.35 × min(distinct_sessions / 8, 1)`.
Low maturity remains explicit uncertainty. It bounds the positive ceiling: familiarity uses `1`;
closeness/affection use `maturity`; trust/comfort/respect use `.5 + .5 × maturity`.

The replaceable background provider returns only one to three closed categorical event codes,
confidence and the two supplied opaque source handles. It never returns dimensions or prose.
The v1 single-root wire deliberately omits reliability-positive/negative because one current
statement cannot prove follow-through; the domain taxonomy reserves them for a future capability
with independent canonical roots. Claims such as “trust me” are not reliability evidence.

### Deterministic update and caps

For a combined category impulse `i` and confidence already included in `i`:

- positive: `min(i × max(0, ceiling - current), per_event_cap, remaining_positive_session_cap)`;
- negative: `max(i × current, -per_event_cap, -remaining_negative_session_cap)`;
- final values are clamped to `[0, 1]`; familiarity never decreases from ordinary events.

Per-event caps are `.010/.015/.020/.010/.015/.010` in vector order. Positive session caps are
`.080/.040/.050/.035/.050/.035`; negative session caps are `0/.120/.150/.080/.100/.080`.
Negative trust impulses are materially larger than repair impulses, so trust loss can be faster
than restoration. Repair is gradual. There is no wall-clock decay in v1 and time passage is not
evidence.

### Lifecycle, ordering and delivery

Current-event affect remains before generation and shapes the same reply. Relationship appraisal
runs only after canonical reply/affect commit, in the existing retryable post-response queue, and
affects future turns. It uses the Stage 7.7 `RELATIONSHIP` derived priority: lower than
conversation/affect and higher than episode/semantic formation. The next turn may read the last
committed snapshot without waiting.

Sources are processed in canonical `(started_at, interaction_id)` order per identity/counterparty.
Optimistic state version plus processed-count comparison prevents lost updates. Replays return the
existing terminal decision; zero-effect/low-confidence results record a decision without a state
version increment or transition. Provider/background failure cannot invalidate the canonical
reply and remains retryable.

### Persistence and historical rollout

Migration `0007_relationship_state` adds current state, terminal decisions and append-only
transitions, plus counterparty and context-version columns. It marks pre-migration interactions as
ineligible. No LLM runs in migration and no historical relationship is fabricated. Explicit
historical backfill is deferred until a separate provenance-complete policy is justified.

Conversation receives only a compact trusted qualitative projection with maturity and state
version. Numeric axes, counterparty/relationship IDs, raw text and provenance IDs are excluded.

## Consequences

- Relationship survives restart and is auditable to canonical user evidence without becoming
  memory, affect, personality or a User Model.
- Foreground committed-reply latency adds only a local projection read; the Qwen classifier is
  derived work and may lag one or more turns.
- The in-process queue is not a durable broker. Crash recovery is explicit idempotent processing
  of eligible undecided interactions; normal graceful shutdown drains the queue.
- V1 cannot establish real-world reliability from a single turn and has no automatic long-silence
  weakening, relationship deletion/export UI or authenticated multi-user routing.
