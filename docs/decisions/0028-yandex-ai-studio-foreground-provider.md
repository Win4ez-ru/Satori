# ADR 0028: Optional Yandex AI Studio foreground conversation provider

- Status: Accepted
- Date: 2026-08-23
- Supersedes: ADR 0011 only for concrete conversation-provider selection and the remote privacy
  boundary; its provider-neutral roles, bounded context and response-validation decisions remain
  accepted
- Related: ADR 0002, ADR 0007, ADR 0008, ADR 0019, ADR 0021, ADR 0027

## Context

Stage 14 is complete and Stage 15 is locked. The target Mac can run the full local system, but
foreground Ollama generation remains the dominant latency and memory-pressure source. The
provider-neutral `ConversationGenerationPort` already makes the language model replaceable, while
all persistent identity, memory, affect, relationship, position, inclination and personality
owners remain local.

The first cloud increment must test whether a stronger hosted model improves response latency and
quality without silently moving Satori's identity or owner decisions into a vendor, exposing an
API key to an arbitrary endpoint, or multiplying paid calls through hidden retries/fallback.

## Decision

### Separate engineering checkpoint

Checkpoint 14.1 is a provider-portability checkpoint, not Stage 15. It adds no persistence schema,
state family, owner or mutation path. Stage 15 remains locked until a separate user command.

### Foreground-only routing

`ConversationProviderKind` accepts `ollama` and `yandex_ai_studio`. Ollama remains the default and
the explicit local rollback path. In this first increment only the foreground
`ConversationGenerationPort` may use Yandex AI Studio. Episode/semantic/model/position formation,
affective and relationship appraisal, reflection and embeddings remain explicitly Ollama-only.
Configuration rejects a Yandex value for any of those capabilities rather than failing later in
composition.

The cloud call receives the same bounded, operation-scoped conversation request that the existing
foreground adapter receives: trusted policy/application projections and untrusted current,
recent/retrieved text selected for this response. It receives no database, repository, API tool,
checkpoint history, drift budget or write capability. Provider output still passes existing
non-empty/size, grounding, self-consistency and canonical-finalize policies.

### Transport and credentials

The adapter uses Yandex AI Studio's OpenAI-compatible non-streaming Chat Completions endpoint. A
developer role is mapped to a separate system message, as in the Ollama adapter. Temperature,
maximum output tokens, finish status and prompt/completion usage remain provider-neutral fields.
For Yandex-hosted DeepSeek only, an optional startup-validated `low`/`medium`/`high`
`reasoning_effort` is mapped as a provider-local transport field. It is not part of the
provider-neutral conversation port and cannot be set for Ollama or YandexGPT. No raw reasoning
content/output field is requested, stored or logged.

The API key is required only when Yandex is selected, is represented as `SecretStr` at the
configuration boundary and is excluded from object representations. The reusable transport adds
`Authorization: Api-Key ...` internally. To prevent credential exfiltration, both configuration
and transport pin the target to exactly `https://ai.api.cloud.yandex.net/v1`; arbitrary compatible
base URLs are not accepted in this checkpoint. The configured model is either a complete
`gpt://...` URI or a folder-scoped model identifier resolved locally.

HTTP 408/409/425/429 and 5xx plus transport failures map to provider-neutral unavailable errors.
Other 4xx responses map to generation failure. Error messages and normal logs contain provider,
model and status metadata only, never credentials, request text or response bodies.

### No automatic fallback yet

There is no automatic Ollama fallback, hedged request or silent retry in the first increment. An
operator switches the foreground provider explicitly. This keeps latency, semantic variation and
cost attributable during A/B evaluation and prevents a failed paid call from unexpectedly causing
a second expensive or divergent call. A later fallback policy requires its own idempotency,
observability, retry classification and budget gate.

### Acceptance before broader routing

Daemon-free acceptance requires config, credential-target, request mapping, usage, response-schema,
error-mapping, composition and provider-neutral regression tests. A real API key/folder smoke must
then compare at least Yandex-hosted DeepSeek V4 Flash and YandexGPT 5.1 Pro on identical versioned
conversation scenarios, recording output quality, prompt/output tokens, latency and estimated cost
without logging prompt/reply content. Structured/background routing, automatic fallback and budget
automation remain locked until that evidence is reviewed.

The accepted 2026-08-23 evidence selects YandexGPT 5.1 Pro for opt-in foreground use and rejects
DeepSeek V4 Flash under the common 768-token production contract: default and low reasoning runs
returned null content or length-truncated visible replies. Candidate rejection changes no owner,
fallback or routing boundary in this ADR.

## Consequences

- Satori can use a hosted foreground language model without changing persistent self or domain
  ownership.
- The cloud provider sees bounded conversational content; this is an explicit privacy change from
  the local default and must be opt-in.
- Local background inference still consumes Ollama resources, but foreground generation no longer
  competes for the same serialized Ollama slot when Yandex is selected.
- Switching back to `ollama` is configuration-only and requires no state migration.
- Streaming, structured cloud calls, automated fallback and cost enforcement are deferred rather
  than implied by OpenAI API compatibility.
