# Project progress

Last updated: 2026-08-27

## Current status

**Checkpoint 14.2 — Grounded Natural Dialogue Calibration remains the active accepted boundary.
Behavior policy v10 is the last provider-accepted baseline. Post-acceptance candidates v11 through
v16 were rejected by bounded production semantic gates. On 2026-08-27 the user explicitly
authorized the next character-visibility implementation. ADR-0033 introduced candidate v17, whose
three-session local production sample was rejected at 0/3 complete pairs for truncation, an
invented recollection and unnatural metaphors. ADR-0034 candidate v18 preserved grounding and
completed all six local replies but was rejected at 0/3 complete pairs for copied semantic wording
and generic repetition. ADR-0035 now accepts candidate policy v19 for production composition while
keeping `CharacterExpressionPlan` immutable, request-local and schema v2. One final realization
after the invariant/mode contract renders all eight axes; duplicate ready-made
achievement/depletion wording and the zero-humor depletion wit conflict are removed. Fresh safe
turns can show a visible soft edge, and only an explicit request or explicitly pending safe
project-hygiene step can license one concrete next move. Generic and vulnerability advice remain
blocked. The complete plan is exposed only through transient manifest fields, a retry preserves
the same realization, and the canonical personality source, persistent owners, ten-reason
validator and max-one retry remain unchanged. Offline OpenAI wire coverage and a versioned 3 × 2
human-review fixture/runner are implemented. No paid v19 provider call has been made, provider fit
remains pending separate authorization and human review. A free three-session local v19 production
smoke completed 6/6 turns on first attempts and exposed every intended typed axis. A final audit
then removed unsupported difficulty from generic completion, made the practical-step license
fail-closed for completed/negated/hypothetical/unrelated actions and placed retry correction before
the unchanged final realization. The required post-audit 3 × 2 local rerun also completed 6/6
first-attempt turns (13,084 input + 337 output) and again rejected the 4B provider at 0/3 pairs:
achievement remained generic/metaphorical or invented detail, while depletion introduced general
rules and speculative causes. Both immutable results are recorded without prompt/private context
and are not being tuned into scripted phrases. Stage 15 remains locked pending a separate user
command. The rebuilt-wheel Foundation
gate is clean at `1198 passed, 4 skipped`;
Ruff format/check, mypy on 266 files, migration head, default bootstrap, isolated clean bootstrap,
`git diff --check` and repository marker checks all pass.**

On 2026-08-25 the user authorized replacing only the rejected Yandex foreground delivery engine
with OpenAI. ADR-0031 adds a credential-pinned, stateless Responses API foreground adapter while
keeping Ollama and Yandex explicitly selectable and every background capability/local owner
unchanged. The initial candidate is `gpt-5.6-terra` with low reasoning, `store=false`, no tools,
streaming, provider conversation state, automatic fallback or hidden retry. Deterministic adapter,
transport, configuration and composition tests are complete; paid v16 OpenAI sampling remains a
separate explicit authorization gate and no character-quality acceptance is claimed yet.
The 2026-08-25 architecture audit restored accepted behavior policy v10 as the production
composition default; rejected candidate v16 remains available only to explicit evaluation
runners. The same audit made valid but incomplete OpenAI Responses fail closed instead of
committing partial text, verified credential/body-safe transport cleanup and made explicit test
runtimes independent from an operator's local `.env`. The complete isolated rebuilt-wheel gate is
clean at `1135 passed, 4 skipped`; Ruff and mypy pass, migration head and clean bootstrap pass, and
no OpenAI request or paid usage occurred.

On 2026-08-27 the operator funded the OpenAI API key. Two context-free connectivity probes with
`reasoning=none` completed through `gpt-5.6-terra` (91 input and 32 output tokens total), proving
credential, balance, model access and the canonical Responses endpoint. The first real
`reasoning=low` probe exposed that OpenAI rejects `temperature` together with reasoning. The
adapter now preserves temperature only for `none` and omits that incompatible sampling field for
`low` or higher without changing the configured reasoning depth. Targeted provider/configuration
coverage passes `55/55`; the rebuilt Foundation gate is clean at `1136 passed, 4 skipped`, with
Ruff, mypy, migrations and isolated bootstrap clean.

The user then authorized exactly two bounded production attempts with accepted behavior policy
v10 and a fresh disposable database. The first used the configured `low` reasoning on the first
public project-completion turn; the second used a process-local `none` override on the combined
completion/depletion contrast. Both Responses ended `incomplete`, so fail-closed policy committed
no assistant reply and the disposable databases were removed. The adapter deliberately exposes
neither partial text nor provider response bodies, and usage was therefore unavailable for these
failed attempts; no exact cost claim is made. This evidence does not accept OpenAI character
quality. A separately authorized bounded follow-up must distinguish output-budget exhaustion from
other safe incomplete reasons before normal OpenAI production chat is claimed ready. Stage 15
remains locked. Candidate v17 began only later under the separate authorization recorded above.

The authorized 2026-08-27 follow-up now parses `incomplete_details` without weakening the
fail-closed boundary: only `max_output_tokens` is surfaced and missing or unrecognized detail is
reduced to `unknown`; partial output, response bodies and arbitrary provider strings remain
unexposed. Targeted OpenAI/configuration coverage passes `57/57`; the rebuilt Foundation gate is
clean at `1138 passed, 4 skipped`, with Ruff, mypy, migration head, default bootstrap and isolated
clean bootstrap all passing. One fresh production turn used accepted behavior policy v10,
`gpt-5.6-terra`, configured `low` reasoning, a disposable database and the process-level
`SATORI_CONVERSATION_MAX_OUTPUT_TOKENS=2048` ceiling. Its single provider attempt again ended
`incomplete`, now conclusively
reported as `reason=max_output_tokens`; no assistant reply was committed, no second call was made
and the disposable database was removed. Because the Responses output limit includes both visible
answer and reasoning tokens, this candidate configuration still had no human-reviewable Satori
reply and was not production-accepted. A later offline production-composition audit established
that the turn-specific builder selected 48 visible tokens for this fresh achievement turn and the
old adapter sent the same 48 as the combined wire cap; the provider did not receive 2048. No
additional paid sampling or Stage 15 work was started in that task; candidate v17 began only under
the later separate authorization recorded above.

ADR-0032 now separates that application-visible turn cap from OpenAI's combined wire cap. With
reasoning enabled, the adapter adds a startup-validated provider-local allowance (default 1024,
bounded to 4096), parses the safe reasoning/visible usage split and rejects a completed Response
when the original visible cap cannot be enforced or is exceeded. `reasoning=none` keeps identical
visible and wire limits. Typed metadata-only metrics cover both limits and the token split on
success and safe typed failures; no prompt, partial reply, response body or raw reasoning is
stored or logged. The rebuilt offline gate is clean at `1146 passed, 4 skipped`; Ruff format/check
and mypy on 260 files pass, migration head plus default and isolated clean bootstrap pass.

The user then separately authorized one ADR-0032 production test. A dedicated fail-before-network
wrapper enforced exactly one OpenAI foreground call in one fresh production-composition session;
no retry occurred. With accepted policy v10, `gpt-5.6-terra`, `reasoning=low` and allowance 1024,
the application-visible cap was 48 and the wire cap was 1072. The completed Response reported
1487 input tokens and 105 total output tokens: 58 reasoning plus 47 visible. Provider wall time was
5028 ms and committed-reply time was 14187 ms. The transport-budget gate passes. Human character
review rejects the sole reply as generic congratulation plus unsolicited productivity advice,
without recognizable Satori wit or independent edge; OpenAI character quality remains unaccepted.
The evaluator was corrected to compare visible rather than total output against the application
cap. The final rebuilt-wheel gate is clean at `1150 passed, 4 skipped`; Ruff format/check and mypy
on 262 files pass, migration head plus default and isolated clean bootstrap pass. One transient
installed-wheel file-layout failure was repaired by force-reinstalling the same local wheel without
dependencies; no source or dependency changed. No second paid call, Stage 15 or candidate v17 work
was started.

Stages 0–8 and engineering checkpoints 7.5–8.1 are accepted and complete. ADR-0021 records the
Stage 8.1 request-composition architecture and its accepted 2026-08-22 calibration refinements.

The user separately authorized Stage 9 on 2026-08-22. ADR-0022 fixes the separate
UserModelManager/WorldModelManager ownership, closed v1 vocabulary, canonical user-message
provenance, counterparty isolation, correction/conflict and deterministic freshness/expiry policy.
The implementation, lifecycle acceptance and local inspection/export surfaces are complete.
The user separately authorized Stage 10 on 2026-08-22. ADR-0023 fixes a transient typed cognition
pipeline, deterministic V1 planning, explicit conservative fallback, existing EmotionManager
handoff, strategy/position invariants and no additional foreground provider call. Implementation
and acceptance are complete. The user separately authorized Stage 11 on 2026-08-22. ADR-0024 fixes
identity-global PositionManager ownership, anti-mirroring evidence thresholds, explicit
revision/competition semantics and the absence of provider-created facts without verified sources.
The implementation, migration, context/grounding integration, local inspection/export surfaces,
deterministic acceptance and sampled real-Ollama lifecycle are complete.
The user separately authorized Stage 12 on 2026-08-22. ADR-0025 fixes deterministic rare triggers
and cost caps, an immutable canonical source set, strict run/attempt lifecycle, structural cycle
prevention, per-proposal owner transactions and the V1 boundary where only `PositionManager` may
accept a mutation. The implementation, runtime/CLI integration and acceptance are complete;
the user separately authorized Stage 13 on 2026-08-22. ADR-0026 fixes a separate
`SatoriInclination` aggregate under `PositionManager`, Reflection V2 affect attachments,
anti-mirroring evidence diversity, deterministic bounds/cooldowns/decay and bounded current-turn
use. Architecture and acceptance contracts are recorded in `inclinations.md`; implementation,
persistence, replay/restart behavior, local inspection and acceptance are complete. Stage 14
was separately authorized by the user on 2026-08-23. ADR-0027 fixes a dedicated Reflection V3
purpose, ninety-day independent evidence gate, exact tiny owner delta, endpoint/path/checkpoint
budgets, append-only checkpoint/restore and qualitative Expression Projection V2. The only enabled
post-activation trait mutation path is the accepted `PersonalityManager` owner transaction; values
remain immutable and Stage 15 remains locked pending a separate user command.

The user separately authorized provider-portability checkpoint 14.1 on 2026-08-23. ADR-0028 keeps
Ollama as the default and permits Yandex AI Studio only for foreground conversation through the
existing provider-neutral port. Persistent state and all owner/background capabilities remain
local; credentials are pinned to the canonical Yandex endpoint; automatic fallback, structured
cloud routing and Stage 15 remain outside the authorized increment.

The versioned eight-scenario A/B retained the same typed starting-self fingerprint for local Qwen,
DeepSeek and YandexGPT. YandexGPT completed 8/8 normal responses with 918 ms foreground p50 and
8/8 human acceptance at ₽3.9428 measured usage cost. Local Qwen completed 8/8 but invented one
absent-memory detail. DeepSeek completed only 2/8 normally at both default and low reasoning; null
content and visible length truncation reject it under the common 768-token contract. Full evidence
is in `docs/performance/stage-14.1.md`; no structured routing or automatic fallback was opened.

The user explicitly redirected work away from Stage 15 and authorized discussion plus small
follow-ups on 2026-08-23. Checkpoint 14.2 keeps the exact ten-reason Stage 8.1 validator and every
persistent owner unchanged. It may revise only provider-facing behavior policy/context projection
and add evaluation evidence for grounded absence, response specificity and natural expression of
the existing typed digital affect/mood. “Human-like” means natural conversational expression, not
a claim of biological physiology or proven human subjective experience.

