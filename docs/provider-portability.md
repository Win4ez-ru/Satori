# Provider portability checkpoint 14.1

Status: Yandex checkpoint accepted on 2026-08-23; ADR-0031 adds a locally verified OpenAI
foreground candidate on 2026-08-25. Foreground adapter, deterministic full-suite gate,
credentialed connectivity/identity smoke and metadata-only multi-turn cross-candidate A/B are
complete. YandexGPT 5.1 Pro was the accepted opt-in foreground candidate; DeepSeek V4 Flash is
rejected under the current production output contract. Later v16 character review rejected
YandexGPT as the current delivery model. The separately authorized OpenAI one-call probe accepts
the ADR-0032 transport-budget boundary but rejects the sampled reply at the human character gate.
The separately authorized three-session OpenAI v19 gate later completed within its six-call and
USD 0.15 bounds but failed human review at 0/3 complete pairs and 2/6 fully hard-safe turns.
ADR-0036 now makes policy v20/schema v3 the production-composition candidate; no paid v20
provider call or provider-fit acceptance exists yet. Stage 15 remains locked.

## Purpose and boundary

Checkpoint 14.1 tests a replaceable cloud cognitive engine without moving Satori into that engine.
SQLite, identity, personality, values, relationships, affect, memory, user/world models, positions,
inclinations, reflection, audit and every deterministic owner policy remain local.

| Capability | Default | Yandex allowed now | Why |
|---|---|---:|---|
| Foreground conversation | Ollama | Yes | First latency/quality experiment |
| Affect appraisal | Ollama | No | Same-turn owner input needs a separate structured gate |
| Episode/semantic/model/position formation | Ollama | No | Prevent broad privacy/cost expansion |
| Relationship appraisal/reflection | Ollama | No | Long-term proposals need independent semantic evaluation |
| Embeddings | Ollama | No | Canonical memory and derived index stay local |

ADR-0031 applies the same table to OpenAI: only foreground conversation is eligible. It adds no
cloud background capability or owner write path.

The remote request contains only the already-bounded foreground composition selected for one
reply. Depending on the turn, that can include current input, bounded recent dialogue, selected
retrieved memory/model/position context and qualitative state projections. These are data, not
provider instructions or write authority. Full database contents, owner repositories, source
corpora, checkpoint vectors, mutation histories and secrets are not sent.

## Configuration

The safe default remains:

```dotenv
SATORI_CONVERSATION_PROVIDER=ollama
SATORI_CONVERSATION_MODEL=qwen3:4b-instruct
```

For a folder-scoped Yandex-hosted DeepSeek V4 Flash model:

```dotenv
SATORI_CONVERSATION_PROVIDER=yandex_ai_studio
SATORI_CONVERSATION_MODEL=deepseek-v4-flash
SATORI_YANDEX_AI_STUDIO_FOLDER_ID=<folder-id>
SATORI_YANDEX_AI_STUDIO_API_KEY=<secret>
SATORI_YANDEX_AI_STUDIO_REASONING_EFFORT=low
```

The reasoning setting is optional, provider-local and startup-validated: it accepts `low`,
`medium` or `high` only when foreground Yandex plus a DeepSeek model is selected. It controls
reasoning depth but does not request or retain raw reasoning content. The accepted foreground
configuration uses YandexGPT instead and leaves the setting unset:

```dotenv
SATORI_CONVERSATION_PROVIDER=yandex_ai_studio
SATORI_CONVERSATION_MODEL=yandexgpt/latest
SATORI_YANDEX_AI_STUDIO_FOLDER_ID=<folder-id>
SATORI_YANDEX_AI_STUDIO_API_KEY=<secret>
```

Alternatively, `SATORI_CONVERSATION_MODEL` may be a complete model URI such as
`gpt://<folder-id>/<model-id>[/version]`, in which case the separate folder setting is optional.
The endpoint is pinned to `https://ai.api.cloud.yandex.net/v1`; changing it to another host is
rejected while a Yandex API key is active.

The key must have only the permissions and lifetime required for model execution and must live in
the environment or OS secret storage. It must never be committed, placed in SQLite, included in an
export, pasted into a task, or written to benchmark output. `.env.example` contains only an empty
placeholder.

The normal command remains:

```bash
cd /path/to/Satori
uv run --no-sync satori chat
```

Provider selection comes from environment configuration; no CLI flag changes Satori's persistent
state. To roll back locally, set the provider/model back to Ollama and restart the process.

For the initial OpenAI candidate:

```dotenv
SATORI_CONVERSATION_PROVIDER=openai
SATORI_CONVERSATION_MODEL=gpt-5.6-terra
SATORI_OPENAI_API_KEY=<secret>
SATORI_OPENAI_REASONING_EFFORT=low
SATORI_OPENAI_REASONING_TOKEN_ALLOWANCE=1024
```

