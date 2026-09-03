# Cognition lifecycle

## 1. Принцип

Cognition превращает вход и immutable state snapshots в ответ и typed proposals. Она не владеет persistent domain state. Semantic judgment может использовать LLM; validation, permissions, bounds, persistence и audit всегда детерминированы.

Structured artifacts описывают результаты рассуждения, но не raw private chain-of-thought.

## Stage 3 implemented slice

Basic Conversation Core реализует только минимальный stateless путь:

```text
current user input
→ load immutable InitialSelfSnapshot
→ deterministic CharacterContextComposer
→ trusted policy + trusted state projection + untrusted user role
→ ConversationGenerationPort
→ non-empty/bounded final text validation
```

Session/message persistence, recent window, perception, classification, retrieval, appraisal, emotion, internal position, response strategy, memory formation, proposals и finalize transaction ещё отсутствуют. `satori talk` не знает предыдущий turn. Это явно Option A, а не урезанная memory system.

Runtime context schema v1 включает только behaviorally relevant Stage 2 identity/personality/values и explicit absence capabilities. Все 15 traits и 9 values помещаются в малый configured budget; при overflow critical context не обрезается, а request отклоняется. Composition manifest хранится только в returned trace metadata и normal logs как schema/section/count metadata, без текста.

Behavior policy v1 запрещает invented shared past и сообщает, что `long_term_memory_available=false`. Golden test проверяет эту capability boundary непосредственно в provider request. Stage 3 не заявляет, что prompt гарантирует semantic coverage любого model output: target `ResponseGroundingGate` ниже остаётся обязательным при появлении evidence/memory, а real-model false-memory behavior оценивается отдельно от deterministic CI.

## Stage 4 implemented slice

Stage 4 оборачивает тот же current-input-only generation durable lifecycle без добавления retrieval/appraisal/full pipeline:

```text
client_request_id + optional explicit session
→ idempotent pending interaction + exact user message commit
→ load immutable InitialSelfSnapshot
→ same trusted policy/character + untrusted current user request
→ ConversationGenerationPort outside transaction
→ validate text + provider-declared past-claim refs
→ atomic assistant message/completed interaction commit
→ reply eligible for delivery
→ StructuredGenerationPort episode proposal
→ MemoryManager create/skip/reject
→ separate atomic episode/evidence/decision/audit commit
```

Without session ID a talk creates/closes an implicit one-turn container. At the Stage 4 boundary,
explicit sessions persisted multiple ordered interactions but did not send earlier messages to the
model. Stage 7.5 later adds only a bounded completed-pair read projection; it does not alter raw
history ownership or reinterpret history as memory.

Completed replay returns stored assistant text and does not call conversation provider. Pending/failed replay may generate again; a different text/session under the same client request ID is rejected. Formation replay returns a prior terminal decision or retries only when no decision exists. Episode failure cannot turn a completed interaction back into failed.

`ResponseGroundingGate` is operational for provider-declared claims. Since Stage 4 context includes no prior evidence IDs, any declared shared-past claim is rejected before assistant commit. Plain conversational providers do not yet have a semantic claim extractor, so undeclared natural-language claims remain a sampled-eval residual risk rather than a falsely claimed complete guarantee.

## Stage 5 implemented slice

Stage 5 inserts a deterministic episodic retrieval branch without implementing the remaining
appraisal/position/emotion pipeline:

```text
durable pending current turn
→ embed current user text
→ exact compatible-space scan of active prior episodes
→ cosine threshold + deterministic relevance/importance/recency rank
→ bounded explicitly untrusted memory envelope + manifest IDs
→ conversation generation → declared-past-claim grounding
→ canonical finalize → episode formation → independent derived indexing
```

Runtime context/manifest schema v2 distinguishes `retrieved`, `no_relevant_memory` and
`unavailable`. Retrieval failure becomes an empty unavailable memory envelope and conversation
continues. The context never contains unbounded session history. The wrapper is trusted
application policy; summaries inside it remain untrusted data. Current pending interaction is an
explicit exclusion and cannot retrieve an episode that will only be formed after finalize.

## Stage 6 implemented slice

Stage 6 extends only the memory branch and context, not appraisal/emotion/position:

```text
canonical finalize → episode formation → embedding attempt
→ bounded semantic structured proposal
→ deterministic evidence/predicate/confidence/conflict owner policy
→ atomic semantic terminal decision + claim/evidence/revision/audit

next turn: episodic retrieval → evidence-linked active semantic selection
→ separate untrusted episodic + semantic envelopes
→ conversation generation → memory/claim-ID grounding
```

Runtime context/manifest schema v3 adds a semantic retrieval capability, status and selected
claim IDs. A semantic section is present only when active claims are reached through Stage 5
selected evidence episodes. Its typed value is data, `inferred_fact` remains labeled and
`attributed_statement` is not a Satori position. Response grounding accepts a claim ID only when
that exact claim was included.

Formation sees the new episode plus a bounded recent evidence window. The provider may propose
zero to configured maximum claims and has no state access. Every accepted claim must cite the new
source and resolve to root user evidence; inference needs two independent interactions. Failure
does not affect reply/history/episode/index, and absence of terminal source/version decision makes
replay/backfill safe. Assistant generation and semantic recall cannot feed back as evidence.

