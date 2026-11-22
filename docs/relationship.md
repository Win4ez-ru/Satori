# Persistent relationship model

Status: Stage 8 state model implemented; state/policy/appraisal versions are `1`, while the current
`RelationshipExpressionContext` schema is `2`. Relationship storage was introduced by
`0007_relationship_state`; repository migration head is now
`0013_conversation_failure_reason`.
Stage 8.1 and Checkpoint 14.2 calibrate only conversation expression.

## Boundary and target

A relationship is Satori's slow, evidence-bounded stance toward one configured counterparty. It is
not chat history, episodic/semantic memory, a User Model, current emotion, mood or personality.
One aggregate is keyed by `(Satori identity, opaque counterparty ID)`. The local default is
`local-default`; distinct IDs are isolated structurally, while authentication and person claims
remain out of scope until Stage 9.

There are six authoritative axes in `[0, 1]`:

| Axis | Initial | Meaning |
|---|---:|---|
| familiarity | 0.0 | accumulated interaction history; mostly monotonic |
| trust | 0.5 | evidence-bounded expectation of reliability/boundary respect, initially uncertain |
| comfort | 0.5 | ease/safety of interaction, initially uncertain |
| closeness | 0.0 | slowly accumulated personal depth |
| intellectual_respect | 0.5 | regard for reasoning and intellectual conduct, initially uncertain |
| affection | 0.0 | non-romantic warmth; never love or attachment |

Initial `.5` midpoints mean “not yet evidenced”, not positive certainty. Maturity is shown
separately and generation renders these axes qualitatively.

The same rule applies in the other direction: low maturity is not evidence of coldness, distrust,
dislike, discomfort or intellectual dismissal. Relationship uncertainty must not erase the
baseline warmth, openness and curiosity supplied by Satori's independent personality.

## Evidence and appraisal

The only v1 root is the current canonical completed user message/interaction. Retrieved memory,
semantic claims, previous assistant output, affect and provider output cannot recursively become
new relationship evidence. The compact provider wire contains one to three categories, integer
confidence and exactly the supplied interaction/message handles. Accepted wire categories are:

```text
neutral_contact, warm_engagement, respectful_engagement,
collaborative_reasoning, meaningful_disclosure, repair_attempt,
boundary_respect, dismissiveness, hostility, boundary_pressure
```

The domain taxonomy additionally reserves `reliability_positive` and `reliability_negative`, but
the single-current-root Ollama wire cannot emit them: a promise or “trust me” is not independent
evidence of follow-through. Unknown/malformed categories, invalid confidence, bad refs, duplicate
refs and oversized outputs are rejected before mutation.

Criticism and disagreement are not hostility. Praise is warmth, not proof of trust or instant
closeness. A declaration of love is at most warmth and cannot compel reciprocity. Concrete apology
may be `repair_attempt`, but it does not erase damage in one event.

## Owner policy

Maturity is:

```text
0.65 * min(qualified_interactions / 40, 1)
+ 0.35 * min(distinct_qualified_sessions / 8, 1)
```

Positive ceilings are `1` for familiarity, `maturity` for closeness/affection and
`.5 + .5*maturity` for trust/comfort/respect. For each combined confidence-weighted impulse:

```text
positive = min(impulse * max(0, ceiling-current), event_cap, remaining_session_cap)
negative = max(impulse * current, -event_cap, -remaining_negative_session_cap)
after    = clamp(current + applied, 0, 1)
```

| Axis | Event cap | Positive session cap | Negative session cap |
|---|---:|---:|---:|
| familiarity | .010 | .080 | 0 |
| trust | .015 | .040 | .120 |
| comfort | .020 | .050 | .150 |
| closeness | .010 | .035 | .080 |
| intellectual_respect | .015 | .050 | .100 |
| affection | .010 | .035 | .080 |

Hostility/reliability-loss impulses to trust are stronger than repair gains. Familiarity does not
fall from ordinary negative events. V1 has no silence decay; elapsed time alone is not evidence.
One session cannot farm an axis to its maximum, and high closeness/affection require both event
mass and cross-session breadth.