Checkpoint 14.2 was accepted on 2026-08-24. Policy v10, the versioned eight-scenario corpus and
YandexGPT metadata-only sampling passed the grounded-absence, concrete-response and natural-affect
contracts. Human review, not the initial lexical score, drove two narrow follow-ups. The final
single-run real-Ollama regression completed 97/97 public-fixture replies and 99 calls with exactly
two successful max-one retries, 70/70 required facets, no incomplete output, no affect-expression
contradiction and an affirmative canonical memory+affect recovery. Full evidence and residual
limits are in `docs/performance/stage-14.2.md`. Zero open-domain hallucinations are not claimed.

The first post-acceptance operational follow-up was completed on 2026-08-24 without reopening the
checkpoint or Stage 15. Interactive `/status` now reports the effective foreground provider/model
from validated runtime settings, without endpoint or credential values. This removes ambiguity
when local `.env` overrides switch between Ollama and Yandex AI Studio; it changes no routing,
prompt, state or ownership contract. The local development `.env` was then normalized to one
foreground selection, `yandex_ai_studio/yandexgpt/latest`; the Ollama base URL and all local
background capability settings remain intact. A no-generation `satori chat` `/status` smoke
confirmed the effective selection. The rebuilt-wheel Foundation gate remains clean at
`1064 passed, 4 skipped`.

The next operational follow-up removed an Ollama-specific user-facing outage message from the
provider-neutral interactive boundary. A Yandex timeout/rate-limit/5xx now reports only that the
foreground conversation provider is temporarily unavailable; exact provider/model/error metadata
remains available in explicit debug output. Routing, fallback, retry, cost and persistence behavior
are unchanged. The added Yandex regression raises the clean rebuilt-wheel suite to
`1065 passed, 4 skipped`.

The workspace runtime follow-up then found and repaired a stale non-editable wheel in the default
project `.venv`; it exposed only the pre-checkpoint `ollama` provider enum despite the accepted
Yandex configuration. The exact Foundation rebuild command reinstalled current `satori-core` into
the user-facing environment. A no-generation run of the documented
`uv run --no-sync satori chat` command reported `yandex_ai_studio/yandexgpt/latest`, and the
installed-wheel interactive suite passed `30/30`. No provider request, paid token usage, database
migration or canonical-state mutation occurred.

The next observability follow-up reused existing provider-neutral `SatoriReply` metadata rather
than adding state or a vendor-specific interface. Successful `satori chat --debug` turns now show
foreground provider/model, finish status, available input/output token counts and replay status.
The line contains no prompt, reply, endpoint or credential data, and runtime intentionally does
not hard-code a ruble price because provider tariffs are mutable external facts. The rebuilt
working-wheel Foundation gate remains clean at `1065 passed, 4 skipped`; no paid provider call was
needed for this deterministic change.

A precision follow-up then verified that `SatoriReply.usage` belongs only to the selected response
when the shared max-one consistency retry runs; the first attempt is not aggregated into that
field. Debug labels now say `selected_input_tokens`/`selected_output_tokens` and report one or two
`provider_attempts`, so selected usage cannot be mistaken for total retry spend. A dedicated
two-attempt Yandex-shaped regression proves the distinction. Full cost aggregation and enforcement
remain deferred rather than inferred from incomplete metadata. The clean rebuilt-wheel suite is
now `1066 passed, 4 skipped`.

The interactive-surface follow-up made ordinary `satori chat` startup, status, session rotation,
shutdown and recoverable error messages consistently Russian while preserving stable English
debug labels/metadata fields. A no-generation installed-wheel smoke covered `/help`, `/status`,
`/new` and `/exit` with the configured Yandex foreground and changed no conversation content or
canonical state. The full rebuilt-wheel gate remains `1066 passed, 4 skipped`; no provider cost
was incurred.

The cloud-boundary transparency follow-up now prints the effective foreground provider/model at
interactive startup, before the first `Ты:` prompt, instead of requiring the user to discover it
through `/status`. The same provider-neutral line appears for Ollama and Yandex and contains no
endpoint or credential value. An installed-wheel no-generation smoke confirmed
`yandex_ai_studio/yandexgpt/latest` before user input; the complete gate remains
`1066 passed, 4 skipped` with zero provider cost.

The command-discoverability follow-up replaced the bare `/help` command list with concise Russian
descriptions of `/help`, `/status`, `/new`, `/exit` and `/quit`. It explicitly tells the operator
that `/status` exposes session/background/provider state and preserves the existing rule that only
an exact command line is intercepted; command-like text inside a normal message still reaches the
conversation boundary. Installed-wheel `/help`/`/exit` smoke and the full `1066 passed, 4 skipped`
gate completed without a provider call.

The background-status accuracy follow-up now reports queued and already in-flight post-response
memory work as one transient `ожидают завершения` count. The single worker therefore no longer
makes an active episode/memory task appear as zero pending after it removes that task from the
queue. A deterministic blocking-worker regression covers the transition; no persistent state,
provider routing, memory ownership or Stage 15 scope changed.

The process-scope clarity follow-up now labels `/status` background pending/failure counters as
belonging to the current `satori chat` launch. This prevents the current session ID shown above
them from implying that counters reset on `/new`; queued work and completed failures intentionally
remain runtime-wide until the process exits. The change is wording-only and preserves the serial
queue, graceful drain, canonical delivery and retry contracts. An isolated installed-wheel
`/status`/`/exit` smoke and the full `1066 passed, 4 skipped` gate completed without a provider
request or paid token usage.

The `/new` responsiveness follow-up moves only its synchronous close/start database transitions
to `asyncio.to_thread`. This prevents SQLite lock waiting from stopping the event loop while the
serial post-response worker is awaiting provider I/O, while preserving the exact close-old then
start-new order and the existing final graceful drain. A deterministic delayed-close regression
proves that the transition runs off the event-loop thread; persistence contracts and Stage 15 are
unchanged. The rebuilt-wheel Foundation gate is clean at `1067 passed, 4 skipped`.
An isolated installed-wheel `/new` → `/status` → `/exit` smoke confirmed the rotated session ID
and graceful close without a foreground provider request or paid token usage.

The dialogue-humanity follow-up reproduced a real two-turn Yandex production failure before code
changes. The first reply used implicit masculine grammar (`Рад за тебя`) that escaped the existing
validator because the pronoun was omitted; the second classified explicit exhaustion as
information and produced a generic therapeutic paraphrase. Direct eight-scenario Yandex sampling
also remained formally grounded while showing copied affect wording and generic unsolicited
advice. Candidate behavior policy v11 preserves v10 grounding, extends the existing
`masculine_self_reference` reason to the observed implicit form, recognizes bounded exhaustion
cues in deterministic cognition and makes `listen_and_reflect` explicitly suppress advice and
therapy-style normalization without a request. Targeted tests and the full rebuilt-wheel gate are
clean at `1071 passed, 4 skipped`. The pre/post direct Yandex samples cost ₽3.5588/₽3.6168; the
two-turn pre-change production reproduction cost ₽1.1472, for ₽8.3228 total new evidence spend.
The user then authorized the exact pair for three clean Yandex production sessions with a ceiling
of nine calls and ₽6. All three sessions reproduced the same failure: the first draft triggered
`masculine_self_reference`, but the sole retry again returned and committed `Рад за тебя`; the
exhaustion turn correctly selected `listen` yet answered with generic normalization (`Понимаю,
такое бывает` / `это нормально`). The run used exactly 9/9 calls. Selected usage cost ₽3.5412;
the conservative total including three discarded drafts is ₽5.3316, within budget. Policy v11 is
therefore rejected as the final humanity follow-up, while its grounding and no-advice improvements
remain useful evidence.
The Stage 8.1 v11 regression completed 97/97 sampled public turns with 98 provider calls, one
successful `near_duplicate_after_dialogue_change` retry, 70/70 required facets, no incomplete
output and no affect-expression contradiction. A later provider-metadata audit found that this
artifact used the configured Yandex foreground rather than local Qwen: 182610 input plus 2046
output tokens, approximately ₽73.8624 at the evaluation tariff. That earlier spend was omitted
from the original report and is now recorded explicitly; the immutable artifact was not rewritten.

Candidate policy v12 responds only to those observed failures. Completed project/work/task/phase
news receives a bounded current-turn achievement instruction that avoids narrating Satori's own
gladness; the existing masculine retry explicitly forbids both `рад` and `рада` and requests
neutral wording; `listen` rejects the observed `Понимаю`/`такое бывает`/`это нормально` family and
targets the contrast between completion and absent joy. It adds no validator reason, retry, state,
owner or output rewrite. The rebuilt-wheel Foundation gate is clean at `1073 passed, 4 skipped`.

The required v12 real-Ollama regression then ran with explicit process-local overrides
`ollama/qwen3:4b-instruct`, leaving the production `.env` unchanged. It completed 97/97 selected
turns with 99 local calls, 70/70 required facets, two successful bounded retries
(`near_duplicate_after_dialogue_change` and `affect_blanket_denial`) and no affect-expression
contradiction. One coherence reply selected `finish_status=length` at its 112-token mode cap, so
the sampled output-completion gate is not fully clean. This is retained as Qwen evidence and does
not authorize another retry, output rewrite or an unrelated token-limit redesign.

The user then separately authorized the same exact pair in three fresh v12 Yandex sessions with
the same nine-call and ₽6 ceilings. All six selected turns completed on their first call, using
8556 input plus 105 output tokens: 6/9 calls and ₽3.4644. Masculine self-reference was gone and
the project stayed concrete, but human review still rejected v12: two achievement replies used
the top-down `Молодец`; all three exhaustion replies explained or normalized the state instead of
briefly staying with it; one said `Понимаю`, and one added unsolicited advice to rest.

Candidate policy v13 therefore keeps the same owners, ten validator reasons and max-one retry, but
adds an equal-adult achievement stance and a one-short-tentative-observation contract for typed
`listen`. It explicitly excludes general explanation, normalization, advice and next-step offers
on that narrow turn class. Temperature is deterministically zero only for completed-achievement
and listen-before-advice turns to reduce sampled variance without changing the provider contract.
The rebuilt-wheel gate remains `1073 passed, 4 skipped`; separate paid v13 evidence has not been
authorized.

The mandatory v13 Stage 8.1 local semantic coverage completed across three versioned artifacts
after one recorded Ollama timeout interrupted the main run at 92 selected turns. The exact timed-
out damaged-relationship probe passed independently, followed by the missing mixed-facet and
canonical-history tail. The combined required corpus is 97 turns and 99 local calls: two
successful max-one retries (`near_duplicate_after_dialogue_change` and `affect_blanket_denial`),
70/70 required facets, 97/97 selected `stop` finishes, no incomplete output, no affect-expression
contradiction, no feminine-grammar regression and no self-contradiction. This is distributed
semantic evidence, not a false claim of one uninterrupted clean run; Yandex v13 remains untested.

The separately authorized v13 Yandex production gate then used three clean sessions and the exact
two-turn pair: 6/9 calls, 8763 input plus 75 output tokens, ₽3.5352. All sessions returned the same
acceptable equal-adult achievement reply, then the same model-like exhaustion reply: a generic
rule that difficult completion can be tiring followed by a literal restatement of fatigue. Human
review rejects v13 3/3. Candidate v14 replaces state-labeling guidance with one in-conversation
meaningful synthesis and restores bounded `0.2` sampling only for typed `listen`; no owner, state,
reason, retry or rewriting changed. Its rebuilt-wheel suite is `1073 passed, 4 skipped`. A single
uninterrupted real-Ollama run completed 97/97 turns and 99 calls with two successful retries,
70/70 facets, all selected finishes `stop`, no incomplete output and no affect contradiction.
Paid v14 production evidence remains separately gated.