## Stage 7 implemented slice

Stage 7 implements the appraisal/emotion handoff without claiming the rest of the target
perception/position/intent pipeline:

```text
pending current turn → episodic/semantic retrieval
→ materialize stored fast affect + mood at event time
→ provider-neutral structured appraisal of current event
→ EmotionManager validation + bounded personality-modulated transition
→ tentative post-appraisal EmotionalExpressionContext
→ generation + grounding
→ atomic canonical reply + affect transition + audit
→ reply delivery eligibility
→ post-response episode/index/semantic work
```

Runtime context/manifest schema v4 adds a separate trusted emotional-expression section and
appraisal/state/mood version metadata. The provider output is semantic scores, confidence, reason
codes and supplied source IDs, never state or delta. Current user emotion is not copied into
Satori; selected memories may interpret the current event but retrieval itself is not an event.
The expression section can subtly affect tone/attention/energy/caution, cannot override policy or
truth, and must not manufacture biology or relationship state.

The tentative state is not durable before generation. Provider/grounding failure discards it;
completed replay skips appraisal; canonical finalize commits assistant message, interaction,
state, transition and audit in one transaction. Stale different-interaction versions fail
explicitly and require re-appraisal/regeneration from the latest state.

## Stage 7.5 implemented runtime slice

Stage 7.5 changes orchestration/runtime UX without adding a cognition or relationship domain:

```text
one interactive process + one explicit session
→ pending intake
→ bounded recent completed user/assistant pairs
→ retrieval + affect materialization/appraisal
→ context/generation/grounding
→ atomic canonical reply + affect commit
→ full reply displayed
→ retryable serial post-response episode/index/semantic processing
```

The recent projection is bounded by whole turns and characters and contains neither pending/failed
turns nor past system/developer provider requests. Its user-message IDs may ground statements about
what the user said in the present session, but the projection is not episodic/semantic memory and
does not create durable user knowledge. This lets a next turn remain coherent while derived memory
is still absent or processing.

Completed replay exits before recent projection, retrieval, appraisal and generation and never
implicitly queues post-response work. Cancellation during generation leaves no completed assistant
message or affect transition. Canonical commit failure exposes no reply. Post-response failure
leaves the already displayed reply valid and reports a retryable phase.

Token streaming remains absent: provider fragments are not authoritative before canonical
finalize. Interactive feedback is a progress indicator followed by the complete committed reply.
Monotonic timings and provider duration/count metadata are observable without raw input, prompts,
recent text, memory or chain-of-thought.

## Stage 7.7 implemented inference refinement

Stage 7.7 preserves the same cognition order and changes only local provider execution:

```text
categorical Ollama appraisal wire
→ deterministic infrastructure mapping to continuous proposal
→ EmotionManager tentative transition
→ foreground-priority conversation generation
→ unchanged atomic finalize and delivery
```

Appraisal still occurs before generation, so the current event still influences the same reply.
Conversation and appraisal are two separate capability calls and keep independently configured
models. Episode and semantic calls use the same provider-aware infrastructure scheduler at lower
priority; a two-second default grace allows a fast next turn to claim the local inference slot,
and 30-second aging prevents indefinite derived-work starvation. An HTTP call already in flight is
not preempted.

The categorical wire carries no prose or reasoning. It is an adapter-local compression, not a new
domain proposal: application still receives continuous typed signals/source refs/confidence and
`EmotionManager` remains the only transition owner. No cheap skip gate or combined reply/appraisal
path is active; appraisal failure still uses the pre-event state and generation proceeds without a
mutation.

## Stage 8.1 dialogue-coherence refinement

Stage 8.1 changes request composition and a narrowly bounded generation failure path, not domain
ownership or the full target intent pipeline:

```text
current-session canonical completed pairs + current input
→ ordinary latest-eight read or explicit recap read (max 32 + existing char cap)
→ pure DialogueCoherenceContext over the newest eight pairs
→ primary dialogue mode + required authoritative self facets
→ trusted v11/v9 projection + untrusted recent/current content
→ first draft
→ optional narrow deterministic self-consistency reason → at most one second draft
→ existing grounding + canonical reply/affect finalize
```

The context can represent current-user repetition, similarity/repeated closing in recent assistant
answers, correction/frustration, session-local requests about questions or emoji, and the current
activity/topic. These are response-planning signals only. They are neither a durable intent nor a
user preference, relationship event, memory, identity fact or Stage 9 User Model claim. The next
request recomputes them from the newest eight canonical completed pairs; another session does not
inherit them.

Ordinary response composition exposes those same newest eight pairs. Only an explicit request to
return to a topic while identifying what was discussed, or to summarize the current conversation,
selects a larger read-only view: at most 32 completed canonical pairs from the same `session_id`,
still bounded by the existing recent-conversation character cap. The provider may use that larger
view for the requested recap, but coherence analysis still consumes only its newest eight pairs.
No recap, selector result or session-style state is persisted, and canonical dialogue from another
session is never imported through this path.

