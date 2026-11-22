# ADR-0101: Explicit output-budget floor for the all-OpenAI candidate

- Status: Accepted for isolated implementation and qualification; not model or release acceptance
- Date: 2026-10-31
- Authority: continued authorized all-role API migration and the reviewed correction of its observed output-limit failure
- Extends ADR-0100; changes only the numerical foreground output limit in the explicitly enabled candidate
- Preserves Character pause, state/evidence owners, incomplete rejection and existing max-one regeneration

## Evidence and limits of diagnosis

The first real exact-production regression committed eight of seventeen fixture turns. Turn9
failed with the closed `output_token_limit` reason. Its observed API usage was56 output tokens.
The canonical failed interaction stores the failure reason, but not the requested output limit;
its normal input/output usage columns are null. No actual request body or partial output was
retained. The existing routine-question-correction branch and deterministic test both specify
a56-token limit. That is strong evidence for the limiting branch, not a captured wire-limit
receipt. Neither API framing overhead nor a minimum number of extra completion tokens was
established. Four background failure phases after the harness stopped are not independent
evidence of four broken adapters.

The incomplete response remains rejected. Its partial text must not become a canonical reply.
The failed sample and the prior r2 source/gate receipts remain immutable historical evidence.

## Changed numerical contract

The earlier mode-specific limits intentionally constrain verbosity. This decision explicitly
changes that numerical contract for the opt-in all-OpenAI candidate; it is not described as a
behavior-neutral HTTP fix. It does not change identity, personality, a prompt, the character
policy text, evidence eligibility or a persistent state schema.

Add a provider-neutral `output_token_limit_floor` to the request builder, default0. Apply it
after all existing per-turn clipping, including repeat and practical-care branches:

```text
effective_limit = min(global_limit, max(configured_floor, historical_turn_limit))
```

The floor is headroom in the maximum allowed output, never a minimum generated reply length.
The explicit all-OpenAI factory selects128. `Settings` permits a positive floor only in the
all-OpenAI mode, rejects nonintegral/negative values and a floor above the global maximum, and
keeps this candidate's global maximum at or below768. The normal local configuration retains0
and its existing numerical limits, including the56-token correction case. Other configured
floor values do not acquire model-quality acceptance from this initial128 candidate.

The builder changes the typed request before transport and budget admission. The Responses
adapter still maps that exact request limit to the physical API limit with reasoning disabled.
There is no hidden adapter allowance, partial-output acceptance, automatic limit increase or
additional retry. The same larger bound is conservatively reserved before paid dispatch;
unknown prices, usage and outcomes retain their existing refusal/reconciliation semantics.

## Preserved behavior and observability

Existing brevity, relevance and sentence-shape instructions remain byte-identical; validators,
response-character bounds, grounding, source admission and the shared max-one regeneration
path remain unchanged. Increasing numerical headroom can nevertheless alter sampled length
or style.128 is a candidate value, not a proven minimum or a guarantee of completion.

The regression HTTP observer records only the actual requested/effective wire output cap,
closed response status and allowlisted incomplete reason. A pre-floor heuristic cannot be
reconstructed at that boundary and is not invented. No prompt, retrieved context, partial
output, raw reasoning or new canonical database column is added for this observation.

## Qualification

Tests must preserve the local56-token case, prove the explicit128 factory/composition path,
reject a floor above its global bound and all-OpenAI globals above768, show unchanged prompt
messages, verify the real budget owner reserves against the actual API cap, and demonstrate
that an incomplete response still produces one failed canonical interaction without a reply
or an added retry. Run the complete fresh Foundation gate after the candidate freeze.

Repeat applicable actual-provider regressions on fresh synthetic installations and keep every
failure. Lexical counts do not establish semantic acceptance, and the existing missing exact
four-turn golden remains unrun until its source sequence is recovered. No phrase rewriting,
prompt tuning, extra model judge or character-state change may be used to manufacture a pass.