The separately authorized v15 target-provider gate then used the exact pair in three clean Yandex
production sessions: six first-attempt calls, 9108 input plus 39 output tokens and ₽3.6588 of the
₽6 ceiling. All achievement replies were the identical generic acknowledgement `Здорово, что тебе
удалось справиться!`; all three exhaustion replies reduced `quiet_open_care` to `Понимаю, как
тебе ...` with an interchangeable difficulty adjective. Human review rejects v15 because neither
register expressed a recognizable independent Satori reaction or noticed the completion/absent-
joy contrast. Since the target-provider semantic gate was unacceptable, the conditional full
Stage 8.1 regression was not run.

The user then explicitly authorized candidate v16 on 2026-08-25 and requested that every future
sampled Satori reply be shown verbatim for direct character review. V16 introduces only a typed
request-local owned reaction and semantic move, renders positive guidance for every selected
character dimension, separates the exact canonical completion/depletion contrast from unrelated
vulnerability, uses natural `вспомнила`/`был похожий разговор` memory wording and acknowledges
repetition without a stock reply. ADR-0030 permits qualitative fresh/developing/established
modulation on ordinary turns while keeping damaged guardedness relationship-relevant. Numeric
initiative percentages and out-of-band contact remain unimplemented. Local deterministic work is
complete: format/lint, mypy on 254 source files, `1100 passed, 4 skipped`, clean migration through
`0012_personality_evolution` and isolated bootstrap all pass. The non-editable sync exposed a
missing generated `satori` launcher despite correct wheel entry-point metadata; reinstalling the
same local wheel without dependencies restored the script and the required `satori bootstrap`
smoke passed. No provider call or paid token usage occurred.

The later separately authorized v16 Yandex gate used a dedicated production-composition runner
with three disposable fresh databases, six reserved base turns, a shared nine-call preflight cap
and a conservative ₽6 guard. It completed 6/6 turns on first attempts: 11,207 input plus 199 output
tokens and ₽4.5624. Human review rejected v16 at 0/3 complete pairs. The provider received the
intended v2 plan codes but produced generic praise/explanation, the non-human `Рад(а)` placeholder,
an explicit meta-description of irony and one unsupported disappointment inference. No shared
memory, intimacy, advice or forced-question violation appeared. The conditional full Stage 8.1
regression was not run after this minimized target-provider failure. The runner follow-up raises
the clean rebuilt-wheel suite to `1105 passed, 4 skipped`; no further paid call is authorized.

## Stage 0–3 foundation

- [x] Product constitution, modular-monolith architecture, ownership, trust, memory/cognition and evaluation specifications.
- [x] Python 3.12 typed foundation with `uv`, structured tracing, capability ports, SQLite/SQLAlchemy/Alembic and Unit of Work.
- [x] Explicit singleton activation with stable identity, canonical versioned seed, persistent personality/values/provenance and atomic audit.
- [x] Frozen `InitialSelfSnapshot`; bootstrap/read/migration never auto-activate or reseed.
- [x] Provider-neutral stateless generation slice with bounded trusted character projection, local Ollama adapter and metadata-only logs.
- [x] ADR-0001…0011 accepted and Stage 1–3 quality/restart/migration suites passing before Stage 4 work.

## Stage 4 deliverables

- [x] ADR-0012 resolves exact local plaintext retention, session lifecycle, canonical/derived transaction split, grounding and idempotency.
- [x] Stable explicit sessions plus implicit one-turn sessions; status/started/ended times use injected clock and durable IDs.
- [x] `client_request_id`-keyed pending/failed/completed interactions with retry-safe canonical reply reuse.
- [x] Exact append-only user/assistant messages; system/developer prompts and serialized character context never enter history.
- [x] Pending user intake commits before provider inference; assistant message, provider metadata, completed status and implicit close finalize atomically before delivery.
- [x] Provider/validation failures retain retryable failed intake; finalize failure exposes no reply and leaves no completed half-pair.
- [x] Separate provider-neutral `StructuredGenerationPort` episode formation with Ollama JSON-schema adapter and deterministic fakes.
- [x] `MemoryManager` create/skip/reject policy with bounded summary/scores, importance threshold, exact quote/source validation and assistant-output exclusion.
- [x] Durable episodic memory, exact evidence, formation method/version, terminal decision and audit committed atomically.
- [x] Source/version idempotency prevents duplicate history, decisions, evidence and episodes; formation failure remains retryable.
- [x] `ResponseGroundingGate` rejects provider-declared past claims without evidence included in generation context.
- [x] No recent-session context, memory injection, semantic facts, embeddings, vector index or retrieval.
- [x] `satori session start|close`, persistent `talk --session/--request-id`, `history` and explicit debug `memories` CLI surfaces.
- [x] Session/history/interaction/memory observability contains IDs, versions, reason/error types and counts without conversation content.

## Stage 5 deliverables

- [x] ADR-0013 fixes provider-neutral embedding space identity, exact SQLite scan, ranking v1,
  threshold and context-budget defaults.
- [x] Ollama `/api/embed` adapter uses batches, explicit 768 dimensions, truncation disabled and
  typed transport/schema failures; deterministic fakes keep CI daemon-free.
- [x] `episodic_memory_embeddings` stores only rebuildable vector state keyed by canonical memory
  and exact provider/model/dimension/input-schema space.
- [x] Post-commit episode indexing is a separate derived transaction; failure preserves both the
  completed conversation and canonical episode.
- [x] Idempotent missing-index backfill and active-space rebuild are exposed by
  `satori memories index|rebuild`.
- [x] Typed current-input query, prior/current-source eligibility, exact cosine candidate scan,
  `0.55` threshold and deterministic `0.80/0.10/0.10` relevance/importance/recency rank.
- [x] Top 32 candidate pool, top 4 selection, exact-summary de-duplication and 2400-character
  canonical memory-payload budget.
- [x] Separate untrusted memory context envelope, no-result/unavailable boundary and retrieved
  memory IDs persisted in the interaction context manifest.
- [x] `ResponseGroundingGate` accepts only provider-declared past claims citing supplied memory
  IDs; current interaction is explicitly excluded.
- [x] Retrieval/index failure degrades to no memory; logs expose status, space, counts, selected
  IDs, scores and latency without query, summary, evidence text or vectors.
- [x] `satori memories search` prints score components and no-result status for local debugging.
- [x] Direct/paraphrase/distractor/no-result eval fixtures, restart golden, space isolation,
  rebuild equivalence, outage and poisoned-memory tests pass.

## Stage 6 deliverables

- [x] ADR-0014 fixes a separate `SemanticMemoryManager`, closed user-subject predicate registry,
  structured claim identity, epistemic kinds, confidence v1 and correction/conflict policy.
- [x] Typed text/number/boolean values, explicit polarity, validity intervals and
  active/superseded/disputed/retracted lifecycle preserve meaning without a generic graph.
- [x] Full `claim → semantic evidence → episode → memory evidence → user message → interaction`
  lineage; assistant output and retrieved repetition cannot become evidence.
- [x] `explicit_fact`, `inferred_fact`, `hypothesis` and `attributed_statement` remain distinct;
  inference/hypothesis need at least two independent root messages from two interactions and yield
  to stronger evidence.
- [x] Deterministic confidence caps (`0.90…0.96`, `0.85…0.91`, `0.65…0.79`, `0.50…0.65`) use only unique root
  evidence; provider confidence is an upper input and replay cannot inflate it.
- [x] Exact structured identity merges only new roots; single-valued corrections supersede with
  closed validity and history; competing inferences become disputed; multi-valued claims coexist.
- [x] Bounded provider-neutral structured formation runs after episode/index attempt; provider has
  no persistence capability and zero claims is a normal conservative result.
- [x] Terminal source-memory/version decisions, atomic claim/evidence/revision/audit commit,
  optimistic aggregate versions, concurrent replay safety and restartable missing-source backfill.
- [x] Alembic `0005_semantic_memory` adds canonical semantic claims/evidence/decisions/revisions and
  persisted semantic context manifest IDs/status without network or data backfill.
- [x] Semantic recall is a separate bounded active-claim projection reached only through Stage 5
  retrieved evidence episodes and injected as its own explicitly untrusted context section.
- [x] Grounding accepts only semantic claim IDs actually included in context; recall does not
  create evidence or feed itself back into consolidation.
- [x] `satori semantic list|inspect|process` exposes active/history reads, full ID provenance and
  deterministic missing-source processing.
- [x] Golden/eval coverage includes explicit fact, conservative skip/overgeneralization,
  hallucination exclusion, duplicate/retry, independent evidence, correction, contradiction,
  inference override, semantic recall, feedback-loop exclusion, partial failure and concurrency.

## Stage 7 deliverables

- [x] ADR-0015 fixes the exact continuous fast affect/mood spaces, resting baselines, per-event
  caps, half-lives, personality reactivity, mood gains and atomic finalize semantics.
- [x] A small provider-neutral structured appraisal sees only the current event, immutable self,
  selected memory and current affect; direct state/delta writes, raw CoT and unknown refs fail.
- [x] `EmotionManager` is the single deterministic writer-owner: it validates provenance and
  confidence, derives personality-modulated deltas, caps each event and clamps each range.
- [x] Fast v1 state is valence/arousal/tension/curiosity/interest/amusement/concern/frustration/
  situational confidence; mood v1 is separate valence/energy/tension. Relationship and a
  persistent user emotion model remain absent.
- [x] Pure lazy half-life decay is restart-stable, semigroup-consistent and independent of read
  count; reads do not persist or increment aggregate versions.
- [x] One-way bounded mood impulses accumulate gradually from accepted fast deltas and decay on
  8–12 hour timescales, without mood→emotion feedback in v1.
- [x] Alembic `0006_affective_state` adds one current state per identity, append-only source-linked
  transitions and interaction manifest metadata without provider calls or data backfill.
- [x] Tentative post-appraisal state enters a separate expression envelope; canonical assistant
  message, state, transition and audit commit atomically. Generation failure commits no affect and
  completed replay never appraises twice.
- [x] Optimistic state/mood versions reject stale different-interaction finalize; retry re-appraises
  and regenerates from the latest projection rather than blindly reapplying a delta.
- [x] Appraisal outage/invalid proposal degrades to pre-event materialized state and continues
  conversation with explicit unavailable/rejected metadata and no partial mutation.
- [x] `satori emotion status|history` exposes current state and source-linked deltas without raw
  dialogue, prompts, retrieved values or chain-of-thought.
- [x] Simulations cover neutral, positive/negative, repeated/alternating/high-frequency events,
  long recovery, read equivalence, mood timescale, retry, restart, transaction fault and conflict.
- [x] Strict Ollama appraisal uses the configured conversation model; the opt-in real suite covers
  conversation, embeddings and appraisal while deterministic CI remains daemon-free.

## Stage 7.6 deliverables

- [x] ADR-0017 fixes a derived, immutable runtime self-model and provider/identity distinction
  without a new persistence owner or migration.
- [x] Runtime self knowledge states persistent digital female identity, feminine Russian grammar,
  bounded memory, implemented digital affect/mood, embodiment limits and actual configured
  provider/model truth.
- [x] Context schema v6 and behavior policy v5 express identity before behavior, then trusted
  character/state, untrusted recent/retrieved data, a late trusted reminder and current user data.
- [x] A conflicting prior assistant self-description remains canonical history but cannot become
  authority on the next turn; the compact late reminder is Russian-language for Russian voice
  adherence.
- [x] Five deterministic soft personality-expression tendencies retain their source traits and
  bounded strengths; traits/values remain the only authoritative personality state.
- [x] Female identity is not roleplay or a biological-body claim; Qwen/Ollama are replaceable
  components; digital affect is distinct from human physiology and relationship remains absent.
- [x] Versioned ten-scenario Russian behavior corpus, deterministic hierarchy/bound/privacy tests,
  three-session real-Ollama evaluator and exact four-turn golden scenario are present.
