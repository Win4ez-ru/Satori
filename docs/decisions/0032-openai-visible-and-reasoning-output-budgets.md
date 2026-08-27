# ADR 0032: Separate OpenAI visible and reasoning output budgets

- Status: Accepted
- Date: 2026-08-27
- Supersedes: ADR 0031 only where it maps the provider-neutral output bound directly to
  OpenAI `max_output_tokens`
- Related: ADR 0007, ADR 0008, ADR 0021, ADR 0031

## Context

The first bounded OpenAI production attempts ended `incomplete` with
`reason=max_output_tokens`. An offline request-composition audit then showed that the process-level
`SATORI_CONVERSATION_MAX_OUTPUT_TOKENS=2048` override was not the effective request bound: the
deterministic turn-specific builder selected an application-visible cap of 48 tokens for the
tested fresh achievement turn. The adapter mapped that 48-token cap directly to Responses API
`max_output_tokens`.

OpenAI defines `max_output_tokens` as a combined limit for visible output and reasoning tokens.
The direct mapping therefore allowed hidden reasoning to exhaust a bound that Satori intended to
limit the visible reply. Raising the application-wide cap would weaken the established bounded
conversation contract for every provider and turn type.

## Decision

`ConversationGenerationParameters.max_output_tokens` remains the provider-neutral maximum for
the application-visible reply. The OpenAI adapter derives its wire limit deterministically:

```text
wire_max_output_tokens = visible_output_token_limit + reasoning_token_allowance
```

The allowance is provider-local, startup-validated and bounded to `0..4096` tokens. Its default is
1024. It is added only when OpenAI reasoning is not `none`; with `reasoning=none`, the wire and
visible limits remain identical. The adapter still makes exactly one foreground call, requests no
raw chain-of-thought and does not change provider-neutral request composition.

For reasoning-enabled completed Responses with a positive allowance, the adapter requires the
provider usage breakdown. It subtracts `output_tokens_details.reasoning_tokens` from total
`output_tokens` and rejects the result if the derived visible count exceeds the original
application-visible limit. Missing or internally inconsistent usage fails closed. Total OpenAI
output tokens remain in `ConversationUsage.output_tokens` for billing compatibility; the visible
and reasoning split is transient provider metadata only.

Typed `ProviderExecutionMetrics` may expose the requested visible limit, wire limit, reasoning
token count and derived visible token count. These fields contain no prompt, partial output,
response body or raw reasoning. They may accompany typed provider failures so debug and bounded
evaluation tooling can diagnose exhaustion without weakening the existing fail-closed boundary.
They are not persistent self, are not stored in canonical domain state and add no owner or write
path.

## Consequences

- Satori's turn-specific visible reply bounds remain unchanged for Ollama, Yandex and OpenAI.
- OpenAI reasoning receives an explicit bounded allowance instead of consuming the visible reply
  budget invisibly.
- A completed reasoning-enabled Response without enforceable usage metadata is rejected rather
  than trusted.
- Operators can tune only the provider-local allowance through
  `SATORI_OPENAI_REASONING_TOKEN_ALLOWANCE`; no database migration is required.
- This resolves a transport-budget mismatch, not OpenAI character quality. Paid semantic sampling
  remains a separate explicit authorization gate; Stage 15 and candidate v17 remain locked.
