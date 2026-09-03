# ADR 0044: Conservative output evidence and isolated V27 production attempts

- Status: Accepted
- Date: 2026-08-30
- Supersedes: ADR 0032 only where `output_tokens - reasoning_tokens` was described as an exact
  count of visible reply tokens
- Related: ADR 0007, ADR 0021, ADR 0031, ADR 0032, ADR 0041, ADR 0043

## Context

The first separately authorized V27 OpenAI production attempt completed 18 replies and stopped on
replica 3 turn 3. OpenAI reported 227 output tokens, including 63 reasoning tokens. The adapter
therefore derived 164 non-reasoning output tokens and rejected the response against that turn's
160-token application limit. The combined wire ceiling of 1184 was not exhausted.

That failure exposed three evidence-boundary issues rather than a character verdict:

- the broad three-facet self-disclosure movement had only four tokens of headroom over a complete
  sampled response, while the other V27 turn-local bounds were not implicated;
- OpenAI documents `output_tokens` as all generated output, including reasoning and other
  non-visible generated tokens. Subtracting only `reasoning_tokens` is therefore an enforceable
  conservative non-reasoning remainder, not proof of the exact tokenizer length of visible text;
- the post-response rejection crossed the typed provider boundary with safe execution metrics but
  without the response's numeric usage breakdown, so the failed call's exact cache-aware cost could
  not be retained even though no response text needed to cross that boundary.

Attempt 1 consumed its one-shot authorization. Recomputing a new plan under that identity or
silently reusing its report path would make authorization and evidence ambiguous.

## Decision

### One narrow V27 cap correction

Only policy V27's broad self-disclosure request that requires at least three of identity, emotion
and interests receives a 200-token application output bound. Policies V25 and V26 remain frozen at
160, and every other V27 turn in the eight-turn production fixture retains its existing bound. The
exact V27 attempt-2 vector is:

```text
[48, 48, 200, 96, 96, 384, 112, 96]
```

With OpenAI reasoning `medium` and allowance 1024, turn 3 therefore uses wire
`max_output_tokens=1224`. The application still rejects a provider-reported non-reasoning remainder
above 200. This is bounded completion headroom, not permission for generally longer replies or a
change to Ollama/Yandex/provider-neutral defaults.

### Conservative interpretation of the OpenAI split

The OpenAI adapter continues to calculate `output_tokens - reasoning_tokens` because it is the
strongest provider-reported bound available without retaining content or reproducing a mutable
provider tokenizer. The result is interpreted as a conservative **non-reasoning output remainder**:
it includes visible reply tokens but may also include provider formatting or other non-visible
generated tokens.

The existing `visible_output_tokens` metric/key remains for compatibility with immutable reports,
debug output and typed consumers. New documentation and evidence claims must not call it an exact
visible-text token count. Rejecting when this upper bound exceeds the application limit is safe;
acceptance never proves that all counted tokens were visible text.

### Numeric-only post-response failure evidence

`ConversationProviderError` may carry immutable `ConversationUsage` plus three closed booleans:
provider response observed, response completed and service tier verified. An adapter may set them
only after parsing the corresponding provider response. The error still carries no generated text,
response body, prompt, user content, credential, arbitrary provider context or raw reasoning.

The successor atomic evaluation ledger uses schema 3. For a rejected post-response call it may
price exact numeric usage only when input/output/cache details are complete, the response and
service tier are verified, standard-context and output guards hold, and integer pricing arithmetic
does not exceed the prior reservation. Such a call contributes to exact usage/cost totals and may
prove `within_cost_limit=true`, but it remains failed: success count, mandatory sample completion,
`gate_valid` and provider-fit acceptance stay false. Missing or inconsistent evidence retains the
conservative reservation.

Historical V26 ledger snapshots remain schema 2 and omit the new settlement-observation key. The
shared validator accepts either exact frozen schema, while completed successor reports require
their own embedded schema.

`provider_call_observed` in evaluator records means a reserved delegate attempt reached terminal
settlement. It is conservative call/budget evidence and must not be described as proof that a
parsed provider response existed. Exact post-response evidence is the separate typed error flag.

### Attempt isolation and authorization lifecycle

V27 attempt 1 is an immutable archive facade. Its old ID, digest and paths cannot execute current
source. Attempt 2 has a distinct one-shot ID and distinct claim/report/review paths. The runner:

1. rejects malformed authority and the known consumed attempt-1 digest before fingerprint or
   filesystem access;
2. verifies installed-wheel/source parity and the exact digest-bound plan;
3. prepares safe, unoccupied report and review targets and constructs the ledger/report entirely in
   memory before consuming authorization;
4. durably consumes the one-shot claim, then immediately writes an `authorized_preflight` report;
5. performs source recheck, Settings validation and provider construction inside one failure-
   checkpoint lifecycle;
6. binds every paid reservation to one of the exact three session IDs, turn identity and cap vector
   before the provider delegate can run;
7. requires an externally supplied authorized plan digest when validating the later human review.

Inspection is offline. Preparing this runner, its digest and its documentation does not authorize
or execute a provider call.

## Consequences

- The observed 164-token non-reasoning remainder can complete under V27 attempt 2, while 201 still
  fails closed and historical V25/V26 behavior remains reproducible.
- Exact cost evidence no longer disappears merely because the application rejects an already
  parsed response; sample validity remains strictly separate from cost validity.
- No content-bearing provider data is added to domain state, persistence, logs or evaluation
  ledgers, and no new retry, fallback, owner or writer path is introduced.
- A completed three-replica sample and direct human-only all-true review are still required to make
  a V27/provider-fit decision. Offline routing and a larger bound cannot establish Satori's voice.
- Behavior policy v10 remains the last provider-accepted baseline, Checkpoint 14.2 remains open and
  Stage 15 remains locked.