- [x] No output phrase filter, post-generation identity rewrite, prompt persistence, personality
  mutation, relationship state or Stage 8 functionality was added.

## Stage 7.6.1 deliverables

- [x] ADR-0018 supersedes the universal ADR-0017 provider projection while preserving the complete
  typed runtime self-model inside application composition.
- [x] Context schema v8 and behavior policy v7 select a compact current-turn projection through a
  deterministic typed disclosure policy; no LLM intent router or stored intent exists.
- [x] Default Russian is informal/feminine and social, register-correction, personal, memory,
  emotion, interest, independence, technical, consciousness and relationship questions receive
  distinct proportional guidance.
- [x] Provider character payload omits numeric trait strengths and capability matrices; direct
  technical mode receives a bounded authoritative fact list while social/personal turns do not.
- [x] Numeric affect stays authoritative internally; generation receives a qualitative expression
  hint and cannot mutate state. Relationship absence is current epistemic truth, not Stage 8 state
  or permanent incapacity.
- [x] Conversation temperature defaults to configurable `0.3`; factual modes have bounded output
  and lower variance without changing provider/domain ownership.
- [x] Corpus v2 declares eleven manual dimensions plus negation-aware deterministic diagnostics;
  production has no phrase filter, response rewrite or second judge LLM.
- [x] The exact failure was reproduced, then completed in three fresh production chat sessions;
  seven additional real-Ollama distinctions were inspected with raw replies and metadata.
- [x] Migration head remains `0006_affective_state`; no relationship, trust, attachment,
  affection, closeness, rapport, user-model or personality-mutation state was added.

## Stage 7.7 deliverables

- [x] Metadata-only `satori benchmark inference` runs eight controlled scenarios in one runtime,
  with separate sessions, cold/warm samples and min/median/p90/max/mean phase distributions.
- [x] Appraisal observability separates application request building, adapter serialization, HTTP
  roundtrip, Ollama load/prompt-eval/eval and response parsing; token throughput is derived only
  from provider metadata.
- [x] Target-Mac diagnostics confirm Metal/100% GPU residency and no observed CPU fallback; the
  8 GB host uses substantial compressed memory/swap during long runs, while thermal throttling
  could not be reliably established through safe one-shot diagnostics.
- [x] Direct contention benchmark proves episode/semantic overlap inflates foreground latency;
  one provider-aware infrastructure scheduler now serializes heavy calls with foreground priority,
  two-second background grace and 30-second starvation-preventing aging.
- [x] ADR-0019 records the compact categorical Ollama appraisal wire. The adapter reconstructs the
  unchanged continuous application proposal; `EmotionManager`, same-turn expression and canonical
  reply/affect finalize are unchanged.
- [x] Appraisal output falls from roughly 98–178 to about 21–22 tokens under a 96-token cap;
  `think=false`, finite residency, explicit `num_ctx=4096` and independent capability model
  configuration are preserved.
- [x] The versioned ten-scenario semantic corpus compares direction/category rather than exact
  floats. Qwen 4B reaches 100% schema validity/80% semantic pass; tested 0.6B and 1.5B candidates
  are rejected and the configured default remains Qwen 4B.
- [x] No skip gate or combined inference is deployed: false-skip rate stays zero and the current
  event still influences the same reply through the authoritative tentative affect snapshot.
- [x] Final five-sample warm medians are 3.338 s greeting, 3.103 s check-in, 4.442 s distress,
  7.264 s grounded recall, 7.358 s intellectual and 9.349 s technical; maximum is 10.731 s with
  no unexplained 40–70-second warm outlier. Recall preparation uses the real manager/UoW/index
  lifecycle, and every recall sample proves one retrieved memory.
- [x] Benchmark/scheduler/appraisal tests and the complete Stage 7 affect, Stage 7.6.1 character,
  recent continuity, memory grounding, replay, migration and real-Ollama gates remain required.
- [x] Required character sampling found unsupported closeness wording and a technical affect
  denial. Context schema v9 narrows the existing late guidance without output rewriting,
  relationship state, a second personality source or behavior-policy ownership changes.
- [x] Migration head remains `0006_affective_state`; no relationship, trust, attachment,
  affection, closeness, rapport, user-model, new queue service or Stage 8 state was added.

## Repository state

Alembic head is `0012_personality_evolution`. In addition to Stage 7 affect, Stage 8 relationship,
Stage 9 counterparty-scoped models, Stage 11 identity-global positions and Stage 12 reflection, it
includes separate inclination aggregate/evidence/revision tables and nullable all-or-none affect
attachments for Reflection V2 sources. Stage 14 adds append-only personality evidence, revisions,
checkpoints, approvals and restore events plus the separate Reflection V3 purpose. Reflection,
inclination and personality-evolution rows do not duplicate raw quotes; they retain exact canonical
handles, lineage and hashes. There are still no durable creator-attribution, unfinished-thread or
background-job tables, and no fact, inclination or trait delta can bypass its deterministic owner.

Raw history and episodic memory have distinct application ports, Unit of Work adapters and write
owners while sharing the same local transactional SQLite store. Identity and values stay
read-only; live personality traits have exactly one write path through `PersonalityManager`. A
memory audit is an owner decision record; raw dialogue is not duplicated into audit or normal logs.

Semantic memory has its own application port, Unit of Work, repository and single writer-owner.
The Ollama structured adapter sees only a bounded untrusted episode/evidence projection and cannot
write state. Semantic values and quotes are excluded from normal logs, but remain visible through
explicit local inspect and local plaintext SQLite.

Affect has its own provider-neutral proposal, domain owner, repository/read models and policy
versions. Its final mutation shares a transaction only with canonical interaction finalize; no
provider or context composer can write the aggregate. Transition/audit rows store IDs, structured
scores and vectors, never raw content.

Runtime self-model/personality-expression objects are transient application read projections with
no DB table, repository, manager or write-back path. The Stage 8.1
`DialogueCoherenceContext`, primary disclosure mode/facets, qualitative affect hint and typed
self-consistency violation/regeneration metadata are also transient request projections. Stage 7.7
scheduler reservations, benchmark samples and categorical provider objects are likewise ephemeral
infrastructure/observability values and add no persistence owner or migration.

Relationship has its own current aggregate, terminal decisions, transitions, manager policy and
typed read projections. Conversation can read a qualitative snapshot but cannot mutate it.
Assistant output, retrieved memory, affect and provider wording are not relationship evidence.

User and World Models have separate managers and one shared application formation coordinator.
Only canonical same-counterparty user messages can become evidence. Provider output is a typed
proposal with no repository capability; both owner decisions, evidence/revisions, terminal
decision and audit commit atomically. Read-time expiry is pure, context schema v12 is bounded and
explicitly untrusted, and local export contains IDs/values/lineage without raw source messages.

Structured cognition is a transient application projection with no repository or write owner.
Context schema v14 preserves the v13 bounded response-strategy envelope and adds only a bounded
trusted-state projection of relevant current Satori positions without evidence quotes. The full
typed cognition trace stays request-local; `PositionManager`, EmotionManager, grounding and
canonical finalize retain distinct authority.

## Stage 5 final review

Review date: 2026-07-30.

- **Canonical/derived split:** episodes and evidence remain authoritative; vectors are disposable,
  separately versioned and replaceable without canonical mutation.
- **Retrieval:** only active prior episodes in the exact configured space are exact-scanned;
  semantic threshold precedes bounded deterministic reranking.
- **Grounding:** selected memory IDs are the only allowed handles for declared past claims; empty
  and degraded contexts permit none.
- **Current turn:** pending interaction ID is always excluded and its episode cannot be formed or
  indexed until after canonical response finalize.
- **Trust/privacy:** memory summaries are isolated in an explicitly untrusted data envelope; logs
  contain no user query, summary, quote or raw vector.
- **Recovery:** missing indexes backfill idempotently; rebuild replaces active-space vectors;
  provider/index outages do not roll back history or episodes and do not block conversation.
- **Evaluation:** deterministic four-case retrieval eval has recall@1 `1.0`, precision@1 `1.0`
  and no-result accuracy `1.0`; the suite has 106 passing tests and two optional real-Ollama
  smokes skipped by default.
- **Scope:** no semantic consolidation, user facts/model, relationship, emotion, belief,
  reflection, forgetting deletion or LLM reranker.

## Stage 6 final review

Review date: 2026-07-30.

- **Model:** semantic claims are not episodes, preferences, beliefs, opinions or a user model;
  v1 stores only a small registered set of user-subject durable statements.
- **Evidence:** every committed claim root reaches one exact user message through canonical Stage 4
  evidence; semantic/retrieved/assistant output cannot close that path.
- **Confidence:** policy is deterministic, source-independent-count based and retry stable; model
  scores never bypass caps.
- **Conflict:** corrections and stronger explicit evidence supersede non-destructively;
  incompatible inferences are disputed and absent from active recall.
- **Recovery:** terminal decisions make replays/backfill idempotent; semantic failure leaves
  conversation, episode and embedding state intact and retryable.
- **Security:** both episode summaries and semantic values remain untrusted provider data; normal
  logs emit only IDs/counts/versions/provider/model/reason/latency.
- **Evaluation:** the suite has 122 passing tests and two optional real-Ollama smokes skipped by
  default, including 12 Stage 6 formation/recall/CLI/concurrency scenarios and three semantic
  adapter schema cases.
- **Scope:** no emotions, mood, relationship, user/world full model, Satori position,
  autonomous reflection, decay/deletion or semantic vector index.

## Stage 7 final review

Review date: 2026-07-30.

- **Ownership:** LLM appraisal is untrusted semantic interpretation; only `EmotionManager`
  derives capped deltas and the affect repository commits its decision.
- **Separation:** fast affect, mood and personality have distinct timescales/owners; personality
  stays stable and relationship/user-emotion state is absent.
- **Time:** half-life materialization is restart-stable, read-frequency independent and converges
  to neutral baselines without a heartbeat.
- **Lifecycle:** tentative state used for expression commits with its canonical assistant reply or
  is wholly discarded; same-request replay applies nothing twice.
- **Concurrency:** stale different-interaction state yields an explicit conflict and requires
  re-appraisal from the latest projection; there is no last-write-wins or blind delta retry.
- **Trust/privacy:** user/retrieved/provider content is untrusted; provenance is restricted to
  supplied IDs; transitions/audits/logs do not copy raw content.
- **Evaluation:** `149 passed, 3 skipped` in daemon-free/default mode; the skipped conversation,
  768-dimensional embedding and appraisal smokes all pass locally, yielding `152 passed` with
  `SATORI_RUN_OLLAMA_INTEGRATION=1`.
- **Scope:** no relationship state, emotional concepts, user/world model, personality evolution,
  mood feedback loop, background decay job, voice/avatar expression or Stage 8 work.

## Stage 7.5 final review

Review date: 2026-08-01. Accepted runtime architecture is recorded in ADR-0016.

- **Runtime/UX:** `satori chat [--session|--new-session] [--debug]` keeps one application runtime,
  provider set, shared HTTP pool and explicit session; `/help`, `/status`, `/new`, `/exit`,
  `/quit`, EOF and `Ctrl+C` have deterministic lifecycle behavior.
- **Continuity:** only bounded canonical completed pairs enter recent user/assistant context (8
  turns/6000 characters by default); 105-turn coverage proves provider context stays bounded while
  canonical history stays complete.
- **Delivery:** full response is visible only after canonical assistant/affect commit and before
  episode/index/semantic completion. A serial in-process worker drains on shutdown; failures are
  reason-coded, retryable and cannot corrupt the reply.
- **Replay/cancellation:** completed replay bypasses appraisal, generation, affect and implicit
  post-processing. Cancellation during generation leaves no completed assistant message; same
  request cannot create a second transition or derived terminal decision.