For a repeated, correction, prompt-pattern, creator or contradiction turn, request composition
uses a transient per-turn temperature-zero limit to reduce sampling variance around the active
contract. It does not persist or alter runtime configuration; a normal casual turn keeps the
configured conversation temperature.

One primary mode sets response shape while facets preserve all relevant authority. For example,
an emoji-and-emotion question can require both style and digital-affect truth; a creator assertion
can require origin uncertainty without inventing biography; and a physical activity can be
discussed with genuine curiosity even though Satori cannot perform it bodily. Policy v9 makes
questions optional/specific, treats corrections non-defensively and prevents prior assistant text
or internal policy language from becoming self truth or a repeated slogan.

Relationship projection remains a read of the unchanged Stage 8 aggregate. Low maturity and
uncertain midpoints mean little evidence, not coldness, distrust or dislike. Baseline friendly
openness and curiosity come from personality. Fresh, developing and established profiles may
subtly modulate ordinary delivery; damaged guardedness remains limited to a relationally relevant
subject.

The deterministic response validator recognizes exactly ten typed reasons: changed-dialogue
near-duplicate, routine reciprocal question after correction, masculine self-reference,
human/biological self claim, blanket affect denial, blanket memory denial, promoted current
creator claim, invented origin backstory, blanket prompt/policy denial and activity-interest false
negative. It is context/facet-gated and not a general semantic evaluator.

One reason may trigger one additional conversation call before commit. The two attempts share one
interaction, user message, retrieved/semantic manifest and tentative affect decision. The discarded
draft is not canonical output or evidence, and the validator does not rewrite it, judge it through
another model or mutate state. Normal turns use one call. Attempt/reason/outcome and duplicate
similarity when applicable are metadata-only; the generic retry duration is
`response_regeneration_ms`, while `duplicate_response_detected` remains duplicate-specific.
`self_consistency_violation_detected` contains no prompt, candidate or user text.

## 2. Target interaction pipeline contracts

### Stage 10 authorized architecture

ADR-0023 authorizes the minimal full observable pipeline as transient application policy. V1
perception, weighted needs, retrieval planning, position, intent and strategy are deterministic
and replaceable behind a provider-neutral planner port. The existing structured affect appraisal
is projected into the pipeline and remains the only semantic foreground cognition call;
`EmotionManager` alone accepts/rejects and bounds its proposed transition.

The pipeline trace carries typed artifacts, source refs, statuses, registry/schema versions,
fallback reason and per-step timings. It has no repository or mutation method. Normal logs and
`satori chat --debug` omit user/prompt/candidate text and internal-position prose. A conservative
fallback preserves current-input retrieval, raises uncertainty and forbids unsupported past claims
or false certainty. Response strategy is checked against the internal stance and uncertainty before
generation, so expression can soften but cannot reverse the position.

Stage 11 не меняет foreground pipeline и не превращает transient internal position в
state. После canonical reply отдельный structured provider может предложить durable
position только из bounded canonical user-message evidence. `PositionManager` заново
проверяет каждую ссылку, materiality, independence, immutable value, confidence cap и
target version. Provider output, assistant reply, retrieval, affect, relationship, Stage 9 models и
transient cognition никогда не становятся position evidence.

Stage 12 также не меняет foreground pipeline. Deterministic eligibility check может
выполниться только в конце existing post-response work; большинство turns
заканчиваются `not_due` без provider call и persistent run. Due run снача
фиксирует immutable canonical evidence handles, а его typed proposals влияют только
на будущие turns после отдельных target-owner decisions.

Stage 13 также сохраняет foreground call topology. Только новый due Reflection V2 run может
вернуть strict `satori_inclinations` candidate; `ReflectionCoordinator` маршрутизирует его, а
`PositionManager` независимо проверяет affect attachment, anti-mirroring, relevance, diversity,
bounds, cooldown, budget и expected version. Context schema v15 затем читает immutable effective
projection для текущей темы. Ни formation, ни context selection не добавляют foreground/per-turn
provider call, а generated reply никогда не становится inclination evidence.

