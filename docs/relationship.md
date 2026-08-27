# Persistent relationship model

Status: Stage 8 state model implemented; policy/schema/appraisal versions are `1`, migration head
is `0007_relationship_state`, and Stage 8.1 calibrates only its conversation expression.

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

Generation receives a small trusted qualitative projection with maturity and state version. It
can subtly calibrate warmth/directness, but never changes truth, grounding, values, safety,
autonomy or permission to disagree. High trust/affection does not mean love, romance, dependency,
exclusivity, possession, obedience or an obligation to agree. There is no CLI setter.

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

Developer inspection:

```bash
uv run --no-sync satori relationship status
uv run --no-sync satori relationship history --limit 20
uv run --no-sync satori relationship process --interaction INTERACTION_ID
```

Status/history are typed read projections and contain no raw user text. Exact policy rationale is
recorded by [ADR-0020](decisions/0020-persistent-counterparty-relationship-model.md).
