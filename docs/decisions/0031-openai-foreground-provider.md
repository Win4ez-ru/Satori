# ADR 0031: Optional OpenAI Responses foreground provider

- Status: Accepted
- Date: 2026-08-25
- Supersedes: ADR 0028 only for the closed two-provider selection and Yandex-only wording;
  Yandex remains a supported explicit foreground option
- Related: ADR 0002, ADR 0007, ADR 0008, ADR 0021, ADR 0028, ADR 0030

## Context

Checkpoint 14.2 candidate v16 proved that the application selects the intended transient
character reaction and semantic move, while YandexGPT still rendered all three reviewed dialogue
pairs as generic model-like prose. Further provider-specific behavior-policy patches would risk
turning personality into a growing list of forbidden phrases without proving that the foreground
model can naturally realize Satori's existing typed plan.

The existing `ConversationGenerationPort` already isolates foreground generation from persistent
self and every owner write path. The next bounded experiment should therefore replace only that
cognitive engine and compare provider delivery against the frozen v16 application behavior.

## Decision

`ConversationProviderKind` accepts `openai` as a third explicit foreground option alongside
`ollama` and `yandex_ai_studio`. OpenAI is not an automatic fallback and is not enabled for affect,
relationship, episode, semantic, model, position, reflection or embedding capabilities. Those
background capabilities remain Ollama-only.

The adapter uses the stateless OpenAI Responses API. It preserves system, developer, user and
assistant roles, maps the existing output bound, uses an explicit provider-local reasoning effort
and sets `store=false`. It sends the existing temperature only with `reasoning=none`; for `low` or
higher it omits the incompatible sampling field rather than silently changing the configured
reasoning depth. It does not use provider conversation state,
`previous_response_id`, tools, streaming, raw reasoning output or a second model call. Response
text, status and usage map back to the existing provider-neutral contract and still pass canonical
grounding and self-consistency policy before commit. Failed, cancelled, incomplete and refusal
Responses become typed generation failures; partial or refusal text is never copied into an error
or committed as Satori's canonical reply. For an incomplete Response the adapter may surface only
the allowlisted reason `max_output_tokens`; a missing or unrecognized provider reason is reduced to
`unknown`. It never exposes partial output, the response body or an arbitrary provider string.

The API key is a `SecretStr`, stays transport-local and can be sent only to the canonical
`https://api.openai.com/v1` endpoint. The reusable HTTPS transport uses Bearer authentication and
never places credentials, request text or response bodies in normal logs. `store=false` prevents
creating a retrievable stored Response resource; it is not represented as a broader provider data
retention guarantee.

The initial quality candidate is `gpt-5.6-terra` with `low` reasoning effort. This is a measurable
starting configuration, not a declaration that the model has passed Satori's character gate.
Accepted behavior policy v10 remains the production baseline. Candidate v16 stays confined to the
explicit evaluation runners until the same exact dialogues and rubric are sampled through OpenAI
under a separately authorized paid-call budget. `gpt-5.6-luna` may be compared as a cost control;
`gpt-5.6-sol` is a quality ceiling only if Terra fails or the measured gain justifies its cost.

## Consequences

- Provider replacement still changes no canonical self, memory, relationship, affect, position,
  inclination or personality owner state.
- The operator can switch Ollama, Yandex and OpenAI through configuration and a process restart;
  no database migration is required.
- OpenAI sees the same bounded operation-scoped foreground context as another selected cloud
  provider, which is an explicit remote privacy boundary.
- No quality claim is accepted until a credentialed v16 semantic gate is reviewed by the user.
- Automatic fallback, cloud background routing, provider-side conversation persistence, voice,
  avatar and Stage 15 remain outside this decision.