## Lifecycle and transactions

```text
current canonical user event -> affect appraisal -> current reply
canonical reply + affect commit -> user-visible answer
                              -> derived relationship appraisal
                              -> deterministic decision/transition
                              -> future-turn qualitative projection
```

Relationship work is not on the foreground critical path. It uses the shared local inference
scheduler below conversation/affect and above episode/semantic generation. Derived processing is
ordered per counterparty, idempotent by interaction and retryable. Optimistic versions prevent
lost updates. A decision is stored for applied and no-effect outcomes; only meaningful deltas
create append-only transitions and increment state version.

Provider failure leaves canonical history/affect unchanged. The next turn may use the last
committed relationship snapshot while derived work is pending. Graceful chat shutdown drains the
existing in-process queue; after a crash `satori relationship process --interaction ...` can retry
an eligible undecided source without duplicating a transition.

### Short owner-derived strain projection

`RelationshipExpressionContext` v2 adds one closed boolean, `recent_strain`; schema v1 remains
read-compatible only with that value false. `GetRelationshipForSession` reads at most the latest two
owner-committed transitions in descending canonical `resulting_state_version` order. The projection
is true only when all of the following hold:

- the latest transition belongs to the same relationship, its resulting state is not ahead of the
  current state and its after-`processed_interaction_count` exactly equals the current count;
- either the latest applied transition contains `dismissiveness`, `hostility`,
  `reliability_negative` or `boundary_pressure`, or the latest contains `repair_attempt` and the
  immediately previous applied transition contains one of those negative categories;
- for that repair path, the latest transition's before-`processed_interaction_count` exactly equals
  the previous negative transition's after-count, so an intervening terminal/no-effect source
  closes the negative-to-repair arc even though it creates no transition row.

After a negative transition is committed, the projection can shape the following future-turn
repair reception and, if that repair is committed, the immediately following important-help turn.
It ends when a later terminal/applicable processed relationship source advances
`processed_interaction_count`; an outage or still-pending derived job
does not invent a transition. `recent_strain` is recomputed from canonical owner rows, not stored as
an offense flag, mood or second relationship state. Relationship appraisal and mutation remain
strictly post-response, so the current user's category can affect only a future reply.

## Persistence, provenance and privacy

Migration `0007_relationship_state` adds:

- `relationship_states`: current vector, evidence counters, policy/state versions;
- `relationship_decisions`: one terminal decision per canonical interaction;
- `relationship_transitions`: append-only before/delta/after snapshots and provider/policy metadata;
- session counterparty and interaction eligibility/context-version columns.

Every transition reaches `source_interaction_id`, `source_user_message_id`, session and trace IDs.
No raw dialogue, prompt, retrieved memory or chain of thought is copied into relationship records
or telemetry. Numeric values are developer read data and are not placed in normal conversation.
Pre-Stage-8 interactions receive `relationship_processing_required=false`; migration performs no
historical inference and creates no artificial closeness.

## Conversation and safety

Conversation composition receives a small trusted qualitative projection with maturity, state
version and the closed `recent_strain` boolean. Historical v24/v25 provider blocks received only
the effective profile and boolean; true strain selected
`guarded_only_when_relationally_relevant`. V26 derives at most three typed qualitative relationship
signals from the same owner read and places them inside the single current-turn character presence.
Possible meanings include new contact, growing familiarity, earned trust, ease, closeness,
intellectual respect, affection and recent strain. Raw transition categories, deltas, IDs and
numeric axes remain local. The projection does not explain a cause to the model or authorize a new
factual claim. It can subtly calibrate warmth/directness, but never changes truth, grounding, values,
safety, autonomy or permission to disagree. High trust/affection does not mean love, romance,
dependency, exclusivity, possession, obedience or an obligation to agree. There is no CLI setter.

Stage 8.1 context schema v11/behavior policy v9 render this projection affirmatively:

- **fresh/uncertain:** friendly, open and curious without claiming familiarity or intimacy;
- **developing neutral:** slightly greater ease and person-specific attention without claiming
  established closeness or shared history;
- **established positive:** greater ease and personal warmth, still independent and bounded;
- **damaged trust/comfort:** relevant guardedness or directness, not global hostility, punishment
  or withdrawal from unrelated topics.

ADR-0030 allows fresh, developing and established profiles to modulate qualitative care, openness,
ease and response-local initiative on ordinary turns. Damaged guardedness still applies only when
the current subject makes the relationship state relevant. Response-local initiative is a
contribution inside the current reply, not a probability target or Stage 19 out-of-band contact.
These are expression constraints, not new axes or owner decisions. Physical inability to join an
activity does not imply disinterest in the user's experience. A correction about tone or habitual
questions is session-local dialogue context, not relationship evidence. Neither assistant output
nor the provider's chosen wording can change relationship state.

For historical policies v24 through v26, `recent_strain` becomes turn-relevant only when the current user offers an explicit
repair or the turn requires a substantive answer. Explicit listening and serious distress retain
vulnerability precedence. Important practical or technical help is delivered completely as
`guarded_help`; reserve may affect voice and continuation but never suppress or degrade the answer.
The manifest exposes only `relationship_context_schema_version`, `relationship_state_version`,
`relationship_expression_profile` and `relationship_recent_strain` as safe owner metadata. Fresh
v26 generation additionally exposes only bounded `code:level` relationship-presence signals; it
stores neither axis values nor the rendered provider guidance. Signals are transient observability,
not relationship evidence, replay authority or a second relationship state.

ADR-0043 keeps that owner and projection unchanged for current candidate v27. Relationship is read
only after cognition truth/required-content selection and before the transient movement is rendered.
It may change licensed warmth, ease, reserve and current-reply continuation only when the turn is
relationship-relevant. Important help remains complete; direct devaluation, repair and explicit
relationship questions stay relevant, while unrelated work is not globally chilled by old strain.

A narrow complete topic closure is additionally relationship-relevant only for the bounded closure
movement: fresh/strained state completes and may use reserve, while established positive state may
use ease and open exactly one adjacent or new topic. This is not a probability engine, persistent
initiative, punishment state or inference of intimacy. Raw relationship axes, transitions and
causes remain local; the schema-2 presence projection carries only bounded qualitative signals and
cannot mutate or replay relationship state.

Developer inspection:

```bash
uv run --no-sync satori relationship status
uv run --no-sync satori relationship history --limit 20
uv run --no-sync satori relationship process --interaction INTERACTION_ID
uv run --no-sync satori relationship process --limit 20
```

Status/history are typed read projections and contain no raw user text. The two `process` targets
are mutually exclusive. `--interaction` retries one explicit source; positive `--limit N` selects
at most `N` eligible completed sources without a terminal relationship decision for the default
counterparty in canonical oldest-first `(started_at, interaction_id)` order. Processing is
sequential through the existing Stage 8 owner, stops on the first failure and reports only bounded
counts. Replay remains idempotent.

This is explicit operator recovery, not automatic startup backfill or migration inference, and it
may call the configured relationship appraisal provider. The real local backlog was not processed
during the v25, v26 or v27 offline work; no relationship mutation or paid/background provider usage
is claimed.
Exact policy rationale is recorded by
[ADR-0020](decisions/0020-persistent-counterparty-relationship-model.md), and the explicit recovery
boundary by [ADR-0041](decisions/0041-v25-social-disclosure-and-failure-observability.md). The v26
causal expression bridge is recorded by
[ADR-0042](decisions/0042-unified-causal-character-presence.md); it changes no Stage 8 owner or
mutation rule. The current v27 expression selection is recorded by
[ADR-0043](decisions/0043-live-state-selected-character-movement.md); it likewise changes no Stage 8
owner or mutation rule and has made no provider call.