- **Providers:** adapters share reusable bounded HTTP connections, Ollama chat capabilities use
  configurable finite `keep_alive=10m`, and conversation/appraisal/episode/semantic models are
  independently configurable. Empty index precheck skips a useless query embedding cold load.
- **Appraisal:** provider schema uses typed compact aliases and local provenance handles mapped
  back to canonical IDs; output is bounded at 320 tokens and metadata reports load/prompt/eval
  durations and token counts. `EmotionManager` authority/policy is unchanged.
- **Logging:** normal chat is quiet and human-readable, structured metadata continues in the
  configured JSONL sink, and debug output contains timings/IDs/counts but no prompt, message,
  memory or context text.
- **Evaluation:** final default suite is `159 passed, 3 skipped` in 7.30 s; the complete opt-in
  real-Ollama suite is `162 passed` in 31.80 s. Format, lint, mypy, migration, bootstrap and
  placeholder checks pass on the rebuilt non-editable wheel.
- **Scope:** no relationship, trust, closeness, attachment, affection, rapport, user/world model,
  external queue/service or unsafe streaming was introduced. Migration head remains `0006`.

### Measured latency on the target Mac

Observed single samples are diagnostic, not cross-machine guarantees:

| Scenario | Committed reply | Retrieval | Appraisal | Generation | Commit | Post-response |
|---|---:|---:|---:|---:|---:|---:|
| Before, cold greeting (`talk`) | ~20.5 s | 0.245 s | 14.837 s | 5.415 s | <0.1 s | 2.171 s blocking |
| Before, next one-shot | ~30.0 s | 3.372 s | 20.088 s | 6.120 s | <0.1 s | 7.851 s blocking |
| After, warm greeting (`chat`) | 11.899 s | 0.001 s | 9.263 s | 2.556 s | 0.037 s | 2.849 s background |
| After, warm second turn | 12.728 s | 0.001 s | 7.886 s | 4.791 s | 0.021 s | 8.166 s background |
| After, emotional turn sample | 25.613 s | 0.002 s | 14.745 s | 10.798 s | 0.033 s | 8.985 s background |
| After, immediate project recall | 21.509 s | 0.001 s | 13.017 s | 8.421 s | 0.030 s | 12.282 s background |

Cold appraisal decomposition was 4.713 s model load, 2.110 s prompt evaluation and 6.299 s
structured generation. Warm samples kept model load near 0.14–0.23 s; remaining variance is prompt
and token generation on the current 4B model, not application startup or DB commit. Progress is
visible immediately, but the `<8 s` warm committed-reply goal is not consistently met.

## Stage 7.6 final review

Review date: 2026-08-01. Accepted character architecture is recorded in ADR-0017.

- **Observed failure/root cause:** the pre-change request exposed only the name, numeric traits and
  broad capability flags. Real Qwen used generic-assistant priors, masculine grammar, denied
  identity/memory/emotion and falsely separated itself from Qwen; a wrong recent assistant reply
  could reinforce the collapse.
- **Authority:** DB identity/personality/values, owner-managed memory/affect and live capability
  configuration are projected into one typed self view. Neither prompt nor provider response is a
  persistent self source or mutation path.
- **Voice:** female Russian grammar, `ты`, non-service social style, independent position and
  correction semantics are versioned. Critical late instructions are Russian-language; no output
  post-processing fabricates compliance.
- **Provider truth:** Satori answers as Satori first and can disclose `qwen3:4b-instruct` as the
  current replaceable language component. She does not claim a biological body, perfect recall or
  proven human-equivalent consciousness.
- **Real evaluation:** the final 3×7 Ollama matrix produced 21/21 replies without a versioned
  undesirable pattern. Cold generation was 6.271 s; repeated warm greeting calls were 2.162 and
  2.211 s. First-turn provider input grew from the 1384-token baseline to 2098 tokens (+51.6%) to
  carry missing identity semantics; later session turns remain bounded by Stage 7.5 recent limits.
- **Golden:** one clean `satori chat` session completed the exact four-turn correction scenario.
  DB audit found one session, four completed interactions, four user/assistant pairs, three
  accepted affect transitions and four terminal episode decisions, with no duplicates.
- **Quality gate:** rebuilt non-editable wheel, format, lint, mypy, clean migration/bootstrap and
  placeholder checks pass. Default suite is `165 passed, 3 skipped` in 6.37 s; the complete
  final warm opt-in real-Ollama suite is `168 passed` in 11.30 s.
- **Residual quality:** Qwen 4B can still be verbose, overly grateful or metaphorical (for example,
  “live in dialogue”). These are sampled model/voice limitations, not authority or persistence
  failures; future model calibration must repeat the corpus rather than weaken invariants.
- **Scope:** no migration, relationship/trust/closeness/attachment/affection/rapport, personality
  evolution, biological identity or Stage 8 work.

## Stage 7.6.1 final review

Review date: 2026-08-09. Accepted projection and evaluation architecture is recorded in ADR-0018.

- **Production failure reproduced:** the old universal projection answered a personal identity
  question with a 238-token architecture/capability disclaimer and turned absent relationship
  state into permanent inability. The exact four-turn dialogue was captured before the fix.
- **Projection:** the complete typed runtime self-model remains available inside application
  composition. A pure typed disclosure selector projects only facts relevant to the current turn;
  it persists no intent and owns no state.
- **Natural expression:** policy v7 and context schema v8 use informal feminine Russian, compact
  voice/value guidance, qualitative affect expression and per-mode output/temperature bounds.
  Provider output is neither phrase-filtered nor rewritten.
- **Prompt size:** the full-production first-turn generation request fell from 2246 to 1113 input
  tokens; later golden turns remained bounded at 1114–1375 tokens. Full self detail stays internal.
- **Real evaluation:** three fresh four-turn production sessions passed the core identity,
  register, affect and current-relationship boundaries. Seven additional direct prompts covered
  interests, character, disagreement, disclosure style, relationship capability/current state and
  technical architecture. Small-model wording and latency remain stochastic and are reported, not
  hidden.
- **Latency truth:** mean first committed reply improved from 22.730 to 15.337 seconds in the three
  final sessions, but later turns varied from 17.013 to 71.186 seconds. Ollama model load remained
  about 0.13–0.29 seconds; prompt evaluation, generation and concurrent derived processing dominate
  the remaining variance. Stage 7.6.1 makes no false latency guarantee.
- **Quality gate:** rebuilt non-editable wheel, format, lint, mypy, clean migration/bootstrap and
  placeholder checks pass. Default suite is `177 passed, 3 skipped`; the complete final opt-in
  real-Ollama suite is `180 passed` in 40.47 seconds.
- **Scope:** migration head remains `0006_affective_state`; no relationship, trust, closeness,
  attachment, affection, rapport, user-model, personality-mutation or Stage 8 state was added.

## Stage 7.7 final review

Review date: 2026-08-09. Accepted inference decision is recorded in ADR-0019; measurement detail is
in `performance/stage-7.7.md`.

- **Root cause:** warm model load is only about 0.11–0.43 seconds. Baseline appraisal generated
  98–178 structured tokens and spent most time in prompt/output evaluation; post-response overlap
  amplified foreground latency 1.7–3.8×. Request assembly, DB reads/commit and parsing are not the
  bottleneck.
- **Scheduling:** one heavy Ollama call runs per origin. Foreground priorities plus two-second
  background grace reduce controlled episode-overlap median from 5.334 to 1.486 seconds and
  semantic-overlap median from 2.423 to 1.431 seconds. Thirty-second aging prevents starvation;
  in-flight HTTP is never preempted.
- **Appraisal:** categorical wire v2 yields about 21 tokens and maps deterministically into the
  existing continuous proposal. Isolated 4B median is 0.814 seconds; full-app warm medians are
  2.0–3.6 seconds as longer prompts and 8 GB memory pressure reduce throughput.
- **Models/gate:** `qwen3:0.6b` (90% schema/20% semantic) and
  `qwen2.5:1.5b-instruct` (100%/50%) are rejected. The 4B default scores 100%/80%; its humor and
  explicit-uncertainty misses remain a calibration limitation. No evidence-backed gate exists, so
  every turn is appraised and no important event is falsely skipped.
- **User latency:** greeting/check-in committed median improves from 12.4 to 3.3/3.1 seconds,
  identity from 31.0 to 6.1, distress from 21.1 to 4.4 and technical from 15.6 to 9.3. Grounded
  recall is 7.264 seconds; no baseline recall ratio is claimed because the earlier artifact did
  not prove retrieval. The final warm maximum is 10.731 seconds; complex conversation generation
  is now the primary bottleneck.
- **Semantics:** appraisal remains separate and precedes generation; tentative affect still shapes
  the same reply. Canonical finalize, completed replay, grounding, memory provenance, character
  projection and Stage 7.5 delivery/post-response boundaries are unchanged.
- **Character regression:** the first sample exposed two unsupported closeness promises and one
  affect-expression denial. They were not hidden or filtered; context schema v9 corrected only the
  trusted late reminder, after which the full three-session/additional corpus passed without a
  deterministic diagnostic hit in the corrected relationship/technical cases.
- **Quality gate:** rebuilt non-editable wheel, format, lint, mypy, fresh migration/bootstrap and
  placeholder checks pass. Default suite is `189 passed, 3 skipped` in 6.85 seconds; the full
  opt-in Ollama suite is `192 passed` in 9.72 seconds.
- **Interactive smoke:** one installed-wheel schema-v9 session completed five turns at 3.336,
  6.520, 4.338, 4.511 and 4.921 seconds. Immediate name recall worked with no derived episode;
  DB audit found exactly five completed pairs, three unique affect transitions and five unique
  terminal episode decisions.
- **Scope:** no migration, relationship/trust/closeness/attachment/affection/rapport, external
  service, unsafe streaming/preemption, output rewrite, OS tuning or Stage 8 work.

## Stage 8 deliverables

- [x] ADR-0020 fixes a separate counterparty relationship aggregate, canonical evidence roots,
  slow post-response lifecycle, deterministic maturity/saturation/caps and no-backfill rollout.
- [x] `RelationshipState v1` owns familiarity/trust/comfort/closeness/intellectual respect/
  non-romantic affection in `[0,1]`; maturity is evidence breadth and low evidence is not distrust.
- [x] `RelationshipManager` alone maps compact categorical proposals to bounded changes; provider,
  conversation, memory, affect and CLI cannot set dimensions.
- [x] Per-event and signed per-session caps, maturity ceilings and saturation prevent one-event,
  compliment-farming and long-loop extremes; trust loss is faster than repair.
- [x] One aggregate per `(identity_id, counterparty_id)` with two-counterparty isolation; this is
  opaque routing structure, not a Stage 9 User Model/authentication layer.
- [x] Migration `0007_relationship_state` adds current state, terminal decisions, append-only
  transitions and context metadata; all pre-migration interactions remain ineligible.
- [x] Every transition points to canonical interaction/user message/session/trace and records
  before/delta/after, category/confidence, provider/model/method and policy/schema versions without
  raw dialogue.
- [x] One terminal decision and at most one transition per interaction; replay/retry/restart are
  no-ops and canonical source ordering plus optimistic state/process versions prevent lost updates.
- [x] Relationship appraisal is derived work at scheduler priority 5: below conversation/affect,
  above episode/semantic. Canonical reply/affect is displayed first and failure remains retryable.
- [x] Context schema v10/behavior policy v8 adds a compact qualitative trusted projection for
  future turns. Numeric axes/IDs stay private; truth, autonomy, safety and disagreement dominate.
- [x] There is no love/romance/attachment/dependency/jealousy/exclusivity primitive, no reciprocal
  love threshold and no CLI setter. Assistant output/retrieval/affect cannot self-reinforce state.