| Step / owner | Input | Output | LLM? | Persistent? | Primary failure mode |
|---|---|---|---:|---:|---|
| 1. Intake / Interaction use case | Client request ID, person/session, content | Pending interaction, user message, trace ID | No | Yes, append-only | Duplicate request, invalid input |
| 2. Perception / Cognition | Normalized input, modality metadata | Entities/topics/signals with confidence/source spans | Hybrid | Trace only | Model treats content as instruction/policy |
| 3. Situation classification / Cognition | Perception, recent context | Need mix, risk flags, task/dialogue type | Hybrid | Trace only | Overconfident single-label classification |
| 4. Retrieval query / Memory query service | Input, classification, state refs | Typed query/features/filters | Hybrid | Trace only | Query leaks unsupported inference |
| 5. Candidate retrieval / MemoryManager read path | Query, canonical memories/index | Ranked candidates + feature scores | Primarily no | Trace manifest | Irrelevant/poisoned/excessive memories |
| 6. Context assembly / ContextComposer | Trusted policy, snapshots, candidates, transient dialogue coherence, budget config | Trust-separated context + composition manifest | No | Trace manifest | Critical section dropped or budget overflow |
| 7. Appraisal / Cognition | Situation context, traits, values, relationship, memories, emotion | Structured appraisal + confidence + source refs | Yes | Trace only | Unsupported interpretation |
| 8. Emotional proposal / Cognition → EmotionManager | Appraisal and prior emotional snapshot | Proposed delta; accepted/rejected bounded delta | Hybrid | Accepted event/current state at finalize | Emotion runaway or direct state overwrite |
| 9. Internal position / Cognition | Evidence, beliefs, appraisal | Position summary, confidence, supporting points, concerns | Yes | Usually trace; durable belief only via proposal | Raw CoT storage or user mirroring |
| 10. Intent selection / Cognition | Need mix, position, relationship, emotion | Extensible intent tags and priority | Hybrid | Trace only | Generic assistant default/forced question |
| 11. Response strategy / Cognition | Intent, position, tone constraints | Tone, verbosity, humor, softness, points, safety constraints | Hybrid | Trace only | Strategy contradicts internal position |
| 12. Generation / Provider port | Trust-separated context + strategy | Response envelope: draft + declared past/identity claim refs | Yes | No until finalize | Hallucination, prompt injection, outage |
| 13. Response grounding / Application gate | Draft claims, context manifest, evidence refs | Approved draft or bounded rewrite/error | Hybrid | Trace decision | Unsupported shared-past claim escapes |
| 14. Expression plan / Expression policy | Grounded draft, emotion, channel capabilities | Text plus future voice/avatar parameters | Hybrid later | Trace only | Internal emotion equated to display |
| 15. Memory formation / Memory cognition | Interaction, appraisal, outcome | Zero or more typed memory proposals | Hybrid | Only after owner decision | Every utterance made permanent; false summary |
| 16. Other state proposals / relevant cognition | Same fixed evidence set | Relationship/position/thread/etc proposals | Yes/Hybrid | No direct write | Feedback loop, missing evidence |
| 17. Domain validation / each state owner | Typed proposals, current version, policies | Accepted/rejected decisions + reason codes | No | Decision committed with change | Stale version, rate/bounds bypass |
| 18. Finalize / Application unit of work | Response, records, decisions | Atomic commit, completed interaction | No | Yes | Partial failure / response before commit |

Таблица описывает полный target lifecycle. ADR-0012 сохраняет episode как
downstream derived transaction, а ADR-0015 добавляет единственное currently implemented
other-state coupling: owner-approved affect transition коммитится вместе с canonical
reply. Все остальные target stages остаются gated.

## 3. Key structured outputs

Situation classification допускает не один enum, а weighted need mix, например:

```json
{
  "needs": {
    "emotional_presence": 0.65,
    "analysis": 0.35,
    "accountability": 0.55
  },
  "uncertainty": 0.22,
  "risk_flags": [],
  "source_refs": ["message-id"]
}
```

Internal position:

```json
{
  "position": "The idea is interesting but likely distracting now.",
  "confidence": 0.78,
  "supporting_points": ["short claim"],
  "concerns": ["short claim"],
  "evidence_refs": ["memory-or-message-id"]
}
```

Response strategy:

```json
{
  "intent_tags": ["challenge", "support"],
  "tone": "warm_direct",
  "verbosity": "medium",
  "humor": 0.15,
  "softness": 0.62,
  "points": ["address the repeated avoidance pattern"],
  "must_not_claim": ["unsupported memory"]
}
```

Schemas версионируются. Свободный текст не используется как mutation command.

Response envelope обязан перечислять evidence refs для утверждений вида «ты говорил…», «мы обсуждали…», «я тогда…» и других claims о прошлом/identity. Grounding gate проверяет, что refs существуют, были доступны generation и семантически допустимы по type/audience. Для формулировки «я помню» evidence должно предшествовать текущему interaction; текущий input позволяет лишь атрибутировать новое утверждение пользователю. Missing refs вызывают rewrite в форму с честной uncertainty либо explicit failure; один лишь уверенный текст модели не проходит gate. Поскольку semantic coverage нельзя гарантировать только schema, sampled false-memory evals проверяют необъявленные claims.

## 4. Internal position vs expression

`what Satori thinks` и `what Satori says` разделены, чтобы учитывать такт, неопределённость, канал и эмоциональное присутствие. Но expression strategy не может:

- превратить disagreement в скрытое agreement ради affection;
- объявить hypothesis фактом;
- создать память, которой не было в retrieved evidence;
- скрыть существенную uncertainty;
- выдать untrusted memory content за system policy.

Durable belief change — отдельный proposal в PositionManager; текущая internal position сама по себе state не меняет.

## 5. Reflection lifecycle

Reflection запускается редко и не на каждое сообщение:

1. deterministic scheduler/policy выбирает bounded canonical source period and evidence IDs;
2. создаётся idempotent `ReflectionRun` с фиксированным input set и source-set hash;
3. LLM анализирует только разрешённый fixed evidence set и bounded target state;
4. возвращает zero or more typed proposals с confidence/evidence;
5. каждый target owner независимо валидирует proposal;
6. decisions и mutations атомарно аудируются.

