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
OpenAI remains the selected foreground transport. The ADR-0038/v22 six-call sample is
transport-clean but human-rejected at 0/3 complete pairs and 2/6 fully hard-safe turns. The later
ADR-0039/v23 sample is also transport-clean at six first-attempt calls, but human-rejected at 0/3
complete pairs and 3/6 fully hard-safe turns. Its lean action/evidence/voice/stop block reduces
echoing but does not reliably produce recognizable character or one strictly grounded practical
move. Stage 15 remains locked.

The later V26 attempt-5 run remained transport-clean but failed direct character review. ADR-0043
therefore makes V27 the current offline application-composition candidate; it changes no provider
wire and has made no provider or paid call.

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
reasoning depth but does not request or retain raw reasoning content. The Checkpoint 14.1 accepted
foreground configuration used YandexGPT instead and left the setting unset:

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
accept OpenAI character quality. The completed v19-v23 gates were all rejected by direct human
review. The later v24 `core_emotional` gate was also rejected. The v25 exact-manual gate completed
9/9 first-attempt turns; its recorded token totals give a repository standard-rate estimate of
USD 0.036292, not a cache-detail-verified exact invoice. It proved the typed social/self-disclosure
wire but did not accept character quality. ADR-0042's V26 attempt-5 sample was later rejected by
direct human review. ADR-0043 now defines V27 as the current offline architecture candidate; no
V27 provider-quality evidence exists.

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

## Historical v24 production-composition candidate

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

The separately authorized v20 OpenAI run completed all six public turns but direct user review
rejected event paraphrase and the repeated recovery-advice scaffold. ADR-0037 therefore
historically activated policy v21 with plan schema v4: acknowledgement could be implicit or
omitted, ordinary replies could end naturally, and guarded expression remained a bounded
request-local projection that could not
suppress important help. The separately authorized v21 OpenAI execution completed 6/6
first-attempt foreground calls for USD 0.027692. Transport, usage and completion were clean, but
every pair retained event/state echo and unsupported causal or project context remained. The
report exporter also omitted the two new flow axes and failed local post-sample validation; that
offline defect is fixed and tested without rewriting the original artifact or repeating a paid
call. OpenAI remains the selected foreground transport, while v21 character/provider fit is not
accepted.

ADR-0038 responds to that failure without changing provider or plan state. Policy v22 derives one
pure response act and evidence scope from the existing v4 axes. Its final trusted block no longer
renders the detailed factual anchor that v21 placed immediately after the no-echo instruction.
The target turns permit only Satori's own reaction and reject new user/world claims, causal
theories and consequences. Versioned deterministic, safe-artifact and stateless Responses-wire
coverage is offline-clean. The separately authorized v22 provider sample completed 6/6
first-attempt calls for USD 0.025758, but human review found the intended response-act topology
unstable: the model still recapped the input and replaced direct reaction with generic
normalization or unsupported internal explanation. OpenAI remains the selected transport; v22
character/provider fit is rejected from this sample.

ADR-0039 keeps the OpenAI transport and changes the request-local decision rather than switching
models. Policy v23 uses plan schema v5: ordinary explicit depletion becomes one grounded practical
move with a gentle supportive push, while serious distress and explicit listen-only language keep
quiet-presence precedence. Achievement permits only a brief deictic acknowledgement before one
self-sufficient verdict. The final trusted projection has exactly action, evidence, voice and stop
fields and does not concatenate the historical plan prose. Offline Responses-wire coverage pins
the comparable gate to `gpt-5.6-terra`, `reasoning=medium`, `store=false` and the existing
1024-token reasoning allowance. The separately authorized gate completed six first-attempt calls
for USD 0.024860. Human review rejects all three pairs: two achievement turns repeat an abstract
low-character formula, the third returns to metaphorical recap, and depletion responses drift into
generic/multiple practical instructions or unsupported inference. OpenAI transport remains
selected; v23 character/provider fit is rejected from this sample.