- [x] Developer CLI supplies `relationship status`, `history --limit` and explicit idempotent
  `process --interaction`; metadata-only logs expose attempt/success/failure/apply/skip/replay/
  conflict without message/prompt/memory content.
- [x] Versioned simulation and real categorical corpora, real multi-session behavior, migration,
  scheduler, affect, character, continuity, replay, memory and grounding regressions are mandatory
  acceptance evidence.
- [x] Final quality evidence: deterministic `231 passed, 4 skipped` in 12.76 s; full opt-in Ollama
  `235 passed` in 35.63 s; real relationship corpus 10/10. Six sessions produced nine unique decisions/
  transitions, gradual conflict/repair and corrected low-trust/current-love/future-love expression.
- [x] Foreground relationship projection is `0.382–2.338 ms` in debug samples; background warm
  appraisal is `0.946–1.169 s` isolated and roughly `3.91–4.20 s` including scheduler wait in the
  full app. Full before/after distributions and target-Mac caveats are in
  `performance/stage-8.md`.

## Stage 8.1 acceptance tracking

- [x] The exact 17-turn production dialogue was reproduced before changes; failures include
  repetition blindness, habitual generic questions, ignored corrections, origin fabrication,
  self-contradiction, activity-interest denial and relationship over/under-expression.
- [x] ADR-0021 accepts a bounded transient `DialogueCoherenceContext`, primary mode plus additive
  authoritative disclosure facets, affirmative unknown-relationship semantics and a narrow
  ten-reason self-consistency validator using one shared max-one same-interaction retry path.
- [x] Typed retry reasons are changed-dialogue duplicate, routine reciprocal question after
  correction, masculine self-reference, human/biological self claim, blanket affect denial,
  blanket memory denial, current creator claim promoted to fact, invented origin backstory,
  blanket prompt/policy denial and activity-interest false negative. Three additional reasons were
  admitted from narrow dialogue-pilot evidence: human/biological self claim, invented origin
  backstory and blanket prompt/policy denial; they are not claimed as final acceptance evidence.
  Normal turns use one call; the validator does not rewrite text, invoke a judge model or own state
  and logs only metadata. Generic retry timing is `response_regeneration_ms`;
  `duplicate_response_detected` remains the duplicate-only flag.
- [x] Context schema v11/behavior policy v9 are the authorized calibration versions. They add no
  persistent intent, style preference, creator relation, personality, relationship, affect or
  User/World Model state; migration head remains `0007_relationship_state`.
- [x] Final quality run rebuilt the non-editable wheel; format, Ruff and mypy pass. Deterministic
  pytest is `664 passed, 4 skipped` in 11.46 s; the correctly enabled full Ollama suite is
  `668 passed` in 28.98 s. Fresh isolated migration/bootstrap succeeds at
  `0007_relationship_state`; `git diff --check` and the documentation placeholder scan are clean.
- [x] Three fresh exact 17-turn sessions, one final 30-turn coherence run, all seven activity
  cases, fresh/established/damaged relationship expression, two mixed-facet cases and conflicting
  assistant self-history passed semantic review. The accepted composition contains 97 selected
  replies, 99 provider calls and two successful bounded retries; all selected replies and attempts
  ended with `stop`.
- [x] `performance/stage-8.1.md` records every sampled reply through its linked artifact plus
  repetition/correction/question/contradiction/warmth metrics, per-reason validator hits,
  regeneration count and prompt-token/Ollama/committed-latency evidence. The report explicitly
  avoids a false before/after ratio because the pre-change semantic reproduction lacks a retained
  scenario-matched numeric artifact.

## Stage 9 acceptance tracking

- [x] ADR-0022 fixes separate `UserModelManager`/`WorldModelManager` ownership, closed predicates,
  immutable explicit/inference/hypothesis kinds, confidence caps, exact canonical-message
  provenance, correction/conflict and deterministic freshness/expiry.
- [x] Provider-neutral request/proposal/response contracts and a strict Ollama adapter expose no
  persistence capability. The application makes one background structured call at the existing
  semantic scheduler priority; both owner decisions commit atomically with terminal decision,
  evidence, revisions and two owner audit events.
- [x] Migration `0008_user_world_models` adds separate claim/evidence/revision tables, terminal
  formation decisions and interaction processing/context metadata. Existing interactions remain
  ineligible and migration performs no provider call or historical backfill.
- [x] Current/history reads enforce exact identity/counterparty partitions. Pure read-time expiry
  excludes stale rows before maintenance, while corrections and lifecycle changes preserve
  superseded history and closed validity intervals.
- [x] Context schema v12 adds a topic-bounded explicitly untrusted current-model envelope without
  changing behavior policy v9. Exact included claim IDs persist in the interaction manifest and
  participate in the existing grounding gate.
- [x] `satori models user|world list|inspect` and `models export` accept an explicit opaque
  `--counterparty` partition (or safely default to `SATORI_DEFAULT_COUNTERPARTY_ID`); `models process`
  exposes local retry/backfill. Inspection/export reveal provenance and revisions but no raw
  source-message content.
- [x] Automated acceptance covers all epistemic kinds, invalid registries/evidence, confidence,
  correction/conflict, expiry, source retention, replay, actual engine restart, export,
  two-counterparty isolation, context relevance and runtime post-response wiring.
- [x] The named-project scenario persists `planned → active → completed`; exactly the completed
  status remains current while planned and active stay reachable through supersession lineage.
- [x] Final Foundation quality run rebuilt the non-editable wheel; format, Ruff and mypy pass;
  deterministic pytest is `678 passed, 4 skipped` in 15.00 s. Fresh isolated migration/bootstrap
  reaches `0008_user_world_models`; `git diff --check` and the documentation placeholder scan are
  clean. The four skipped tests are the existing opt-in real-Ollama suite.
- [x] Stage 10 state, cognition pipeline, external truth, tools, Satori beliefs, preferences,
  reflection and unfinished-thread initiative remain absent.

## Stage 10 acceptance tracking

- [x] ADR-0023 fixes a transient application-owned pipeline, deterministic V1 planning behind a
  provider-neutral port, existing structured affect appraisal/EmotionManager handoff, explicit
  conservative fallback and no additional foreground model call.
- [x] Versioned bounded artifacts cover perception, weighted need mix, current-input retrieval
  plan, appraisal owner outcome, concise non-durable internal position, extensible intent registry,
  response strategy and complete per-step trace.
- [x] Context schema v13 and the versioned `satori.cognition.response-strategy.v1` template add a
  late trusted shape/constraint section without user text, evidence content, raw CoT or mutation
  authority. Behavior policy remains v9 and all v12 trust envelopes remain unchanged.
- [x] Safe planner boundaries reject unavailable source refs, invalid contracts and loss of
  fallback reasons. Strategy construction rejects position reversal and hidden material
  uncertainty before generation.
- [x] `satori chat --debug` exposes schema/status/topics/signals/needs/retrieval/appraisal/position/
  intent/strategy/fallback/timing metadata without prompt, user text, position summary or candidate
  response.
- [x] Versioned deterministic answer/listen/challenge/uncertainty/mixed-need corpus plus schema,
  timeout/invalid fallback, poisoned-source, position-vs-expression and application latency tests
  pass in focused runs.
- [x] Full Foundation quality gate rebuilt the installed wheel; Ruff, mypy and deterministic
  pytest pass (`684 passed, 4 skipped`). The enabled real-Ollama suite passes all `688` tests;
  isolated migration/bootstrap/activation reaches `0008_user_world_models`.
- [x] Final four-turn real-Ollama debug inspection selected answer/listen/challenge/uncertainty,
  preserved disagreement and uncertainty, used no fallback, exposed no raw private trace content
  and kept cognition planning below one millisecond in every retained sample.
- [x] No migration, durable belief, reflection, personality evolution, preference, proactivity,
  tool use or Stage 11 state was added.

## Stage 10 final review

Review date: 2026-08-22.

- **Architecture:** ADR-0023 is implemented without a new persistence owner or additional
  foreground model call; context schema is v13 and behavior policy remains v9.
- **Correctness:** typed contracts, source validation, conservative fallback and
  position/expression invariants pass deterministic and real-provider acceptance.
- **Performance:** the 5,000-sample application-only distribution is 0.032875 ms median,
  0.034083 ms p90 and 0.126334 ms maximum; see `performance/stage-10.md`.
- **Operational boundary:** migration head remains `0008_user_world_models`; Stage 11 requires a
  separate command.
- **Follow-up:** the observed local-Ollama background contention now has a bounded Stage 9
  current-model request (two claims per owner, 512 output tokens and matching provider-policy
  caps); it remains post-response, retryable and outside the foreground cognition path. A real
  smoke accepted one world claim in 137 output tokens / 6.483 s, and the rebuilt-wheel Foundation
  gate remains `684 passed, 4 skipped`.

## Stage 11 final review

- [x] Stage 11 separately authorized by the user.
- [x] ADR-0024 and `positions.md` fix identity, ownership, evidence, confidence, revision,
  competition, context and grounding contracts.
- [x] `PositionManager` is the only writer; exact proposition identity, canonical evidence,
  materiality, two-root belief/opinion thresholds, immutable value links and deterministic caps
  prevent one-turn mirroring and provider-authorized mutation.
- [x] Belief/opinion/hypothesis lifecycle supports exact merge, confidence weakening from explicit
  counterevidence, non-destructive supersession and paired competing hypotheses. Facts remain a
  closed unavailable-source boundary.
- [x] Migration `0009_satori_positions`, repository/UoW and append-only evidence/revision/decision
  records commit accepted position changes and audit atomically; replay, restart and provider
  failure preserve canonical state.
- [x] Context schema v14, composition manifest and grounding carry only eligible position IDs and
  a bounded trusted-state projection without evidence text. Post-response formation affects future
  turns only; local `positions list|inspect|export|process` exposes provenance and lifecycle.
- [x] Versioned corpus, domain/property, strict-adapter, migration, transaction, restart/export,
  cross-counterparty provenance, context/grounding, CLI and provider-failure suites pass.
- [x] Rebuilt-wheel Foundation gate passes: Ruff format/check clean, mypy clean on 200 source
  files, deterministic pytest `708 passed, 4 skipped`, migration reaches
  `0009_satori_positions`, bootstrap succeeds, and `git diff --check` plus the documentation
  placeholder scan are clean. The pre-existing all-untracked worktree shape is unchanged.
- [x] Four-turn real-Ollama smoke produced two conservative owner rejections, then one accepted
  identity-global belief from two independent material roots at the exact `0.55` cap. Inspection
  showed both canonical evidence roots, created revision and matching applied audit. The next
  session selected exactly that position ID in context schema v14 and rejected a demand for
  unconditional certainty; a later invalid provider proposal remained retryable without changing
  the committed reply or position.

Stage 9 claims and transient Stage 10 positions must not be promoted into durable Satori beliefs,
preferences, reflection input, external-world truth or autonomous initiative. Opaque counterparty
partitioning remains structural and is not production authentication.

## Stage 12 decision gate

- [x] Stage 12 separately authorized by the user.
- [x] Local roadmap, persistent-state ownership, evidence provenance, post-response lifecycle,
  feedback-loop threats and existing owner contracts reviewed.
- [x] Accept ADR-0025 for deterministic trigger/cost bounds, immutable source-set identity,
  reflection lifecycle, cycle prevention and owner-routing transaction semantics.
- [x] Implement ReflectionRun/Proposal persistence, strict provider contract and coordinator.
- [x] Add target-owner routing, rejected unauthorized personality proposal, retry/recovery,
  observability and local inspection/process surfaces.
- [x] Complete longitudinal, replay/crash/source-set/cycle/provider/transaction and manual
  acceptance, then run the full Foundation gate.