Reflection не читает собственный сгенерированный вывод как новое evidence и не повторяет rejected proposal без нового evidence/policy version. Так разрывается feedback loop.

Reflection V2 дополнительно прикрепляет к source уже committed owner-approved affective transition,
если она существует для того же identity, interaction и canonical user message. Attachment
сохраняется до inference, входит в V2 source-set hash и проверяется при routing. Он позволяет
`PositionManager` детерминированно вычислить inclination signal, но не делает affect, assistant
reply, current inclination или reflection output самостоятельным evidence. V1 runs продолжают
обрабатываться по исходному contract и не могут создавать inclination candidates.

## 6. Curiosity and proactivity

Conversational curiosity зависит от trait, transient affect, novelty, uncertainty, relationship и
контекста. В Stage 13 topic-relevant durable interests добавляют только typed numeric
`curiosity_influence`, bounded до `0.20`; comparative preferences его не увеличивают. Ordinary
selection требует exact normalized lexical relevance, а explicit вопрос о собственных
inclinations Сатори допускает bounded top-three projection. Influence конкурирует с потребностью
пользователя; follow-up не обязателен.
Невозможность физически участвовать в занятии ограничивает capability claim, но не
любопытство к опыту пользователя. Вопрос задаётся только когда он конкретен и
двигает общую тему; generic reciprocal closing не является evidence curiosity.

`topic_relevant_inclination` может быть только дополнительным response-strategy point. Он не
добавляет `ask_specific_follow_up`, не меняет stance, не перекрывает distress, correction или
direct request и не разрешает автономное начало разговора. Расчёт deterministic и не вызывает
модель. Durable inclinations исключены из affect appraisal, retrieval, relationship appraisal и
future evidence selection, поэтому curiosity influence не образует affect feedback loop.

Future Observer использует отдельный `should_initiate?` use case. Default — `nothing`. Допустимая инициатива ссылается на конкретный unfinished thread/event, проходит rate/quiet-hours/permission policy и не оптимизируется под engagement. Реализация только на Stage 19.

## 7. Failure behavior

- Если retrieval не даёт достаточного evidence, стратегия признаёт неизвестность; generation запрещено заполнять gap.
- Если draft содержит unsupported shared-past/identity claim, grounding gate не пропускает его к expression: bounded rewrite признаёт неизвестность или interaction завершается ошибкой.
- Если structured output invalid, ограниченно retry с тем же immutable input и trace; затем deterministic fallback/error.
- Если provider недоступен, core state остаётся неизменным, interaction получает recoverable status.
- Если expected aggregate version устарела, proposal пересматривается на новом snapshot или отклоняется; blind retry delta запрещён.
- Если commit не удался, клиент не получает non-streaming response как completed.
- Safety policy может остановить expression, но не должна фальсифицировать internal/domain state.

Начиная со Stage 4 provider outage возвращает typed `ProviderUnavailable`/`GenerationFailed`, пишет metadata-only failure log и переводит уже committed intake в `Interaction(status=failed)`. Identity/personality/values не меняются; retry с тем же client request ID повторяет incomplete turn. Episode outage происходит только после completed history, логируется отдельно и оставляет projection retryable.

## 8. Stage 7.6 self-knowledge in generation

Self-knowledge for conversation is a typed read projection, not introspection by the LLM. Before
generation the application composes identity/state/capability truth and deterministic personality
expression guidance. The model may verbalize that truth, but it cannot add, remove or mutate a
capability. Qwen/Ollama are disclosed as current language machinery; they do not become Satori's
identity. Digital affect and mood are domain state, while physical sensation and human physiology
remain unavailable.

Bounded recent assistant replies are useful for continuity but are not evidence about who Satori
is. Therefore a trusted current-turn reminder is placed after recent roles and before current user
data. It corrects authority ordering only: canonical history is not edited, the final answer is not
rewritten, and no provider output feeds back into persistent self. Memory grounding, affect
ownership, canonical finalize, replay and post-response processing remain exactly as in Stages
4–7.5.

### Stage 7.6.1 contextual disclosure

Full self-knowledge is not synonymous with full prompt disclosure. Before generation a small
deterministic selector chooses response depth from conservative current-input cues. It controls a
read projection only: social, personal, technical, memory, emotion, consciousness and relationship
wording never become cognition state or evidence. Detailed capabilities remain available in the
application and are projected only when relevant.

Behavior policy v7 uses informal feminine Russian, proportional length and natural character as
the default. A qualitative affect hint adjusts expression without becoming relationship evidence.
Relationship wording states only current epistemic incompleteness; it does not create love,
attachment or future promises. Generated text is committed unchanged after the existing grounding
gate; no second model judges it and no phrase filter repairs it.

Stage 7.7 keeps this character projection and its general disclosure budgets. The required
relationship-boundary correction narrows only relationship modes to 48/56 output tokens and
temperature zero; it does not globally cap answers. Technical and intellectual scenarios remain
slower because conversation output/prompt throughput, not application context serialization,
becomes the dominant cost after appraisal is compressed.