ADR-0040 keeps the transport boundary unchanged and replaces only application composition for
candidate policy v24. The application selects one `CharacterDeliveryDecision` directly from the
completed cognition strategy and existing qualitative affect/relationship projections; it does not
send the legacy expression plan or response-act chain. The decision preserves cognition stance and
uncertainty plus the V2 intent registry, primary intent, ordered tags, required points, complete
forbidden-claim boundary and verbosity. Policies v10 and v19–v23 keep cognition intent/template
registry V1. V24 alone requires intent/template registry V2 with template ID
`satori.cognition.response-substance` and schema 2. Composition fails closed when the exact
registry/template, typed substance or goal/stance/topology is missing or inconsistent.

The remote request contains one cohesive baseline derived from canonical personality and exactly
one late v24 director. The V2 response-substance template is embedded in that same director; the
historical V1 cognition prose is not separately rendered, so it cannot compete with the director.
Relationship and affect may modulate warmth, wit, openness or reserve, but important practical/
technical help remains intact. Ordinary depletion permits at most one optional low-cost grounded
suggestion after presence; explicit listening and serious distress remain presence-only. Cognition
owns protective safety, repetition and clean repair with precedence safety > repetition > repair;
the delivery layer cannot synthesize a conflicting intent.

Offline acceptance was defined by the 32-case character-delivery corpus, the separate four-module
employer-demo contract and stateless OpenAI Responses-wire inspection. The separately authorized
v24 `core_emotional` module then completed 9/9 first-attempt calls across three clean three-turn
sessions for 12,517 input tokens and 502 output tokens, with no retry, incomplete response or
provider error. Their repository standard-rate estimate is USD 0.031058; the artifact did not
retain cache-detail usage, so this is not a cache-verified exact invoice. Human review rejected
the replies for repeated ordered scaffolding, input/state echo and unsupported causal psychology.
This one rejected module cannot accept the
four-module readiness aggregate or v24 provider fit. Non-generation replay may omit transient
decision fields but cannot promote them to provider or state authority. The change added no
provider-side state, output rewrite, model judge, retry, persistent owner or Stage 15 capability.

## Historical v25 production-composition candidate

ADR-0041 keeps the OpenAI transport contract and replaces only application selection/projection for
policy v25. A typed disclosure plan carries social current-affect checks, reciprocal warmth and
direct questions about Satori together with exact disclosure facets, including `interests`.
`DisclosureRequestKind.SATORI_SELF` alone emits `SELF_DISCLOSURE_REQUEST`; reciprocal warmth is a
social `NONE` plan and emits no self-request signal. V25 keeps intent registry V2, requires cognition
template registry/schema V3 and uses `CharacterDeliveryDecision` schema 2 with
`social_connect`/`self_disclose`. The remote request still contains one cohesive canonical character
core and one late director; no legacy plan, phrase bank or output rewrite is introduced.

The historical manual failure after the broad self-disclosure request can be recovered from the
old durable record only as `InvalidProviderResponse`. Its exact cause is unknowable because the
old failed-row schema did not persist a safe provider reason. Migration
`0013_conversation_failure_reason` fixes future observability with a closed provider-neutral reason
enum plus safe provider/model identifiers. It never stores raw error messages, HTTP bodies, prompt
or user text, private provider context, partial output or credentials. Legacy/non-provider failed
rows remain valid with null reason/provider/model.

