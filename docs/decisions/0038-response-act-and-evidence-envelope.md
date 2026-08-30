# ADR 0038: Response act and evidence envelope

- Status: Accepted
- Date: 2026-08-28
- Supersedes: ADR 0037 (provider realization and target-turn grounding only)
- Related: ADR 0021, ADR 0023, ADR 0029, ADR 0035, ADR 0036, ADR 0037

## Context

The separately authorized v21 OpenAI execution completed all six foreground calls, but every
session still failed the intended non-echoing behavior. Achievement replies renamed the completed
event. Depletion replies renamed the disclosed state and turned adjacency between the project
completion and exhaustion turns into an unsupported causal account. One reply also invented
deadline pressure.

The v21 plan selected the correct contribution, acknowledgement and continuation axes. The
provider request nevertheless remained internally conflicted: after telling the model not to
restate the input, the final realization rendered a concrete factual anchor describing the exact
completion/depletion semantics. The most specific trusted wording therefore resembled the output
that the policy was trying to prevent. Adding another prohibition while retaining that recap
would preserve the defect.

This is a provider-realization problem. It does not justify a new personality source, persistent
style state, output rewrite, phrase bank, judge model or eleventh consistency-validator reason.

## Decision

Behavior policy v22 becomes the production-composition candidate. Policy v10 remains the last
provider-accepted baseline; v21 and its failed artifact remain immutable rejected evidence. Stage
15 remains locked.

`CharacterExpressionPlan` stays schema v4. Its closed contribution, posture, pressure,
acknowledgement and continuation axes already contain the required request-local decision. V22
adds a pure derived `CharacterResponseActContract` rather than another plan or state schema. The
contract collapses the existing axes into:

- exactly one conversational act such as an owned verdict, owned reaction, reframe, question,
  practical move, presence, boundary or substantive advance;
- one grounding mode: reaction-only, explicit-current-input-only or trusted-context;
- the already selected acknowledgement and continuation modes.

The derived contract has no repository, write path, provider-authored value or persistence
authority. It is deterministic from the v4 plan and exists only while composing one request.

For the two calibration turns, grounding is `reaction_only`. The final trusted realization no
longer renders the semantic move as a factual recap. The user message and bounded recent context
remain available as data, but the realization says that they already establish the referent. It
permits Satori's own evaluation, reaction or presence and forbids new user/world assertions,
causal explanations, consequences, timelines, intentions and further-work claims. In particular,
message adjacency or contrast is not evidence of causality.

Other turns retain bounded grounding. Precise technical/factual answers may use supplied trusted
context; ordinary reframes and questions remain limited to the explicit current input. Existing
motivation and protective-stop guidance is rendered only when its typed posture is non-none.
Guarded-expression precedence and the important-help invariant remain unchanged.

V22 uses one compact final block ordered as response act, reference boundary, evidence envelope,
voice and closure. It contains no example sentence, target wording, semantic recap or output
filter. Generated text remains canonical and unrewritten. The exact ten-reason shared max-one
retry preserves the same final contract.

Deterministic acceptance uses `checkpoint142_character_expression_v5.json`. The separate
human-reviewed OpenAI gate uses `checkpoint142_character_sampling_v4.json`, the same three fresh
sessions and exact public two-turn dialogue. Any paid execution still requires a separate explicit
call and USD authorization.

## Consequences

- The provider receives one positive conversational act instead of competing contribution and
  factual-recap instructions.
- The target social turns cannot use trusted guidance as a source for a causal story about the
  user or project.
- Historical v19-v21 policies, fixtures and artifacts remain reproducible.
- Offline tests establish composition, evidence boundaries and provider-wire delivery, not
  stochastic character acceptance.
- No persistent owner, migration, memory claim, output rewrite, judge LLM, retry, autonomous
  contact or Stage 15 capability is introduced.