Stage 8 retains those budgets except relationship capability, raised from 56 to 80 tokens after a
real regression repeatedly truncated the future-uncertainty explanation. Current relationship
remains 48 tokens. The late reminder distinguishes per-axis uncertainty, current love evidence and
unknown future capacity; it does not rewrite output or create love state.

Stage 8.1 supersedes the single disclosure choice with a primary mode and additive authoritative
facets. Critical self facts can coexist when a turn genuinely asks about several boundaries;
unrelated capability dumps remain excluded. Context schema v11 and behavior policy v9 additionally
carry bounded dialogue-coherence guidance after recent canonical roles. A style correction is
session-local and prior assistant prose remains untrusted about identity, affect, provider role or
origin.

### Checkpoint 14.2 character expression

ADR-0029 added one post-strategy, pre-generation typed read projection; ADR-0030 superseded its
relationship-modulation clauses, and ADR-0036 historically replaced its current-turn contribution/
motivation selection. ADR-0037–0039 then evolved that request-local plan through policy v23; those
plan/response-act contracts remain historical for v19–v23. The response strategy still owns stance,
uncertainty, evidence boundary, verbosity, humor and softness. In the historical path, a pure
`CharacterExpressionPlan` composes those constraints with the five authoritative personality-
guidance codes, qualitative affect and the existing qualitative relationship profile. Fresh,
developing and established profiles may modulate ordinary-turn ease, care, openness and response-
local initiative. Damaged guardedness is read only when the current relational subject makes it
relevant.

Policy v19/schema v2 remains historical. Historical candidate v20 required schema v3: its semantic
move is the factual/continuity anchor, while closed contribution, motivational-posture and
pressure axes select what Satori adds and the maximum interpersonal force allowed by current
evidence. Narrow deterministic checks recognize explicit depletion, serious distress, a request
to be heard, a direct request for motivation, task retreat and harmful overextension. They reject
local negation and quoted examples, create no cognition artifact and are never persisted.

That plan was rendered as positive guidance so `listen` could choose open care, `challenge` a playful
edge, creative collaboration active energy, negative affect reflective candor and technical mode
thoughtful precision. A completion/depletion contrast may choose gentle recovery direction, but
cannot infer cause or further work. Explicit listening or serious distress disables ordinary
motivation; only directly stated harmful continuation permits a firm protective stop. These
choices never reversed the internal position, added evidence, persisted a style mode or authorized
an external action. V20 rendering was contribution-first, then factual-anchor constrained, and
bounded to at most two short complete sentences. Its target achievement/listen-sensitive turns
received a 128-token cap; reaching Ollama's length limit failed closed rather than committing a
fragment. Provider text otherwise remained canonical and unrewritten.

Response-local initiative means only a contribution inside the current reply that the typed
strategy already licenses. No probability target or persistent counter is implied. Observer-driven
or otherwise out-of-band initiative remains the separate Stage 19 `should_initiate?` boundary.

ADR-0040 historically made policy v24 the direct-delivery candidate while preserving v19–v23 as
historical paths. Once cognition returns a complete `ResponseStrategy`, the application derives one
`CharacterDeliveryDecision` directly; it does not construct the legacy expression plan or
response-act contract. Policies v10 and v19–v23 remain pinned to intent/template registry V1 with
template ID `satori.cognition.response-strategy` and schema 1. V24 alone uses intent/template
registry V2 with template ID `satori.cognition.response-substance` and schema 2. The selector copies
the registry version, primary intent, ordered tags, required point codes, complete forbidden-claim
boundary, response verbosity, `position_stance` and `preserve_uncertainty` exactly. It fails before
generation if the
strategy or intent is absent, the registry/template identity differs, any copied substance differs,
or the selected goal/voice/grounding/continuation/pressure topology is invalid. Character delivery
therefore cannot become a second cognition owner.

V24 renders the typed position through one cohesive canonical-character baseline and exactly one
late director. The director is the sole turn-specific provider-facing reply-shape instruction: the
V2 response-substance template renders cognition-owned intent/tags, required points, forbidden
claims and verbosity inside the same director. The historical V1 cognition-strategy prose, legacy
plan realization and response-act projection are not rendered alongside it. The underlying
cognition trace and decision remain typed and observable through safe metadata, while raw
chain-of-thought is neither requested nor stored.

Intent registry V2 adds `hold_safety_boundary`, `notice_repetition` and `receive_repair`. Protective
safety has precedence over exact-turn repetition, which has precedence over a clean repair offer.
Repair applies only when the ordinary stance remains `ANSWER` and no question, request, correction
or challenge owns the current message; it cannot hide an actionable turn. The character selector
must consume these cognition-owned meta-intents rather than infer an incompatible goal itself.

V2 also closes the intent-to-substance topology. `IntentSelection` contains exactly one
response-action tag, equal to `primary_tag`; `ResponseStrategy` contains exactly one action point,
equal to that same primary intent. Meta-intents use a singleton point set. A non-meta strategy also
contains `address_current_request`; its only permitted supplemental point codes are
`state_uncertainty`, `presence_before_advice` and `topic_relevant_inclination`. The cognition trace,
embedded V2 template and character-delivery boundary independently fail closed on a missing,
competing, unknown or mismatched action/point combination.