OpenAI `status=incomplete` with `incomplete_details.reason=max_output_tokens` maps to
`output_token_limit`; absent or unsupported details map to `incomplete_unknown`. The official
OpenAI documentation states that [`max_output_tokens` includes reasoning and visible output and may
produce `incomplete` before visible text](https://developers.openai.com/api/docs/guides/reasoning#allocating-space-for-reasoning).
The adapter therefore continues to discard partial text and fail closed. Other adapters map only
their established transport, terminal and response-contract conditions to the same closed enum.
Neither reason persistence nor the v25 delivery change adds automatic provider retry or fallback.

The later separately authorized immutable v25 exact-manual plan ran three fresh sessions × three
public turns. All 9 calls completed on the first attempt with 13,748 input and 733 output tokens;
no retry, incomplete response or provider failure occurred. The repository standard-rate estimate
is USD 0.036292. Cache-detail usage was not retained, so the estimate is not an exact cache-
verified invoice. The run proved that the foreground adapter received the intended v25 social/
current-affect, reciprocal-warmth and broad
self-disclosure routes and that the historical missing-reply path was fixed.

Provider fit remained unaccepted. The replicas repeatedly announced calm/level affect, explained
the absence of stable hobbies and added polished assistant-like abstractions. ADR-0042 historically
superseded that then-current delivery/projection role while preserving this transport evidence, migration
`0013_conversation_failure_reason` and the explicit relationship-recovery boundary.

## Historical v26 production-composition candidate

V26 does not change the OpenAI, Yandex or Ollama transport contract. Provider choice, credentials,
endpoint pinning, `store=false`, output-budget handling, completion enforcement, failure mapping
and absence of automatic fallback remain exactly as documented above. The change is entirely in
application-side request composition before the replaceable foreground port.

The audit found that v25 discarded live personality/value strength and collapsed affect/
relationship owner reads before passing several overlapping instruction blocks to the adapter.
V26 derives one typed `CharacterPresenceProjection` from those existing owner reads, cognition and
an exact memory-use license plus bounded position/inclination availability. The license requires
both retrieved memory and final `trusted_context` grounding; memory existence or retrieval alone
cannot enable it. Exactly one late presence layer replaces the
historical canonical-character core, standalone affect/relationship blocks and v25 director. The
adapter transports that ordinary provider request without interpreting presence fields or owning
any character state.

This supports provider portability: replacing the foreground model does not replace personality,
values, affect, relationship, memory, positions or inclinations. The manifest exposes only bounded
qualitative signal codes/levels and optional evolution direction; raw state vectors, prompts and
generated prose remain outside portable state authority.

The historical v24/v25 paid execution entrypoints are retired and fail closed before settings,
runtime construction or network I/O; offline inspection, validators and immutable evidence remain
available. The first authorized v26 plan acquired its one-shot claim and failed local settings
validation (`low` effective reasoning versus planned `medium`) before ledger, report, runtime or
provider construction. Its claim is retained and cannot be replayed.

The distinct attempt-2 plan
`sha256:906f250d62d0fbf6087c0ba293808e98b35617fc226a67dccfa5b7c3d10f067d`
with authorization ID `satori.checkpoint142.openai.v26.phase1.attempt2.2026-08-29.one-shot` was
explicitly authorized. It retained its private claim and then failed closed on the first neutral
greeting before the first OpenAI foreground call: local appraisal returned provider-success
`SKIPPED`/`neutral_appraisal_no_delta`, while the old evaluator incorrectly required `APPLIED`.
Its safe report records zero provider/base calls, zero tokens and USD 0, and no review exists. A
free local diagnosis confirms that this owner-approved no-op is correct and prevents fabricated
neutral-event drift; the failure belongs to the evaluator rather than the portable affect owner or
provider adapter. V26 OpenAI paid-call usage remains exactly zero across attempts 1 and 2.

The separately authorized attempt 3 at
`sha256:1db817bba4bd751126a470e59802fa3554807063b6a8f81ecd6b218ce49d7734`
with authorization ID `satori.checkpoint142.openai.v26.phase1.attempt3.2026-08-29.one-shot` is
consumed. A valid neutral local `SKIPPED`/no-transition appraisal preceded exactly one successful
OpenAI call: 1,063 input, 32 output, cache `0/0`, service tier `default` and exact USD 0.002510.
The reply committed before post-commit `NonComparableProviderReply`: canonical `SatoriReply`
retains total usage but not the cache breakdown needed to compare it with the atomic ledger. Its
private `0600` claim/report are immutable; no review or sample digest exists. Human-only review is
5/7, and one of 24 replies makes the overall result `INCONCLUSIVE / NOT ACCEPTED`.

The evaluator-only report-schema-4 correction freezes per-attempt ledger evidence, totals parity,
selected-retry identity and explicit usage provenance without changing the portable provider,
production owner/state or migrations. The separately authorized attempt 4 at
`sha256:e26f2c4a9f86d3ec40006af2ea3ff3c6624cc04fc94829d61ceb8cef3fe474e4`
under `satori.checkpoint142.openai.v26.phase1.attempt4.2026-08-29.one-shot` is consumed. Two base
calls completed without retry: 2,110 input/44 output tokens, cache `0/0` and exact USD 0.004748.
Both replies committed before an evaluator-only safe-manifest check incorrectly required
`self_consistency_facets` while production correctly omits that field when `disclosure_facets` is
empty. Its private `0600` claim/report remain immutable; no completed sample digest, review or
official human rubric exists, so the result is `INCONCLUSIVE / NOT ACCEPTED`, not a provider-fit
verdict.

The corrected evaluator now mirrors the production iff rule, and an exact eight-turn fresh
production `Talk`/composition stub regression passes sanitizer-to-safe validation offline. This
changes no transport, provider adapter, state owner or migration. The final gate is clean at 177/177
source/installed parity. The separately authorized attempt 5 under authorization ID
`satori.checkpoint142.openai.v26.phase1.attempt5.2026-08-29.one-shot` and final frozen digest
`sha256:8f191667e539296266aa4bb8eacbb837559d432d3b623d6f6b5896d250369107`
completed its same 3 × 8/Terra-medium contract on 24/24 base calls without retry. The immutable
report remains `completed_awaiting_human_review` and records 31,836 input tokens, 2,076 output
tokens (454 reasoning and 1,622 visible), cache `0/0`, exact USD 0.088584 and sample digest
`sha256:29b2e14acabc3b9422b410a44a6fa8c00c4780e449e9639157da73b44b62a840`.

The direct human-only review is `accepted=false`: 107/168 per-turn decisions passed, with dimension
totals `G23 O22 S11 N10 L15 C2 Q24`, cross-session `TFTFTF` and attestations `TTT`. Its content
digest is `sha256:6e887ec86c0e23194d4ce46eb7d67e911e9a27dfc827b02dd955c522a55ce92e`; the private `0600` file
hash is `c14aac4c5b314426aa5444404465f6fe7bb021b73349fca2c55883fdc603919b`. This rejects the frozen
then-current V26/Terra composition and sampled delivery, not provider portability and not a proven Terra model
ceiling. Total V26 paid usage through attempt 5 is USD 0.095842.

Offline v9 coverage has 40 public-input scenarios, five controlled owner-state contrasts and two
committed `Talk` flows, but it judges no generated prose. Stage 15 remains locked because
autobiographical state would not repair this application bridge.

## Current v27 production-composition candidate

V27 changes only the application-side composition feeding the same provider-neutral request. The
OpenAI, Yandex and Ollama adapters, provider selection, credential boundaries, endpoint pinning,
`store=false`, completion enforcement, output-budget semantics and absence of automatic fallback
remain unchanged. The current default composition selects policy v27 with decision/presence
schemas 4/2.

Before the provider request is rendered, existing personality strengths/evolution cues, one
contextual value guard, current affect, scoped relationship and narrow request evidence select one
typed movement under the complete cognition-owned truth/substance contract. The adapter sees one
compact operational-move block; it does not interpret character state or gain a write path.
Assistant history used to resolve a direct objection remains untrusted context and never becomes a
canonical position. Topic closure allows only bounded ease/reserve and continuation.

Offline adapter coverage sends the exact composed messages with `store=false`, no tools,
`previous_response_id`, `conversation` or provider state. The wire maximum is the application
visible cap plus only the configured reasoning allowance; service tier and explicit cache options
are preserved. Runtime regressions prove normal one-call completion, exactly one retry after a
validator violation and no third call after a repeated violation. No actual provider request is
made by those tests.

The historical V26 paid entrypoint is now retired before settings, filesystem claim, source
fingerprint, provider construction or network. Retained V26 evidence remains inspectable only
against its embedded frozen plan/source. The V27 production plan is immutable at
`sha256:5e6bcc1fc53100e66990feb25d9448465a1a6bb1364e7b98eb6f14ddb4d94feb` under one-shot ID
`satori.checkpoint142.openai.v27.phase1.2026-08-30.one-shot`: OpenAI `gpt-5.6-terra`, reasoning
`medium`, allowance 1024, three clean sessions × eight fixed turns, 24 required/30 maximum calls,
at most two attempts per turn and USD 0.15. The `/responses` request is stateless, `store=false`,
tool-free and explicit-cache `0/0`; visible/provider caps are 768/1792. No prior V26 authorization
can be reused. Execution awaits the exact V27 authorization; provider calls remain zero and provider
fit remains unaccepted.

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