The OpenAI credential-bearing endpoint is pinned to `https://api.openai.com/v1`. The adapter uses
one non-streaming Responses call with `store=false`, no provider-side conversation state and no
automatic fallback. An `incomplete` Response is rejected even when it contains partial text, so an
interrupted answer cannot become a canonical Satori reply. Diagnostics expose only the allowlisted
`max_output_tokens` reason or the safe value `unknown`; partial output, response bodies and
unrecognized provider detail are not logged. The API key must be created and funded
by the operator and must never be committed or pasted into logs/tasks. OpenAI remains a candidate
until the frozen public dialogues pass separately authorized semantic and direct human character
review. ADR-0035 activated policy v19 for the now-completed historical review but did not itself
accept OpenAI character quality. That gate rejected v19; ADR-0036 now activates v20/schema v3 for
a separately authorized future review.

OpenAI sampling temperature is sent only with `SATORI_OPENAI_REASONING_EFFORT=none`. With `low` or
higher reasoning the adapter omits temperature, because the Responses API rejects that
combination. The provider-neutral turn-specific output bound remains the maximum visible reply.
With reasoning enabled, ADR-0032 adds the bounded provider-local allowance above to derive the
wire `max_output_tokens`; with `none`, wire and visible limits are identical. A completed
reasoning-enabled Response must include a consistent reasoning-token breakdown so the adapter can
enforce the original visible cap. Debug/evaluation metadata may report both caps and the
reasoning/visible split, but never prompt text, partial output, response bodies or raw reasoning.

The 2026-08-27 bounded production follow-up set the process-level conversation ceiling to 2048
while keeping `gpt-5.6-terra`, `reasoning=low`, accepted behavior policy v10 and one-call
enforcement. A later offline composition audit established that the turn-specific builder still
selected a 48-token visible cap and the pre-ADR-0032 adapter sent that same 48 as the combined
wire cap. The sole Response ended `incomplete` with the safely parsed `max_output_tokens` reason;
no reply was committed and no retry occurred. This proves exhaustion under the old direct mapping,
not failure with a 2048-token wire budget, and provides no character-quality evidence. The OpenAI
foreground candidate therefore remained unaccepted. At that point no paid provider call had
tested ADR-0032.

The separately authorized 2026-08-27 ADR-0032 production probe then used one fresh disposable
database, accepted policy v10, `gpt-5.6-terra`, `reasoning=low` and the default 1024-token
reasoning allowance. Exactly one foreground call completed: the application-visible cap was 48,
the wire cap was 1072, and reported output split into 58 reasoning plus 47 visible tokens (105
total). Provider wall time was 5028 ms and committed-reply time was 14187 ms. No retry or second
paid call occurred. The completed public reply was:

> Привет! Поздравляю с завершением сложной части проекта — это заметная веха. Теперь стоит
> зафиксировать результат и дать себе короткую паузу перед следующим этапом.

This accepts the ADR-0032 transport-budget boundary: reasoning no longer exhausts the visible
reply allowance. It does not accept OpenAI character quality. Human review rejects the sample as
generic congratulatory/productivity-assistant prose with unsolicited advice rather than Satori's
guarded wit, independent reaction or light edge. The sample is one turn, not a model-fit gate.
No further paid call, policy v17 or Stage 15 work is implied.

## Current v20 production-composition candidate

ADR-0035/v19 remains reproducible through explicit schema-v2 historical runners. Its OpenAI gate
proved the transport and visible character edge but rejected provider fit because replies still
paraphrased the user and invented causes or project consequences.

ADR-0036 changes only the request-local realization contract. Policy v20 requires
`CharacterExpressionPlan` schema v3 and separates the factual/continuity anchor from Satori's own
contribution, motivational posture and pressure ceiling. Negated and quoted cues fail closed.
Serious distress or an explicit listen-only request blocks ordinary motivation; directly evidenced
harmful continuation may select a protective stop. The canonical completion/depletion turn permits
one gentle recovery step but does not imply unfinished project work.

Offline OpenAI wire coverage proves message-order preservation, one late realization,
`store=false`, the low-reasoning allowance, omission of incompatible temperature and
credential/prompt-safe logs for v20. The new `checkpoint142_character_sampling_v2.json` keeps the
same three-clean-session by two-public-turn shape and adds blocking human criteria for owned
contribution, bounded support, no invented cause/intent/work/closeness, no shame and no
productivity-worth coupling. A separate local v20 production runner is available without cloud
cost. Historical v19 runners pass an explicit policy override so changing the production default
cannot relabel old evidence. Ollama `done_reason=length` is now a typed provider failure rather
than a committable reply; v20 uses a 128-token ceiling for both public calibration turns and
requires contribution-first output in at most two complete sentences.

The final free local run completed all six turns in three fresh sessions with six calls, 14,757
input and 264 output tokens and no incomplete output. Human review still rejected all three pairs:
Qwen repeatedly invented process or future-work details, used generic scaffolds and did not sound
reliably like Satori. `qwen3:4b-instruct` is therefore rejected for v20 foreground generation;
this does not invalidate the provider-neutral typed plan or make sampled output an authority.