The safe planner boundary also treats `curiosity_influence` as owner-approved input rather than a
planner output. A returned strategy may project exactly zero or the supplied value; a positive
value requires `topic_relevant_inclination`, that point requires a positive value, and positive
influence is forbidden on fallback traces. Any amplification, substitution or point/value drift
uses the existing conservative cognition fallback. This does not create a second inclination
owner, and legitimate zero suppression remains allowed.

The director separates factual scope from conversational movement. `grounding` controls which
claims current or trusted evidence licenses; `continuation` independently controls a grounded
reaction, one in-reply initiative, a natural close or reserve. Ordinary depletion means presence
first and no more than one optional low-cost suggestion supported by current input; listen-only and
serious-distress paths remain presence-only. Existing affect and relationship owners only modulate
delivery. Guardedness cannot degrade an important technical or practical answer.

The existing ten-reason validator and max-one retry are unchanged and reuse the final director
byte-for-byte. There is no output rewrite, judge model, new persistent state or Stage 15 behavior.
The 32-case deterministic corpus and four-module employer-demo contract are acceptance inputs, not
proof of provider quality. Each reviewed module is digest-bound to its immutable safe report, and
the final readiness aggregate requires all four distinct modules, their exact human reviews and a
shared production configuration. Non-generation replay may omit the transient cognition/delivery
projection, but cannot treat it as state or fresh-generation authority. The later paid v24
`core_emotional` module was rejected and is only historical sampling evidence.

ADR-0041 adds a v25 cognition/delivery boundary for questions about Satori herself. The request-
local disclosure plan classifies social current-affect checks, reciprocal warmth, personal self-
disclosure and current-relationship questions, and carries every directly requested authoritative
facet, including `interests`. Its closed `DisclosureRequestKind` emits
`SELF_DISCLOSURE_REQUEST` only for `SATORI_SELF`; reciprocal warmth remains `NONE` and uses the same
social delivery path without claiming a self request. The cognition pipeline does not reinterpret
Satori's requested affect as evidence that the user needs emotional presence; it raises the
information need and selects `ANSWER` from trusted self state. Explicit listening, high distress
and harmful-overextension safety retain precedence.

V25 keeps cognition intent registry V2 and switches only the substance renderer to template
registry/schema V3. V3 renders `listen_and_reflect` without repeating, explaining or diagnosing the
experience, treats `presence_before_advice` as conditional and expands `hidden_user_state` to
include causal psychological explanations. The template still renders only closed typed codes
inside the sole character director; it is not a second personality source. V24 remains pinned to
template V2.

The schema-2 delivery decision copies cognition stance, uncertainty and all required substance as
before, then may select `social_connect` or `self_disclose` and carry the exact disclosure facets.
It cannot invent a stable interest when no owner-approved inclination is available. The adjacent
`depletion_follow_through` signal is also deterministic: only an explicit current stop/defer choice
immediately after a canonical depletion disclosure permits pressure-free practical care. No
provider output feeds back into any signal or cognition artifact. The v25 implementation phase was
offline. The later separately authorized 3 × 3 OpenAI sample completed without
retry or provider failure and proved this route, but its repeated calm-state declarations,
interest disclaimers and polished abstractions did not establish recognizability.

ADR-0042 historically replaced the active v25 realization with policy v26 while preserving that historical
path. The audit found a break after cognition and context assembly: live personality strengths and
values were not consumed by the v25 decision, affect and relationship were collapsed into a few
profiles, and several overlapping prose blocks instructed the provider to satisfy a response
checklist. Stage 15 autobiographical state would not fix that break, so it remains locked.

V26 advances `CharacterDeliveryDecision` to schema 3 without changing cognition ownership. The
decision still copies the exact V2 intent registry, primary intent, ordered tags, required points,
forbidden claims, verbosity, stance and uncertainty. `CharacterPresenceProjection` schema 1 then
derives one bounded causal read from that decision plus the existing runtime personality/value,
affect and relationship owner projections, an exact memory-use license and the availability of a
canonical position and an owner-approved inclination. Memory use is licensed only when retrieval
actually returned memory and the final delivery decision uses `trusted_context` grounding; stored
or retrieved memory under another grounding scope remains outside the character-presence move. At
most three qualitative signals from each state family may be selected; none is a proposal or
mutation.

The V26 V3 cognition template exposed `render_presence_purpose`: it rendered the selected
outcome compactly rather than repeating the historical response-substance checklist. One final
presence layer combines that purpose with the live state signals, grounding, continuation,
pressure and requested disclosure facets. Separate current affect, relationship, canonical-
character core and late-director blocks are absent on a fresh v26 turn. Factual/safety contracts
remain distinct where their authority requires it.

This boundary explicitly distinguishes a new Satori reaction, opinion or taste from an external
fact about the user or world. Missing inclination stays silent unless the user specifically asks
whether a stable hobby exists; personality and values may support general curiosity but never
manufacture a durable topical preference. Ordinary depletion uses no default pressure in v26;
motivation still requires an explicit request or the separate safety basis.