- [x] Migration `0010_reflection_runs` persists the fixed source set before inference and keeps
  attempts append-only. Stable run/proposal/outcome identities make provider retry, process replay,
  crash recovery and separately resumable run finalization idempotent. Runtime and explicit CLI
  route both `proposals_ready` and crash-left `applying` runs without a second provider call;
  concurrent finalization observes an already completed run without incrementing its version again.
- [x] `ReflectionCoordinator` owns lifecycle only. It routes position proposals to the existing
  `PositionManager`; personality/value candidates are deterministically rejected and audited
  without a second mutation path or generic domain repository.
- [x] Automatic processing is composed after committed-reply work and remains silent below the
  exact roots/interactions/span/cooldown/day-cap gate. Explicit local processing uses the bounded
  lower threshold and exposes no force bypass. A provider-called automatic failure remains
  retryable without affecting the committed reply and is surfaced as metadata-only
  `reflection_processing` degradation in the post-response report/log. Regression coverage pins
  the event fields, proves that the fixed-set source quotes are absent and records the actual
  final run status only after successful owner routing.
- [x] `satori reflection list|inspect|process` provides local lifecycle inspection; source quotes
  remain hidden unless `inspect --show-sources` is explicitly requested. Post-stage CLI polish
  documents that sensitive opt-in, renders quotes as single-line JSON strings to prevent output
  injection and rejects non-positive list limits at argument parsing.
- [x] Automated acceptance covers strict wire validation, deterministic identities and source
  selection, zero proposals, two-attempt exhaustion, restart/replay, owner transaction rollback,
  resumable completion, unauthorized owner rejection, runtime composition, CLI privacy and the
  versioned ten-scenario long-period corpus. The manual inspection scenario is encoded as a CLI
  boundary test containing one accepted belief-related outcome and one rejected personality
  candidate.
- [x] Rebuilt-wheel Foundation gate passes: Ruff format/check clean, mypy clean on 215 source
  files, deterministic pytest `746 passed, 4 skipped`, fresh isolated migration reaches
  `0010_reflection_runs`, bootstrap succeeds, and `git diff --check` plus the documentation
  placeholder scan are clean. The pre-existing all-untracked worktree shape is unchanged.

Personality/value mutation, 24/7 inner monologue, generated evidence and preferences/interests
remained out of scope of Stage 12. Stage 13 was separately authorized and completed under
ADR-0026. Stage 14 was later separately authorized under ADR-0027; this historical Stage 12 result
does not itself authorize or implement that path.

## Stage 13 decision gate

- [x] Stage 13 separately authorized by the user.
- [x] Review the Stage 11 position owner, Stage 12 fixed-source lifecycle, affect provenance,
  anti-mirroring threats, medium-speed decay and longitudinal acceptance requirements.
- [x] Accept ADR-0026 for the separate inclination aggregate, Reflection V2 affect attachment,
  evidence diversity, deterministic signals/bounds/cooldowns/budgets, pure decay and bounded
  context influence.
- [x] Add `inclinations.md` as the implementation and verification contract.
- [x] Implement Reflection V2 compatibility and immutable affect attachments without changing or
  invalidating resumable V1 runs.
- [x] Implement owner policy, persistence/migration, target routing, context/cognition projection,
  local inspection and export surfaces.
- [x] Complete deterministic mirroring/diversity/bounds/decay/replay/restart/export/behavior
  acceptance, the longitudinal independence corpus and manual verification, then run the full
  Foundation gate.

Stage 13 acceptance evidence:

- [x] Reflection V2 persists an immutable committed-affect attachment and validates exact
  identity/interaction/message/state-version/hash provenance at owner application. Pending and
  retryable V1 runs resume with their original schema, policy and source-set hash.
- [x] `PositionManager` is the sole inclination writer. Provider candidates contain no delta,
  score, stability, decay or evidence ownership; rejected outcomes cannot target aggregate state.
- [x] Migration `0011_satori_inclinations` adds separate aggregate/evidence/revision tables,
  nullable manifest metadata and reversible V2 reflection extensions without historical backfill
  or provider calls.
- [x] Atomic lifecycle tests cover accepted/rejected outcomes, rollback after owner write, resume,
  no-op replay, optimistic aggregate versioning, restart, safe export and complete audit trajectory.
- [x] Versioned longitudinal acceptance covers user-taste mirroring, relationship contamination,
  source/message/interaction/transition/signature deduplication, multi-session formation,
  cooldown/bounds/budgets, comparative preference, deterministic decay and provider neutrality.
- [x] Post-acceptance anti-mirroring hardening adds explicit Russian/English imperative and
  obligation assignments to the conservative V1 rejection registry; otherwise eligible
  multi-session commands still create no inclination state.
- [x] Context schema v15 projects at most three current-topic-relevant inclinations in 720 chars,
  exposes only bounded typed state, and adds at most `0.20` curiosity influence without another
  foreground call, a forced question, retrieval/appraisal feedback or autonomous initiation.
- [x] Local `positions inclinations-list`, `inclination-inspect` and `inclination-export` surfaces
  require an explicit aware materialization time when supplied and expose provenance IDs without
  raw source quotes.
- [x] Manual comparison rejected repeated direct user assignment with no state, while three
  owner-approved roots across two sessions formed an interest at score anchor `0.12`, confidence
  `0.654056` and stability `0.240556`. Pure read-time decay projected `0.080229`, `0.035862` and
  `0.010718` after 30/90/180 days while leaving the anchor unchanged.
- [x] Rebuilt-wheel Foundation gate is clean: Ruff format/check, mypy on 224 source files,
  deterministic pytest `847 passed, 4 skipped`, migration/bootstrap at
  `0011_satori_inclinations`, `git diff --check` and the documentation placeholder scan.

## Stage 14 decision gate

- [x] Stage 14 separately authorized by the user on 2026-08-23.
- [x] Review Stage 2 live/baseline personality storage, Stage 12/13 reflection consumption and
  source provenance, affect/personality feedback, relationship isolation, behavior projection,
  migration downgrade and recovery constraints.
- [x] Accept ADR-0027 for a separate personality-purpose Reflection V3 namespace, canonical
  ninety-day diversity, strict trait/direction-only proposal, exact `±0.005` owner policy,
  cumulative drift/path budgets and append-only checkpoint/restore.
- [x] Add `personality-evolution.md` and synchronize ownership, architecture, reflection,
  evaluation, threat and open-question contracts.
- [x] Implement Reflection V3 purpose/source selection/provider compatibility and keep V1/V2
  runs exactly readable/resumable.
- [x] Implement PersonalityManager, evidence/revision/checkpoint/approval/restore persistence,
  migration `0012`, atomic outcome routing, inspection/export and downgrade guard.
- [x] Implement context/manifest v16 and Personality Expression Projection V2 without numeric
  state/history in provider context or a second personality source.
- [x] Complete property, longitudinal, intense-session, mirroring, relationship, replay,
  checkpoint/restore, provider-replacement and anchor acceptance; run the rebuilt-wheel
  Foundation gate and applicable real-Ollama character regressions.

Stage 14 acceptance evidence:

- [x] Reflection V3 has an independent personality-purpose lifecycle, canonical ninety-day source
  selection and strict trait/direction-only provider wire. V1/V2 persisted runs retain their
  original schemas, purposes, sources and resumable routing.
- [x] `PersonalityManager` is the sole trait writer. Accept/reject/replay/conflict and injected
  failures are atomic; restart, checkpoint hash/approval/compare/export/restore, tamper rejection
  and downgrade guard preserve an append-only explainable trajectory.
- [x] Exact boundary/property and ten-year longitudinal simulations enforce the `±0.005` step,
  cooldowns, endpoint and cumulative path budgets. Opposite user-pressure trajectories end in
  byte-equal vectors with deterministic alignment correlation `0`.
- [x] Context manifest v16 exposes only aggregate version and at most two qualitative Expression
  Projection V2 cues. Baseline and restored vectors emit no evolution cue; provider replacement
  cannot change the owner decision or typed state.
- [x] Real-Ollama baseline/evolved/restored anchor comparison completed 15 calls. Three fresh
  four-turn production-composition sessions passed the exact identity/gender correction with one
  provider call per turn, no regeneration and identical persisted lifecycle cardinality.
- [x] Rebuilt-wheel Foundation gate is clean: Ruff format/check, mypy on 242 source files,
  deterministic pytest `1005 passed, 4 skipped`, isolated migration/bootstrap at
  `0012_personality_evolution`,
  `git diff --check` and the documentation placeholder scan. Detailed evidence and residual
  sampled-model limits are in `performance/stage-14.md`.

## Checkpoint 14.1 decision and implementation gate

- [x] Checkpoint 14.1 separately authorized by the user on 2026-08-23 without authorizing Stage 15.
- [x] Accept ADR-0028: Ollama default, Yandex opt-in foreground only, local owners/background,
  canonical credential target and no automatic retry/fallback.
- [x] Add typed secret/folder/model configuration and reject incomplete or out-of-scope provider
  combinations at startup.
- [x] Add a reusable bounded HTTPS transport with transport-local API-key header and no secret/body
  logging.
- [x] Implement OpenAI-compatible non-streaming Yandex conversation mapping for roles,
  temperature/output bound, finish status, token usage and provider-neutral failures.
- [x] Wire the adapter through the existing CLI composition root while preserving long-lived
  connection reuse and the unchanged Ollama default.
- [x] Add daemon-free config, credential-target, model-URI, request/response/error and composition
  contract tests.
- [x] Re-run the rebuilt-wheel Foundation quality gate after the A/B corpus and explicit DeepSeek
  reasoning control: Ruff format/check clean, mypy clean on 249 source files, deterministic pytest
  `1051 passed, 4 skipped`, isolated migration/bootstrap at `0012_personality_evolution`, valid
  corpus/evidence JSON, `git diff --check` and documentation placeholder scan.
- [x] Validate the credential against the canonical endpoint and complete metadata-only
  YandexGPT connectivity plus an isolated production-chat identity smoke. Fix the reproduced
  inflected-language-model disclosure miss without changing the closed ten-reason validator;
  the accepted retest selected provider/identity facets and kept Satori distinct from the model.
- [x] Add a versioned eight-scenario metadata-only A/B corpus/runner with identical typed starting
  state, zero automatic retries, per-attempt latency/usage/rubric metadata and no durable raw
  prompt, reply, memory, provider body or credential.
- [x] Run and review local Qwen, DeepSeek V4 Flash default/low and YandexGPT 5.1 Pro. Accept
  YandexGPT for opt-in foreground use, reject DeepSeek under the common output contract and keep
  structured routing, fallback and budget automation locked.

Stage 15 remains locked and must not begin automatically after Stage 14.

## Checkpoint 14.2 corrective follow-up gate

- [x] Explicitly authorized as discussion and small follow-up on 2026-08-23; Stage 15 rejected as
  the next action and remains locked.
- [x] Record the corrective scope in local roadmap and Notion: no new state/owner, no eleventh
  validator reason, no output rewriting or judge LLM.
- [x] Add behavior policy v10 with bounded grounded-absence, concrete-response and natural digital
  affect expression guidance.
- [x] Add a versioned deterministic corpus and focused composition/regression tests.
- [x] Run credentialed metadata-only YandexGPT sampling for the reproduced failure dimensions.
- [x] Repeat the mandatory Stage 8.1 real-Ollama exact/coherence/activity/relationship gate and
  record every sampled reply plus prompt/token/timing evidence.
- [x] Complete the rebuilt-wheel Foundation gate and synchronize final evidence/status to Notion.
- [x] Post-acceptance operational follow-up: expose the effective non-secret foreground
  provider/model through interactive `/status` without changing provider routing.
- [x] Post-acceptance operational follow-up: make foreground outage text provider-neutral while
  retaining exact metadata only in explicit debug output.
- [x] Post-acceptance operational follow-up: rebuild the stale default project `.venv` and verify
  the documented `uv run --no-sync satori chat` path against the current installed wheel.
