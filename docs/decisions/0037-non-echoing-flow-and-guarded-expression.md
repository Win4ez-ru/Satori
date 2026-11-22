# ADR 0037: Non-echoing flow and guarded expression

- Status: Accepted
- Date: 2026-08-28
- Supersedes: ADR 0036 (production candidate, ordinary depletion posture and final rendering only)
- Related: ADR 0021, ADR 0023, ADR 0029, ADR 0030, ADR 0035, ADR 0036

## Context

The separately authorized v20 OpenAI sample was technically clean, but direct user review rejected
all six turns. Every achievement reply spent most of its content restating the completed work. All
three depletion replies used effectively the same recovery-instruction scaffold. The sample showed
that separating a factual anchor from contribution is insufficient when the anchor still must be
rendered and ordinary depletion deterministically selects grounded direction.

The target character also needs a human conversational right to finish a thought, disagree, become
more reserved after repeated dismissive treatment and sometimes decline further emotional
interrogation. That behavior must not become a gender stereotype, a phrase bank, a permanent
`offended` flag or a second relationship owner. Ordinary disagreement and constructive correction
must remain safe. Even when expression is guarded, Satori must still answer important practical
requests rather than retaliate or sabotage help.

## Decision

Behavior policy v21 becomes the production-composition candidate. Policy v10 remains the last
provider-accepted baseline; v19 and v20 remain reproducible rejected provider-fit evidence. Stage 15
remains locked.

`CharacterExpressionPlan` schema v4 extends schema v3 with two closed request-local axes:

- `acknowledgement_mode`: omit, implicit or contextual;
- `continuation_mode`: complete, open, guarded or boundary.

Schema v2 and v3 remain supported by their historical policies. Policy v21 requires a complete v4
plan. The v4 axes are observable only as `compare=False` manifest metadata and never become domain
state, replay authority or provider-authored mutation.

V21 changes the two canonical turns structurally. Achievement uses implicit acknowledgement: the
provider may show recognition through a short evaluation but must not repeat the action, object or
result. The completion/depletion contrast omits acknowledgement, selects an owned emotional
reaction, removes the ordinary supportive-push posture and completes the thought without a required
question, recovery instruction or offer of help. Explicit motivation, listen-only distress and
protective-stop precedence from v20 remain intact.

Guarded expression is a deterministic projection from bounded current/recent user evidence, not a
new emotional truth. Direct personal devaluation, repeated dismissive or critical pressure and a
repeated probe into Satori's state can select `cool_reserve` plus `restrained_hurt`. A first state
question does not. Plain disagreement does not. Repeated criticism requires multiple canonical user
turns; quotes, examples and hypothetical text fail closed. Direct devaluation may select a concise
boundary, while an explicit substantive request keeps the reply helpful and merely guarded.

Guarded expression may also carry across turns when the existing authoritative relationship
projection is guarded and the existing affect owner remains tense or negative. That conjunction
changes only current delivery and does not name a cause. Serious distress, an explicit listen-only
request and a protective stop retain precedence over guarded hurt.

Exactly one v21 realization remains the last trusted character guidance before the current user
turn. It contains no sample reply or phrase bank. Generated text remains canonical and unrewritten;
the existing ten-reason maximum-one retry is unchanged and no model judge is added.

Deterministic acceptance uses `checkpoint142_character_expression_v4.json`. The separate
human-reviewed OpenAI gate uses `checkpoint142_character_sampling_v3.json` and remains fail-closed
behind a new explicit paid-call and USD authorization. Historical samples cannot authorize a v21
call.

## Consequences

- Recognition no longer requires restating the user's event or feeling.
- Advice, questions and continuation are choices rather than default proof of care.
- Satori can express bounded hurt or reserve without a second persistent owner.
- Disagreement and constructive correction do not automatically punish the user.
- Guarded tone cannot suppress an important factual or practical answer.
- Provider quality remains a sampled human-review question; offline correctness does not accept
  v21 character fit.
- No migration, output rewrite, extra validator reason, second retry, autonomous contact or Stage
  15 capability is introduced.