The v26 manifest records decision schema 3, presence schema 1, bounded qualitative signal codes
and levels, and the exact memory-use-license boolean. The boolean must agree with retrieval status
and final grounding. Non-generation replay may omit the transient fields, and provider prose never
feeds back into cognition or any state owner. The later attempt-5 sample rejected that frozen
V26/Terra realization; it remains historical evidence rather than cognition or prompt authority.

ADR-0043 kept the same cognition intent/template registry V2/V3 for the then-current policy v27
offline candidate. In that now-rejected historical policy, the full validated V3 support is no
longer reduced to a generic presence purpose: the compact operational renderer carries
`address_current_request`, the selected analysis/creative/follow-up support, conditional
presence-before-advice, uncertainty, every
forbidden-claim category and cognition-owned verbosity. Character selection may choose voice,
pressure and continuation only inside that substance/truth boundary.

Live personality/values, affect, scoped relationship and narrow current-turn evidence are consumed
before schema-4 movement selection. Direct objection and topic closure are request-local response
acts under the existing `ANSWER` stance, not new beliefs or planner state. A previous assistant
turn may establish what is being disputed, but cannot become a durable Satori position; revision of
a canonical position still requires the Stage 11 owner path. Pressure-free ordinary depletion
renders no advice/action plan, while explicit help, safety and listen-only paths retain cognition
precedence. Schema-2 presence is transient proof of selected owner inputs, never feedback into
cognition.

Offline tests cover exact schema isolation, complete V3 support, all 40 historical public inputs,
eight exact current turns, 28 broader situations and the unchanged maximum-one retry. The later
V27 attempt-2 provider sample completed 24/24 base calls without retry; generated prose remains
non-authoritative. Its direct human-only gate rejected the configuration at 6/24 recognizable
Satori presence and 9/24 natural delivery despite 24/24 grounding and completeness.

Checkpoint 14.3 / ADR-0045 changes the ordering that V27 could not validate. After retrieval and
tentative affect reads, but before `SafeCognitionPipeline.complete`, deterministic policy selects
one `CharacterAgencyDecision` from prepared perception/needs plus the immutable live state. The
decision states what Satori chooses to do in this reply; it does not contain prose, a hidden
thought, a durable goal or an instruction to mutate state. Cognition completion then remains the
hard obligation boundary: agency cannot remove required points, weaken forbidden claims, reverse
stance, hide uncertainty or pre-empt safety/listen/repetition/repair precedence.

Policy v28 delivery schema 5 copies both inputs into one validated result and presence schema 3
renders exactly one integrated movement. An owner-approved position/inclination contribution must
name a supplied ID; absent state cannot be synthesized. A retry receives the byte-identical
decision and request, and neither successful nor rejected provider prose becomes cognition or
agency evidence.

Completion failure is explicit rather than a mixed-status request: if the safe pipeline completes
as `fallback` after an applied intake, the earlier agency decision is downgraded before rendering
to the sole conservative `none/respond/stop` fallback topology. Delivery and manifest validation
require status parity while retaining cognition's final safety, repetition, uncertainty and point
requirements.

Fresh V28 generation records that transient decision in manifest schema 17. Historical policies
through V27 remain on manifest schema 16 and cannot consume or reconstruct V28 agency authority;
non-generation replay may omit the full transient agency/delivery/presence set but cannot create
one.

No persistent creator relation exists. The model may accurately attribute a creator claim to the
current user input, but cannot make it a durable fact, deny it as impossible, or replace the
unknown with invented biography. A future persistence contract needs its own provenance,
correction and privacy decision rather than reuse of relationship or semantic memory implicitly.

The required real-character rerun did expose two boundary phrases. Context schema v9 therefore
refines the existing late current-turn guidance only: relationship answers cannot add a promise of
closeness/being nearby, and technical answers cannot deny that the bounded affect projection
changes expression. Behavior policy v7, provider output delivery and all typed self/state remain
unchanged.

## 9. Stage 8 slow relationship cognition

Stage 8 keeps same-turn responsibility unchanged: the current event is affect-appraised before
generation, then reply/affect finalize atomically. Only after that canonical point does the
post-response processor submit a compact relationship event request. The request contains one
untrusted canonical user event plus opaque interaction/message handles; it contains no assistant
reply, retrieved memory, semantic claim, affect vector or relationship vector.

The Ollama wire returns one to three closed categories, bounded confidence and both handles, with
`think=false`, JSON schema and a 64-token ceiling. Infrastructure/provider semantics stop there.
`RelationshipManager` alone turns a valid proposal into counters, maturity-gated saturating deltas,
session caps and an applied/skipped/rejected terminal decision. No raw reasoning is requested or
stored.

This work reserves the shared inference scheduler at `RELATIONSHIP`: below conversation and affect,
above episode and semantic formation. A new user turn may take foreground priority or use the last
relationship snapshot; it never waits for derived relationship consistency. Conversation context
schema v10 adds only a trusted qualitative relationship section, while numeric state and provenance
remain outside the provider request. Love/dependency/exclusivity are not inferred constructs.