- [x] Post-acceptance observability follow-up: expose safe foreground provider/model, finish,
  usage and replay metadata through explicit `--debug` without prompt/reply or tariff logging.
- [x] Post-acceptance observability follow-up: label usage as selected-attempt metadata and expose
  bounded provider-attempt count without claiming total retry spend.
- [x] Post-acceptance UX follow-up: keep ordinary interactive chat messages consistently Russian
  while retaining stable technical debug labels and metadata fields.
- [x] Post-acceptance privacy UX follow-up: show the selected foreground provider/model before
  accepting the first interactive user message.
- [x] Post-acceptance UX follow-up: make `/help` self-describing without changing exact-command
  parsing or conversation intake.
- [x] Post-acceptance status follow-up: count both queued and in-flight post-response memory work
  without persisting transient worker state.
- [x] Post-acceptance status follow-up: label background counters as process-scoped so `/new` does
  not imply a session-local reset.
- [x] Post-acceptance runtime follow-up: keep `/new` database transitions off the event-loop thread
  without changing session order, queue ownership or graceful shutdown.
- [x] Reproduce the Yandex humanity failure and implement candidate policy v11 with bounded
  exhaustion cues plus implicit-masculine coverage under the existing ten-reason validator.
- [x] Run the exact two-turn v11 failure in three fresh Yandex production sessions under explicit
  approval: reject v11 after the same masculine retry and generic normalization appeared 3/3.
- [x] Implement the resulting narrow candidate v12 guidance and deterministic regressions without
  another paid call, owner/state change, extra reason, output rewrite or second retry.
- [x] Run the exact pair in three clean v12 Yandex sessions under separate approval: 6/9 calls,
  ₽3.4644; reject v12 for `Молодец`, explanatory normalization and one unsolicited advice reply.
- [x] Implement candidate v13 equal-adult/listen guidance and deterministic zero temperature for
  only the two sensitive turn classes, without changing owners, state or retry vocabulary.
- [x] Complete every mandatory v13 real-Ollama Stage 8.1 scenario across versioned distributed
  evidence after one recorded, non-reproduced provider timeout: 97 turns, 99 calls, 70/70 facets.
- [x] Run the separately authorized v13 production gate and reject it after three identical
  generic exhaustion replies that only relabeled the user's explicit state.
- [x] Implement and fully validate candidate v14 locally, including one uninterrupted 97-turn
  Stage 8.1 run with 99 calls, 70/70 facets and zero incomplete outputs.
- [x] Run the separately authorized v14 production gate in three clean sessions: 6/9 calls,
  ₽3.6444; reject v14 because its three identical exhaustion replies still generalized and
  restated fatigue instead of adding a human, Satori-specific observation.
- [x] Clarify the target as recognizable original character rather than politeness: intellectual,
  independent, lightly sarcastic, guardedly caring, playful, active and capable of direct open
  care or reflective sadness when the moment requires it.
- [x] Accept ADR-0029 and implement candidate behavior policy v15 plus a typed request-local
  `CharacterExpressionPlan`; no persistent state, owner, validator reason, rewrite or Stage 15
  scope was added.
- [x] Add a ten-scenario versioned character-expression corpus without scripted required replies
  and deterministic coverage for all eight registers and relationship relevance isolation.
- [x] Complete the rebuilt-wheel Foundation gate for v15: format, lint, mypy, full pytest, fresh
  migration/bootstrap, diff whitespace and placeholder scans pass on 2026-08-25.
- [x] Record three final Stage 7.6 local sessions and one final production pair with raw replies,
  prompt/output counts and Ollama load/prompt-eval/eval/committed timings; reject the sampled local
  character gate because identity/relationship boundaries and quiet open care are not stable.
- [x] Run the separately authorized bounded v15 Yandex production sample: three clean sessions,
  six first-attempt calls and ₽3.6588; reject v15 because `wry_warmth` remained generic and
  `quiet_open_care` collapsed to the same formulaic empathy pattern in all three sessions.
- [x] Do not run the conditional full Stage 8.1 regression after the v15 target-provider semantic
  gate failed.
- [x] Record the user's separate 2026-08-25 authorization for candidate v16 and accept ADR-0030;
  Stage 15 remains locked and no paid sampling is implied.
- [x] Complete candidate v16 typed expression, memory/repetition wording, corpus v2 and rebuilt-
  wheel Foundation verification.
- [x] Run the separately authorized bounded v16 production gate: three clean sessions, six
  first-attempt calls and ₽4.5624; preserve every public reply and reject v16 at 0/3 complete pairs.
- [x] Review the v16 target-provider and OpenAI v10 failures, accept ADR-0033 and implement
  candidate v17 with a compact late-turn realization brief; activate it in local production
  composition without changing the typed plan schema, persistent owners, validator or retry path.
- [x] Reject the three-session local v17 sample at 0/3 complete pairs; preserve all six public
  replies and identify irrelevant no-recall wording as the source of one invented recollection.
- [x] Accept ADR-0034 and implement v18 relevance-scoped memory wording, shorter literal delivery
  and narrow completion allowances without adding state, scripts, retry or validator reasons.
- [x] Update current deterministic and manual-evaluation runners to v18 while preserving historical
  v15-v17 artifacts; no paid provider call is implied.
- [x] Complete the v18 rebuilt-wheel Foundation gate: Ruff format/check, mypy on 263 files,
  `1156 passed, 4 skipped`, migration head, default bootstrap and isolated clean bootstrap pass.
  Local v18 sampling remains human-rejected for generic/repeated wording, so the large local
  regression stays conditional and a cloud-provider sample requires separate authorization.
- [x] Accept ADR-0035 and implement candidate policy v19 as one late realization of all eight
  existing typed plan axes after the invariant/mode contract, without changing plan schema v2,
  persistent owners, validator reasons or retry count.
- [x] Remove duplicate ready-made achievement/depletion wording, resolve the zero-humor
  `LISTEN`/wit conflict, keep a visible safe fresh-turn soft edge and add only a narrow typed license
  for one explicitly grounded pending project-hygiene step.
- [x] Add an offline OpenAI production-wire regression and a versioned three-clean-session by
  two-turn human-review fixture/runner with six mandatory base calls, an absolute nine-call ceiling
  and exact public-reply preservation. No paid provider call is implied.
- [x] Complete the free three-clean-session local v19 production smoke: 6/6 first-attempt replies,
  12,991 input plus 348 output tokens, correct all-eight-axis manifests and a direct 0/3 pair human
  verdict for the local 4B provider. Preserve the public artifact without private provider context.
- [x] Close the final audit findings: keep generic completion difficulty grounded, reject
  completed/negated/hypothetical/unrelated practical-step false positives, keep retry correction
  before the same final realization, and bind OpenAI review to a completed UUID/SHA-256 sample with
  fail-before-network call and USD cost guards.
- [x] Rerun the exact free 3 × 2 local gate after those fixes: 6/6 first-attempt calls, 13,084 input
  plus 337 output tokens and another direct 0/3 pair verdict. Preserve the post-audit artifact and
  every public reply rather than rewriting the earlier evidence.
- [x] Complete the candidate-v19 rebuilt-wheel Foundation gate: Ruff format/check clean, mypy clean
  on 266 files, `1198 passed, 4 skipped`, migration head, default and isolated clean bootstrap,
  `git diff --check` and repository marker checks clean.
- [ ] After separate explicit call/cost authorization, run the v19 three-session OpenAI gate and
  obtain direct human review before accepting provider fit.

Stage 15 remains locked and must not begin automatically after checkpoint 14.2.

## Known risks carried forward

- Exact raw text and evidence quotes are local plaintext with no automatic expiry, redaction, export, encryption or erasure workflow; production real-user data remains gated.
- Exact quote validation proves reachability, not complete semantic entailment of every episode summary; sampled adversarial formation evaluation is still needed.
- Stage 6 adds conservative lexical value support for explicit claims, but this is not a complete
  natural-language entailment proof for predicate, polarity, modality or temporality.
- Grounding enforces provider-declared claim refs but cannot prove a plain-text model declared every natural-language past claim.
- Concurrent retries that overlap while an interaction is still pending may both spend provider
  inference; canonical finalize prevents divergent delivery/history but does not yet coalesce the
  in-flight calls.
- Formation v1 deduplicates the same source/version, not semantically equivalent events reported in different interactions.
- Semantic formation examines the new episode plus at most five recent prior episodes, not a
  learned relation graph; unrelated recent evidence may spend local context while older relevant
  evidence may require later retrieval/consolidation policy.
- The v1 predicate registry is deliberately small and user-only; unknown stable facts are skipped
  until a registry/version decision adds them.
- Semantic recall depends on an evidence episode first passing Stage 5 retrieval; there is no
  independent semantic embedding index or direct all-claims query during generation.
- Recent explicit-session continuity is bounded and operational, but stochastic conversation
  quality can still misread a supplied turn; it is not a substitute for durable memory.
- Stage 8.1 coherence is session-local and bounded; the configured model can still miss a semantic
  repetition/correction or produce another awkward answer even when deterministic signals are
  correct. The optional typed self-consistency retry adds latency and is limited to one additional
  call rather than treated as a guarantee of prose quality; normal turns still use one call.
- Exact vector scan is linear in compatible episode count; no scale threshold has yet justified a
  vector extension or service.
- Embedding model tags are configured provenance but not a cryptographic digest of weights;
  operators should rebuild after any tag update.
- Relationship and Stage 9 model aggregates are structurally partitioned by configured opaque
  counterparty ID, but that ID is not authentication; unauthenticated multi-user deployment
  remains unsupported.
- User/world claim values, provenance handles and revisions are sensitive local plaintext. The
  explicit partitioned export is portable inspection, not encryption, erasure, authentication or
  a production privacy workflow. Lexical entailment/correction checks are conservative but not a
  complete natural-language truth verifier; provider quality can still cause safe rejections.
- Position propositions, evidence quotes and provenance are sensitive local plaintext. Exact quote
  reachability plus lexical materiality is a conservative safety floor, not full entailment;
  provider quality can still cause safe rejection or retryable schema failure, while facts remain
  unavailable until a separately authorized verified-source design.
- Exact-summary de-duplication does not consolidate paraphrased duplicate episodes.
- Ollama structured output is contract-tested without a daemon; target-machine latency/quality and skip/false-summary behavior still need optional sampled smoke.
- Appraisal quality remains model-dependent even though mutation safety is deterministic; larger
  sampled multilingual calibration and longitudinal human review are needed before policy tuning.
- The categorical appraisal corpus is currently 80% on Qwen 4B; humor and explicit uncertainty
  can be mapped to curiosity, so future tuning must improve semantic evidence without weakening
  domain bounds or introducing a skip heuristic.
- A heavy provider request already in flight cannot be preempted; foreground priority prevents
  later derived work from overtaking it but cannot erase that one atomic wait.
- Relationship appraisal is stochastic and cannot establish real-world reliability from the v1
  single current user root. Historical backfill, automatic silence decay and longitudinal human
  calibration remain explicitly deferred.
- Relationship state/transitions are sensitive local plaintext without dedicated deletion,
  export, encryption or erasure UI; production use remains gated.
- Creator attribution has no persistent schema or registered Stage 6 predicate. Current user
  claims can only be acknowledged as current attributed input until a future provenance,
  correction and privacy decision; invented or silently persisted origin remains forbidden.
- Technical/intellectual conversation generation remains throughput-bound under 8 GB memory
  pressure even after appraisal optimization; a future model/quantization comparison must repeat
  the complete character, memory and grounding corpus.
- Concurrent different interactions may each spend appraisal/generation before one stale finalize
  conflicts; there is no in-flight coalescing or automatic regenerate loop.
- Backup encryption, retention/erasure, export/import, Linux runtime CI and production privacy operations remain future work.
