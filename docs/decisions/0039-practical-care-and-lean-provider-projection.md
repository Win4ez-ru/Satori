# ADR 0039: Practical care and lean provider projection

- Status: Accepted
- Date: 2026-08-28
- Supersedes: ADR 0038 (current provider realization and target-turn selection only)
- Related: ADR 0021, ADR 0023, ADR 0029, ADR 0036, ADR 0037, ADR 0038

## Context

The separately authorized v22 OpenAI gate completed all six calls cleanly, but human review
rejected all three pairs. Achievement turns still reconstructed the reported event. Depletion
turns repeatedly used a generic empathy-plus-normalization scaffold and introduced unsupported
internal mechanisms. The typed v22 plan made that second failure likely: ordinary explicit
depletion was reduced from v20 practical care to `emotional_reaction` with no motivational
posture, so the provider was asked to react without a useful independent move.

The no-recap rubric was also stricter than the intended conversation. A short context-dependent
acknowledgement is acceptable when it does not name, paraphrase or metaphorically reconstruct the
event. What is rejected is spending the answer on a substantive recap or adding a rationale after
Satori's verdict.

The repeated failures do not justify another personality source, scripted phrases, output
rewriting, a judge model, a new validator reason or a provider switch. They require a corrected
request-local action and a less redundant provider projection.

## Decision

Behavior policy v23 becomes the production-composition candidate. Policy v10 remains the last
provider-accepted baseline; v19-v22 and their sampled artifacts remain immutable historical
evidence. Stage 15 remains locked.

`CharacterExpressionPlan` schema v5 preserves the existing closed fields but versions the changed
selection semantics instead of silently altering schema v4. For ordinary explicitly stated
depletion, v23 selects one `grounded_direction` contribution with `supportive_push`, a `gentle`
pressure ceiling, practical care and at most a restrained situation-directed edge. This licenses
one proportionate recovery-oriented practical move from the current input. It does not establish
a cause, remaining project work, a deadline, surrender or a duty to continue.

Serious distress and an explicit request to listen retain precedence and select quiet presence
with no advice or motivational pressure. Explicit motivation and directly evidenced harmful
overextension retain their existing bounded firm/protective paths. Guarded relationship or
current-turn evidence is not overwritten by ordinary depletion.

The pure `CharacterResponseActContract` keeps schema v1. For a schema-v5 practical move its
grounding becomes `explicit_input_only`; achievement remains `reaction_only`. Achievement may use
one brief deictic acknowledgement, but it must not identify or restate the event, and the owned
verdict must stop without a rationale or second semantic move.

The provider receives one lean final block with exactly four decisions: action, evidence, voice
and stop. The block does not render the old factual anchor or concatenate all historical plan
axes. It contains no example sentence, phrase bank or target wording. Generated text remains
canonical and unrewritten; the shared ten-reason max-one retry reuses the byte-identical final
contract.

Deterministic acceptance uses `checkpoint142_character_expression_v6.json`. The separately
authorized human-reviewed gate uses `checkpoint142_character_sampling_v5.json`, the same three
fresh sessions and exact public two-turn dialogue. Its target remains `gpt-5.6-terra`, but v23
requires `reasoning=medium`; historical v19-v22 gates remain pinned to `low`. Paid execution still
requires a separate explicit call count and USD authorization.

## Consequences

- The action chosen for ordinary depletion now expresses Satori through useful guarded care rather
  than generic normalization.
- High-distress and listen-only boundaries remain deterministic and cannot be overridden by the
  motivational path.
- Historical policy, plan and evaluator versions remain reproducible.
- The provider projection is smaller and has one instruction owner for each of action, evidence,
  voice and closure.
- No persistent state, owner, migration, relationship mutation, output filter, judge LLM, retry,
  autonomous contact, provider call or Stage 15 capability is introduced.