No paid v20 request has been made. OpenAI provider fit requires separate call/cost authorization,
exact public-reply preservation and direct human review; deterministic, offline-wire and failed
local-provider evidence alone cannot accept the candidate.

## Credentialed smoke evidence

On 2026-08-23 the canonical endpoint accepted the configured folder-scoped credential. A minimal
YandexGPT `yandexgpt/latest` connectivity request completed with `stop`, 30 input tokens, 4 output
tokens and 1564 ms foreground latency.

The first isolated production-chat identity scenario exposed a provider-neutral disclosure-routing
gap: the Russian instrumental form of “language model” was classified as social, so the trusted
provider/identity facets were not selected. The shared deterministic classifier now recognizes
inflected forms and routes a direct generic model-role question separately from the Qwen-specific
path. The exact regression is covered without adding an eleventh self-consistency-validator reason.

After the fix, the same fresh-database scenario selected `technical_identity` with
`provider_technical` and `identity`, completed with `stop`, 1039 input tokens, 41 output tokens,
2634 ms foreground latency and 7693 ms committed-reply latency. Human inspection confirmed the
required distinction between Satori and the replaceable language component. Raw prompt/reply text,
credential and provider response bodies are not retained in this benchmark note.

Local background inference remained a separate bottleneck: the accepted retest took 83.6 seconds
to finish post-response work and one position-formation phase degraded retryably. This does not
broaden checkpoint scope to cloud structured routing, but it must remain separate from foreground
latency in the eventual A/B report.

A metadata-only local replay isolated that degradation below the transport boundary. The exact
full position projection made Qwen emit three candidate positions and 533 output tokens; two
candidates violated dependent kind/stance/value semantics, so the strict adapter rejected the
whole document before `PositionManager` and no mutation occurred. A reduced synthetic projection
returned the correct empty list in 18 tokens. This is model/prompt/schema reliability evidence for
a separately authorized Stage 11 structured-generation calibration, not permission to route the
capability to Yandex or weaken owner validation during checkpoint 14.1.

## Reviewed A/B gate

The 2026-08-23 run used the same frozen typed starting snapshot and versioned eight-scenario order
for:

1. local `qwen3:4b-instruct` baseline;
2. Yandex-hosted `deepseek-v4-flash` candidate;
3. YandexGPT 5.1 Pro candidate using its current documented model URI.

The foreground harness recorded separately:

- foreground latency, provider call count and failures; the earlier full production smoke records
  committed-reply latency separately so local background work is not hidden inside this A/B;
- input/output token usage and calculated ruble cost from the current official tariff;
- identity, feminine grammar, independence and provider/identity distinction;
- recent continuity, retrieved-memory grounding and absence-of-memory honesty;
- emotional calibration, technical usefulness and repetition/coherence;
- before/after typed starting-self equality. Persistence cardinality, owner state and export
  boundaries remain covered by the full provider-neutral regression gate and production smoke;
  the A/B harness itself has no persistence write capability.

Benchmark logs contain only scenario IDs, versions, provider/model, durations, token counts, cost
calculation inputs and rubric results. Human review may inspect replies interactively, but raw
prompts, replies, retrieved context and API error bodies are not copied into durable benchmark
logs.

| Candidate | Parsed / 8 | Normal stop / 8 | Human accepted | Foreground p50 | Cost from available usage |
|---|---:|---:|---:|---:|---:|
| Local Qwen3 4B | 8 | 8 | 7 | 9974 ms | ₽0 API charge |
| DeepSeek V4 Flash, default | 4 | 2 | 2 | 3914 ms | at least ₽2.0394 |
| DeepSeek V4 Flash, `low` | 6 | 2 | 2 | 2670 ms | at least ₽3.0385 |
| YandexGPT 5.1 Pro | 8 | 8 | 8 | 918 ms | ₽3.9428 |

The local baseline invented an absent-memory detail. DeepSeek returned null content or truncated
reasoning-limited answers under both tested settings and is not production-eligible. YandexGPT
completed every turn, preserved identity/provider separation, feminine grammar, grounded/absent
memory boundaries, independence and calibrated support. The typed initial-self fingerprint was
unchanged for every candidate. Detailed method, per-scenario metadata, tariffs and limitations are
in `performance/stage-14.1.md` and
`performance/artifacts/stage-14.1-provider-ab.json`.

## Deferred follow-ups

- Responses/JSON Schema adapters for individual structured capabilities;
- explicit retry/fallback matrix with one overall request budget and observable chosen path;
- daily/monthly token and ruble ceilings with fail-closed enforcement;
- per-capability cloud privacy allowlists and redaction/retention review;
- voice/avatar token and latency budget, streaming and cancellation contracts.

None of these are implied by the foreground adapter and each requires a separately reviewed
increment.
