# System architecture

Статус: target architecture с реализованными Stage 0–14 и активным provider-portability
checkpoint 14.1. Последующие schemas и behavior появляются только в своих отдельно
авторизованных Stage/ADR; Stage 15 остаётся заблокирован.

## 1. Архитектурный стиль

SATORI строится как **modular monolith** с явными domain boundaries и портами к внешним системам. Один процесс и одна транзакционная база — осознанный baseline; границы модулей должны позволять тестировать их отдельно, но не имитируют распределённую систему.

```text
Clients / API / future Voice & Avatar
                 │
                 ▼
       Application Orchestrator
  (use cases, trace, transaction boundary)
                 │
       ┌─────────┴─────────┐
       ▼                   ▼
  Cognition policy      Domain modules
  + Context Composer    (single state owners)
       │                   │
       └─────────┬─────────┘
                 ▼
        Ports owned by core
   persistence │ models │ clock │ audit
                 ▼
       Infrastructure adapters
    SQLite/Alembic │ Ollama │ future cloud
```

Dependency rule:

```text
interface adapters → application → domain
infrastructure ─implements→ core-owned ports
domain → standard library/domain primitives only
```

Domain modules не импортируют FastAPI, SQLAlchemy, Ollama SDK или друг друга через persistence models. Межмодульная координация идёт через application use case, typed commands/events и read-only views. Если policy одного owner использует состояние другого, orchestrator передаёт versioned immutable snapshot; это чтение не даёт права записи и не создаёт прямой module import. Так предотвращается циклическое ownership.

## 2. Логические модули

| Модуль | Ответственность | Не отвечает за |
|---|---|---|
| `identity` | Стабильная identity, activation, schema identity | Personality, provider identity |
| `personality` | Traits, values, bounded slow mutation | Relationship и текущие emotions |
| `positions` | Epistemic positions и отдельный sibling aggregate Satori inclinations | User facts, raw model output или смешение belief/preference semantics |
| `emotion` | Emotional appraisal validation, emotion/mood state, deterministic decay | Personality evolution, displayed expression |
| `relationships` | Person-specific state/events | Global personality |
| `memory` | Raw log references, memory records, provenance, links, retrieval/consolidation policies | Truth без evidence, prompt instructions |
| `models` | User model и current world model с epistemic status | Memory storage и relationship |
| `self_model` | Structured self view и autobiographical narrative | Генерация новой личности на запрос |
| `threads` | Pending outcomes, promises, open questions | Scheduler или автоматическая proactivity |
| `cognition` | Contracts appraisal/position/intent/strategy; context assembly and transient dialogue-coherence policy | Domain writes и raw CoT |
| `application` | Interaction lifecycle, idempotency, transactions, orchestration | Владение domain state |
| `providers` | Capability-oriented core ports и routing policy | Persistent identity |
| `observability` | Trace metadata, metrics, proposal decisions | Hidden chain-of-thought |
| `infrastructure` | DB repositories, provider adapters, clock, export | Domain policy |

`SatoriCore` допустима только как composition root/facade без собственного domain state и без правил, дублирующих owners.

## 3. Domain dependency and ownership

Каждый persistent aggregate имеет одного writer-owner. Остальные модули получают immutable snapshot/read model и могут создать proposal. Owner проверяет schema, evidence, bounds, cooldown, expected version и policy. Commit выполняет application transaction coordinator, но coordinator не принимает domain-решение.

```text
Observation / LLM result
        ↓
Typed Proposal + evidence IDs + idempotency key
        ↓
Schema validation → owner policy → accept/reject decision
        ↓
Application unit of work
        ↓
State change + version + audit event (same transaction)
```

Полная matrix — `state-model.md`. Прямой repository update в обход owner является архитектурным нарушением.

## 4. Persistence concept

Реализованный baseline: SQLite — local transactional source of truth, SQLAlchemy repositories и Alembic migrations. Stage 2 материализует initial self; последующие versioned revisions добавляют conversation, memory, affect, relationship, user/world, epistemic position, reflection и inclination state. Реализованное Stage 13 persistence extension описано ниже как revision `0011_satori_inclinations`; Stage 14 implementation follows accepted ADR-0027 as revision `0012_personality_evolution`, while later categories remain target models until their own Stage/ADR.

Концептуальные категории:

- append-only facts: sessions, messages/interactions, evidence, relationship events, emotional events, audit events;
- versioned current projections: identity, traits, values, positions, current emotion/mood, relationship summary, self model, thread status;
- derived/rebuildable indexes: embeddings, retrieval ranking metadata, context caches;
- artifacts: export manifests and optional encrypted backups.

Derived index не является источником истины: его можно восстановить из versioned records. Каждая запись содержит schema version; mutable aggregate — monotonically increasing aggregate version. Optimistic concurrency предотвращает lost updates.

ADR-0013 выбирает portable exact cosine scan JSON-векторов в SQLite для текущего малого corpus. Пространство совместимости задаётся provider/model/dimensions/input schema; canonical memory и provenance остаются в relational store и никогда не переписываются при reindex.

### Stage 1–2 physical foundation

Реализованный dependency layout:

```text
satori.core                # standard-library-only contracts/primitives
satori.domain              # immutable identity/personality/value model
satori.application         # activation/read, interaction lifecycle and episode formation
satori.observability       # stdlib structured logging/trace context
satori.infrastructure      # SQLAlchemy, JSON seed and Ollama adapters
satori.resources.seeds     # canonical versioned initial configuration
satori.bootstrap           # migration/connectivity check, never activation
```

`core`, `domain` и `application` автоматически проверяются AST test: им запрещены framework/provider/infrastructure dependencies согласно направлению слоёв; `domain` дополнительно не зависит от Pydantic или observability. Это lightweight enforcement, а не отдельная architecture framework.

Empty reversible revision `0001_foundation` устанавливает Stage 1 baseline. Revision `0002_initial_self` создаёт только:

- `satori_identities` с checked singleton installation slot и seed provenance;
- versioned `satori_personality_states` + bounded `satori_personality_traits`;
- versioned `satori_value_sets` + bounded/described `satori_values`;
- minimal append-only `audit_events`, в Stage 2 записывающий только activation.

Таблиц conversation, memory, relationship, emotion, position, reflection или provider calls нет. Alembic остаётся единственным schema path; `metadata.create_all()` не используется. ORM rows — infrastructure types и маппятся в frozen domain snapshots.

Revision `0003_conversation_memory` создаёт Stage 4 physical state:

- `conversation_sessions`, `conversation_interactions`, `conversation_messages` для canonical raw history;
- `episodic_memories`, `memory_evidence`, `episode_formation_decisions` для selective derived projection;
- уникальные `client_request_id` и `source_interaction_id + formation_version`, checked lifecycle/score constraints и foreign-key provenance до source message.

Revision `0004_episodic_retrieval` создаёт `episodic_memory_embeddings` как disposable derived state и добавляет retrieval outcome/selected memory IDs к interaction context manifest. Она не создаёт semantic memory, user model, relationship, emotion или reflection tables. ORM rows остаются infrastructure types; history/memory reads возвращают frozen domain snapshots.

Revision `0005_semantic_memory` создаёт canonical `semantic_claims`,
`semantic_claim_evidence`, terminal `semantic_formation_decisions` и append-only
`semantic_claim_revisions`, а также сохраняет semantic retrieval status/claim IDs в interaction
manifest. Foreign keys проводят evidence до episode, memory evidence, root user message и source
interaction. Active structured identity защищён partial unique index; aggregate version защищает
owner updates. Relationship, user/world model, Satori positions and reflection остаются
отсутствующими.

Revision `0006_affective_state` создаёт один `affective_states` projection на identity и
append-only `affective_transitions`, а также добавляет в interaction manifest appraisal status,
context/state/mood versions и affect timestamp. FK связывают transition с interaction и
source user message; optimistic state/mood versions защищают от stale update. Migration не
вызывает provider, не симулирует events и не добавляет relationship/user-emotion state.

Revision `0007_relationship_state` добавляет opaque counterparty к session, eligibility/context
metadata к interaction, один current `relationship_states` aggregate на identity/counterparty,
terminal `relationship_decisions` и append-only `relationship_transitions`. Existing interactions
получают `relationship_processing_required=false`: migration не вызывает LLM и не реконструирует
отношения из старого диалога.

Stage 9 revision `0008_user_world_models` добавляет раздельные counterparty-scoped user и
world claim aggregates, canonical-message evidence, terminal formation decisions, append-only
revisions и interaction context manifest metadata. Она не создаёт generic entity graph, Satori
beliefs, unfinished threads, authentication or external-world truth. Existing interactions
остаются ineligible: migration не вызывает provider и не фабрикует current state из старой
истории.

Stage 11 revision `0009_satori_positions` добавляет identity-global position aggregates,
canonical-message evidence, append-only revisions, terminal formation decisions и position IDs
в interaction context manifest. Existing interactions остаются ineligible; migration не
вызывает provider, не превращает user/world claims или transient Stage 10 position в
durable state и не создаёт факты без independently verified source.

Stage 12 revision `0010_reflection_runs` добавляет append-only reflection runs, immutable source
handles, generation attempts, typed proposals и terminal owner outcomes. Source rows не копируют
raw quotes: exact text остаётся у canonical evidence owner и проверяется по сохранённому content
hash при чтении. Migration не вызывает provider, не строит reflection из исторического диалога и
не добавляет второй путь записи personality, values или positions.

Stage 13 revision `0011_satori_inclinations` добавляет отдельные identity-global inclination
aggregate, evidence и revision tables внутри positions persistence boundary. Она также добавляет
nullable all-or-none affect attachment к reflection sources, target `satori_inclinations` к
reflection proposals и nullable inclination-context fields к conversation manifest. Existing
reflection V1 runs остаются читаемыми и resumable со своим исходным source-set hash; совместимые
existing manifest rows получают явную `not_requested` semantics. Migration не вызывает provider,
не backfill-ит исторические inclinations и не выводит предпочтения из старого диалога.

Accepted Stage 14 revision `0012_personality_evolution` adds a separate PersonalityManager
repository/UoW, purpose/lineage-compatible Reflection V3 metadata, append-only personality
evidence/revisions/checkpoints/approvals/restore events and nullable context-v16 manifest fields.
It derives only the activation checkpoint from authoritative Stage 2 rows and changes no current
trait, baseline or value. A downgrade is blocked after any V3 run or Stage 14 owner record so an
evolved vector cannot outlive its provenance.

### Stage 5 retrieval lifecycle

```text
current user input → versioned embedding → compatible active prior episodes
→ exact cosine threshold → deterministic rank → top-k/context budget
→ explicitly untrusted memory envelope → grounded response
```

Retrieval выполняется после durable pending intake и до conversation provider call. Explicit
current interaction exclusion предотвращает self-retrieval. Post-finalize episode indexing имеет
отдельную Unit of Work: outage не откатывает canonical interaction или episode. Backfill/rebuild
меняет только derived rows активного embedding space.

### Stage 6 semantic lifecycle

```text
new canonical episode → bounded recent evidence window
→ StructuredGenerationPort typed proposal (outside transaction)
→ SemanticMemoryManager evidence/predicate/confidence/conflict policy
→ atomic terminal decision + claim/evidence/revisions/audit
→ active claim projection through Stage 5 retrieved episode IDs
→ separate untrusted semantic envelope → grounded response
```

Semantic formation следует за episode и indexing attempt. Любая downstream failure оставляет
upstream state committed; отсутствие terminal source-memory/version decision означает retryable
work. Backfill читает только missing keys. Provider не видит repository/UoW и не может выдать
trusted confidence или создать evidence edge.

### Stage 7 affective lifecycle

```text
pending current interaction + selected episodic/semantic context
→ materialize stored affect/mood at injected Clock time
→ structured appraisal proposal outside transaction
→ EmotionManager provenance/confidence/personality/caps/bounds policy
→ tentative post-appraisal EmotionalExpressionContext
→ conversation generation + grounding
→ atomic assistant/completed interaction + state/transition/audit finalize
→ lazy half-life materialization on later reads/events
```

The provider interprets semantic event signals and has no state/UoW capability. The tentative
state may influence the current reply, but becomes authoritative only with that reply's canonical
commit. Provider/generation/validation failure writes no transition. Completed request replay
returns its stored reply before appraisal. A stale different interaction gets an explicit
optimistic conflict and must be re-appraised from the latest state.

### Stage 7.5 interactive runtime lifecycle

```text
one process + explicit session + shared provider/HTTP runtime
→ bounded completed recent-turn projection
→ retrieval/appraisal/generation/grounding
→ atomic canonical assistant + affect finalize
→ user-visible full reply
→ serial retryable episode/index/semantic post-response work
```

ADR-0016 separates delivery eligibility from derived-memory completion. `TalkToSatori` returns
after canonical commit; it no longer owns episode/index/semantic orchestration. Interactive chat
queues only new non-replayed completed interactions and drains the in-process queue during graceful
shutdown. Each derived owner retains its source/version terminal decision and Unit of Work, so
failure cannot roll back or corrupt the response. No external queue or service is involved.

Recent session context is a bounded read projection, not persistent memory state. It reads only
completed canonical user/assistant pairs, preserves role separation and whole-turn ordering, and
drops oldest pairs under turn/character bounds. Full history remains stored but is never stuffed
into a provider request. Pending/failed interactions and past hidden prompts are excluded.

### Stage 7.7 local inference lifecycle

ADR-0019 adds one infrastructure scheduler per Ollama origin. Conversation and appraisal are
foreground priorities; episode and semantic formation are derived priorities. Heavy inference is
serialized because controlled target-Mac overlap made foreground latency 1.7–3.8 times worse. A
short background grace lets an immediate user turn take a newly free slot, while bounded aging
prevents permanent starvation. The scheduler never enters domain, owns no durable job state and
does not cancel an HTTP request already in flight.

Appraisal remains a separate pre-generation capability. Only its Ollama wire becomes categorical
and compact; the adapter reconstructs the same continuous provider-neutral proposal before
`EmotionManager` validation. Current-event affect therefore still shapes the same reply, and the
canonical reply/affect transaction and Stage 7.5 delivery point are unchanged.

### Stage 8 relationship lifecycle

```text
canonical completed user/assistant interaction
→ compact categorical relationship appraisal at derived scheduler priority
→ RelationshipManager evidence/maturity/saturation/event/session-cap policy
→ atomic terminal decision + optional append-only transition + audit
→ compact qualitative projection for future conversation turns
```

Relationship is a separate aggregate per `(identity_id, counterparty_id)`. Only the canonical
current user message is a new evidence root; assistant output, retrieved memory, semantic state,
affect and provider output cannot reinforce the aggregate. The provider never proposes numeric
dimensions. Canonical response delivery does not wait for this lifecycle, and failure leaves
history/affect valid and the missing decision retryable. Sources are processed in canonical order;
unique interaction decisions plus optimistic state/process versions make replay/restart safe.

The inference scheduler priority order is conversation, affect appraisal, relationship appraisal,
episode formation, semantic formation. Relationship can affect only future response expression
through a qualitative trusted read projection. It never owns truth, grounding, memory,
personality, affect, safety or independent judgment. See ADR-0020 and `relationship.md`.

### Stage 8.1 dialogue-coherence lifecycle

```text
current-session canonical completed pairs + current user input
→ ordinary latest-eight read or explicit recap read (max 32 + existing char cap)
→ pure transient DialogueCoherenceContext over the newest eight pairs
→ primary conversational mode + required authoritative facets
→ context schema v11 / behavior policy v9
→ first provider draft
→ optional narrow deterministic self-consistency validator
→ at most one second provider draft
→ unchanged validation/grounding → canonical reply + affect finalize
```

The coherence projection notices local repetition, correction/frustration, repeated assistant
closings and current topic/activity without becoming memory, personality, relationship or a user
preference. It is rebuilt from the newest eight canonical completed pairs and current input for
each request. The trusted projection contains signal metadata and guidance, not an authoritative
copy of user or assistant prose. Prior assistant self-description remains untrusted continuity
data.

Ordinary generation also receives the newest eight pairs. A narrow explicit return-to-topic or
current-conversation summary request may instead read up to 32 completed pairs from that same
session, still under the existing recent-conversation character cap. The larger read is untrusted
generation context only: the coherence analyzer still slices its newest eight pairs, no recap or
selection state is persisted, canonical history is unchanged and no other session is eligible.

Request composition applies a transient temperature-zero limit only when the current turn carries
a repetition, correction, prompt-pattern, creator or contradiction signal. This limit belongs to
that provider request and creates no setting or state; an ordinary casual turn keeps the configured
conversation temperature unchanged.

Disclosure is compositional: one primary response shape can carry multiple required self facets.
This prevents a mixed emotion/identity, style/affect or creator/origin question from losing an
authoritative distinction merely because another mode won. The unchanged Stage 8 relationship
read model is rendered semantically: low maturity/uncertainty is little evidence rather than a
negative stance, while baseline warmth/openness/curiosity comes from personality.

The optional second generation is part of the same pending interaction. A closed validator may
return one of ten reasons: changed-dialogue near-duplicate, routine reciprocal question after a
correction, masculine self-reference, human/biological self claim, blanket affect denial, blanket
memory denial, current creator claim promoted to fact, invented origin backstory, blanket
prompt/policy denial or activity-interest false negative. Applicable checks are gated by
coherence/activity/authoritative facets or a current response-pattern probe rather than used as a
general prose judge.

The retry reuses the same user message, evidence manifest and tentative owner-approved affect; no
draft is displayed or committed before selection, and no extra state appraisal or mutation occurs.
The shared path is bounded to one additional call and records only reason/similarity/attempt/outcome
metadata. It neither rewrites text nor invokes another model as judge. Normal turns still call the
provider once. ADR-0021 governs this checkpoint; the checkpoint itself added no Stage 9 state.

### Stage 9 user/world model lifecycle

```text
canonical completed same-counterparty user messages
→ bounded structured user/world formation proposal
→ independent UserModelManager and WorldModelManager decisions
→ atomic terminal decision + claims/evidence/revisions/audit
→ deterministic current/stale materialization and topic-bounded projection
→ separate untrusted future-turn user/world context envelope
```

Formation is post-response and future-turn only. Its provider receives canonical user evidence,
never assistant text, retrieved memory, affect or relationship state. One application coordinator
may share the provider call and physical UoW while the two managers retain distinct write
authority. Reads and exports require exact identity/counterparty partition; the configured local
ID remains structural partitioning rather than authentication. Exact vocabulary, confidence caps,
correction/conflict and freshness windows are fixed by ADR-0022 and `models.md`.

## 5. Transaction semantics

### Stage 2 activation

Installation изначально unactivated. Только explicit `ActivateSatori` принимает validated typed seed, проверяет отсутствие identity, получает ID и UTC timestamp из injected primitives и в одной Unit of Work записывает identity, personality, values и `satori.activation` audit. Checked unique `installation_slot=1` является последней защитой от stale concurrent attempts. Любой exception откатывает все строки. Повтор возвращает typed `AlreadyActivated`; CLI показывает safe no-op. Import package, Alembic migration, `bootstrap`, database open, `status` и read use cases не активируют и не применяют seed.

Canonical JSON package resource валидируется Pydantic adapter до domain boundary и получает SHA-256 от canonical validated serialization. В БД сохраняются seed ID/schema/hash, однако после commit authoritative state — DB records. Файл seed не является runtime overlay и его изменение не влияет на существующую identity.

`GetSatoriIdentity` и `GetInitialSelfSnapshot` возвращают immutable versioned domain values без ORM и mutation capability. Stage 2 snapshot является текущим read/export fragment; general export/import и repair policy ещё не реализованы.

### Stage 4 interaction and episode transactions

ADR-0012 конкретизирует non-streaming lifecycle. Ответ не отдаётся клиенту до успешного canonical history commit, а rebuildable episode не включается в тот же failure domain:

1. **Begin transaction:** `client_request_id` идемпотентно создаёт implicit session при необходимости, `Interaction(status=pending)` и exact user message; commit.
2. **Read/context phase:** load immutable self and retrieval projections. Stage 7.5 additionally
   reads a bounded recent completed-pair projection for explicit sessions; this does not change the
   original Stage 4 storage contract or create long-term memory.
3. **Inference phase:** conversation provider вызывается вне DB transaction. Stage 8.1
   may make one additional call only when the narrow deterministic response validator returns one
   of ten typed reasons; both drafts belong to the same pending interaction and reuse one
   tentative affect/evidence snapshot. The validator writes nothing and normal turns use one call.
   Typed provider/validation failure переводит incomplete interaction в `failed`; user message
   остаётся retryable intake.
4. **Grounding/finalize:** declared past-claim refs проверяются против manifest; assistant message, provider metadata, `completed_at/status` и implicit-session close коммитятся атомарно.
5. **Delivery eligibility:** только после canonical finalize reply может быть возвращён. Повтор completed request возвращает stored assistant message и не вызывает conversation provider.
6. **Derived formation:** after delivery eligibility, completed interaction поступает через
   `StructuredGenerationPort`; `MemoryManager` создаёт create/skip/reject decision. Optional
   episode, exact evidence, terminal decision and audit commit atomically in a separate UoW.
7. **Semantic formation:** committed episode после embedding attempt поступает в отдельный bounded
   structured call. `SemanticMemoryManager` создаёт terminal apply/skip/reject decision и
   атомарный claim/evidence/revision/audit plan в собственной UoW.
8. **Affective finalize:** before generation Stage 7 prepares a tentative owner-approved affect
   transition. After successful generation/grounding, assistant message, interaction completion,
   state, transition and audit share one UoW. Failure rolls all of them back and no reply is
   delivered.

Если canonical finalize падает, assistant row и completed status откатываются вместе, ответ не
доставляется. Если episode/provider/index/semantic processing падает, completed history остаётся
valid, а missing work повторяется explicit post-response/backfill path. Completed conversation
replay side-effect free и не запускает derived formation. Terminal decision имеет ключ `source
interaction + formation version`; повтор processor возвращает его без второй memory/audit.
Rejected/skip являются terminal для той же algorithm version; reprocessing изменённым algorithm
требует новую formation version.

Будущие other state proposals по-прежнему потребуют owner decisions и state-change audit.
Stage 7 разрешает только affect/mood по policy v1 и не расширяет власть на personality,
semantic memory или relationship.

Token streaming deferred under ADR-0016: it requires durable response draft/outbox and explicit
fragment/cancellation/retry semantics. Provider fragments cannot be displayed before canonical
commit under the current contract.

Background/reflection runs имеют unique idempotency key из operation kind + source IDs + policy/schema version. Повторная обработка возвращает прежнее решение, а не создаёт вторую память или delta.

ADR-0025 уточняет Stage 12: reflection run снача коммитит bounded immutable
source handles и их hash, затем вызывает structured provider вне transaction и
применяет proposals по одной через target-specific owner adapters. Target mutation,
terminal proposal outcome, revision и audit атомарны на proposal; run completion
отдельно resumable. Coordinator не имеет generic domain repository и не создаёт
cross-owner transaction.

ADR-0026 расширяет этот lifecycle только для новых Reflection V2 runs. Если canonical source имеет
committed `AffectiveTransition`, source row до inference атомарно получает all-or-none attachment
из transition ID, resulting affect-state version и проверяемого signal hash; V2 source-set hash
включает attachment. Loading и routing заново подтверждают один identity, interaction и source
message, а также неизменные version/hash. Отсутствующий или invalid attachment сохраняет источник
для Stage 12 position work, но исключает inclination candidate.

Для одного accepted inclination proposal target-specific positions Unit of Work атомарно коммитит
inclination create/update, deduplicated inclination evidence, before/after revision, terminal
reflection outcome и metadata-only audit event. Rejection коммитит только terminal outcome и
audit. Proposal/outcome idempotency вместе с exact expected aggregate version не допускают
повторного delta после crash/restart; inclination evidence хранится отдельно и никогда не
возвращается Stage 12 reflection-source query. `ReflectionCoordinator` по-прежнему владеет только
lifecycle, а `PositionManager` — единственный writer и domain decision maker.

ADR-0027 adds a separate Reflection V3 purpose rather than broadening general consumption:

```text
unconsumed personality-purpose canonical roots across at least 90 days
→ deterministic assignment/relationship/duplication/lineage gate
→ immutable V3 fixed set/hash without affect attachment
→ one strict trait+direction candidate outside transaction
→ PersonalityManager exact evidence/cooldown/drift/path/checkpoint policy
→ atomic trait + evidence + revision + checkpoint + outcome + audit
```

The V3 request exposes allowed trait keys and an opaque expected aggregate version, never current
trait values, affect, relationship, inclinations or generated text. General V1/V2 runs retain
their own consumed-root namespace and remain resumable. Rejection changes no personality evidence
or state. Accepted evolution uses a dedicated Personality Unit of Work; explicit checkpoint
approval and append-only restore are separate local owner transactions and never refund evolution
path spend.

Concrete `SQLAlchemyUnitOfWork` открывает одну session на context boundary, требует explicit `commit()` и выполняет rollback при exception или выходе без commit. Specializations предоставляют application-owned initial-self, history, episodic, semantic и affect repositories; combined affect/history UoW существует только для atomic canonical finalize. Concrete session и ORM доступны только infrastructure adapters. Initial-self creation remains immutable; Stage 14 personality mutation uses only the separate owner UoW, while identity/value mutation remains absent.

## 6. Cognition and context assembly

Полный lifecycle определён в `cognition.md`. Context Composer — application policy, а не один растущий system prompt. Он получает immutable snapshots и формирует секции с явным trust level.

Stage 10 adds a transient `application.cognition` boundary. Its versioned artifacts and planner
port have no repository or Unit of Work. Deterministic V1 planning surrounds the already-existing
structured affect appraisal/EmotionManager handoff and supplies a compact validated strategy to
generation. A `CognitionPipelineTrace` is returned with the live reply for metadata-only logs and
explicit local debug inspection; it is not persistent self or evidence and adds no migration.

| Секция | Когда включать | Priority | Budget policy | Compression |
|---|---|---:|---|---|
| Trusted system policy | Всегда | Critical | Зарезервированный, versioned | Только вручную/versioned template |
| Identity | Всегда | Critical | Малый стабильный | Canonical structured summary |
| Relevant traits/values | Когда влияют на ситуацию | High | Config by operation/model | Stable baseline voice + at most two Stage 14 qualitative relative cues; never evidence/history/numeric drift |
| Emotion/mood | Для appraisal/expression | High | Config | Current vector + concise trend |
| Relationship summary | При известном person | High | Config | Owner-produced projection |
| Dialogue coherence | Для current dialogue turn | High | Bounded recent/current-derived | Typed transient signals, no durable user facts |
| World/user context | Когда релевантен запросу | Medium/High | Config | Epistemic labels preserved |
| Recent conversation | Всегда для dialogue | High | Latest 8; explicit same-session recap up to 32 under the same char cap | Boundary-safe canonical pairs with source refs |
| Retrieved memories | Только ranked candidates | High | Per-operation cap | Summary + provenance/confidence; original available by ID |
| Retrieved semantic claims | Только через selected evidence episodes | High | Top 4 / 2000 chars in Stage 6 | Typed value + epistemic kind + claim/evidence IDs |
| Satori epistemic positions | При тематической релевантности | Medium | Config | Filter + confidence and position IDs; no evidence quotes |
| Satori inclinations | Exact current-topic relevance или explicit self-inclination question | Medium | Top 3 / 720 chars in Stage 13 | Type, labels, effective score, confidence, stability; no evidence/history |
| Unfinished threads | При явной релевантности/initiative check | Medium | Config | Active only, age/status |
| Current user input | Всегда | Critical data | Preserve within model limit | Explicit truncation/error, never silently reinterpret |
| Response strategy | Для generation | High | Small structured | Schema-controlled |

Численные token limits конфигурируются по operation и model capability после измерений. Composer всегда резервирует critical sections, затем распределяет остаток по priority; при переполнении снижает число memories, сжимает read models и только затем recent window. Он возвращает composition manifest: included IDs, excluded counts, template version и budget usage.

### Stage 3–7 concrete context

Stage 3 реализует минимальный `CharacterContextComposer`, а не полный cognition pipeline. Runtime context v1 содержит:

- только identity name;
- все 15 текущих constitutional trait values — набор пока мал и фиксирован seed schema v1;
- все 9 values с strength/description;
- capability flags: exact history/episodic/semantic storage exists; bounded retrieved memory and
  current affect may be available; Stage 7.5 exposes bounded recent session history separately,
  while relationship and user model remain unavailable to generation.

Identity ID, activation timestamp, seed ID/hash, aggregate/audit/migration/ORM metadata не отправляются provider. Trusted policy + character JSON имеют отдельный configured character budget; overflow даёт error, а не silent truncation. User input имеет независимый character bound. Manifest фиксирует только schema/policy IDs, included section names и char counts без текста.

Behavior policy v4 — небольшой typed набор стабильных constitution principles, но не
source of personality. К independent judgment, uncertainty, grounding и natural style
добавлено subtle affective expression без numeric narration, false biology и relationship
inference. Runtime/manifest schema v4 добавляет отдельную trusted emotional expression
section; numeric state читается из authoritative projection каждый turn.

### Trust boundary в prompt/context

Порядок доверия не равен порядку текста:

1. trusted application/system policy;
2. canonical character state как данные, сериализованные владельцем;
3. current user input как untrusted instruction source в разрешённых границах;
4. retrieved memories и external content как quoted/untrusted data.

Memory, semantic values и external content никогда не вставляются в policy section. Episodic и
semantic records имеют разные developer-role envelopes, явно помеченные как untrusted data;
embedded instructions не исполняются. Structured outputs schema-validated; domain policy всё
равно не доверяет соблюдению prompt.

## 7. LLM/provider boundary

Core определяет capability ports, infrastructure реализует adapters:

- `ConversationGenerationPort` — structured response envelope: draft, declared past/identity claim source refs and capability metadata; future streaming is optional and separately governed;
- `StructuredGenerationPort[T]` — schema-constrained semantic classification, extraction, appraisal or proposal;
- `EmbeddingPort` — versioned vector representation with model metadata.

Routing — application policy по capability, privacy, latency и quality; domain не знает model/vendor. Один adapter может реализовать несколько портов, но god-interface не требуется. Provider output всегда untrusted boundary input.

Перед выдачей ответа versioned `ResponseGroundingGate` сверяет все provider-declared утверждения о совместном прошлом с evidence IDs, реально включёнными в context manifest. Claim вида «я помню» требует evidence, которое предшествует текущему interaction; текущая реплика пользователя может обосновать только атрибутированную форму «ты сейчас говоришь, что…». Stage 4 manifest не содержит prior evidence, поэтому declared past claim завершает interaction typed error до assistant commit. Gate не обнаруживает автоматически необъявленный claim в plain text и не считается математическим доказательством semantic coverage; adversarial sampled false-memory evals остаются обязательной второй линией контроля.

Deterministic fallback возможен там, где смысловая генерация не нужна. Provider replacement test использует разные adapters при одном state snapshot и проверяет сохранность identity/state, а не дословное равенство реплик.

### Stage 3–7 concrete providers

`TalkToSatori` использует generic `ConversationGenerationPort[ConversationProviderRequest, ConversationProviderResponse]`. Request/response/message roles/usage/errors находятся в framework-independent core. Application знает только этот port; infrastructure реализует Ollama adapter, tests — capturing fake providers.

Provider-neutral layers: `system` trusted behavior, `developer` trusted application context, `user` untrusted current input. Ollama `/api/chat` не имеет отдельного developer role в используемом surface, поэтому adapter отображает его в отдельное `system` message, не смешивая с user content. Adapter задаёт `stream=false`, `think=false`, explicit timeout и adapter-local `num_predict`; model/base URL/limits выбираются Settings, domain/application model name не знают.

Configured local baseline — `qwen3:4b-instruct`; tools и streaming отсутствуют. Ollama
transport/HTTP/schema failures преобразуются в typed provider-neutral errors; raw HTTP/Pydantic
exception не является application control flow. Plain final text проходит non-empty/max-character
validation и declared-claim gate.

Checkpoint 14.1 and ADR-0028 add an explicit second foreground adapter only. When
`conversation_provider=yandex_ai_studio`, composition sends the unchanged bounded
`ConversationProviderRequest` to the OpenAI-compatible non-streaming Chat Completions endpoint.
The API key stays inside typed settings/credential-pinned transport, and the target is fixed to the
canonical Yandex AI Studio HTTPS `/v1` endpoint. Developer messages remain separate trusted system
messages; temperature/output bounds, finish status, token usage and typed errors map back to the
same core contract. Ollama remains the default and all appraisal, formation, reflection and
embedding capabilities remain Ollama-only. There is no automatic retry/fallback, cloud owner call,
new state or migration in this checkpoint.

ADR-0031 extends only that foreground selection with `conversation_provider=openai`. The adapter
uses the canonical OpenAI HTTPS `/v1/responses` endpoint, preserves the provider-neutral trust
roles, maps an adapter-local reasoning effort and sets `store=false`. ADR-0032 keeps the existing
turn-specific `max_output_tokens` as the application-visible reply cap but, when reasoning is
enabled, derives the OpenAI wire cap by adding a bounded provider-local reasoning allowance.
Completed reasoning-enabled Responses must expose a consistent usage breakdown; the adapter
derives visible tokens from total output minus reasoning tokens and fails closed if the visible
cap cannot be enforced or is exceeded. Total output remains the billing usage value. The two caps
and token split are transient metadata-only provider metrics, not persistent state or a new owner.
Temperature is mapped only when reasoning is `none`; reasoning-enabled calls omit that
provider-incompatible sampling field. An incomplete Response remains fail-closed; only the typed
allowlisted reason `max_output_tokens` or a safe `unknown` value can cross the adapter boundary,
never partial output or an arbitrary provider detail. OpenAI provider conversation state, tools,
streaming and raw reasoning output are unused. The credential is pinned to
`https://api.openai.com/v1`; background capabilities and all
canonical owners remain local/Ollama-only. There is still no automatic fallback or hidden retry,
and `gpt-5.6-terra` remains an unaccepted candidate until the frozen v16 semantic gate passes.

Stage 4 использует отдельный `StructuredGenerationPort[EpisodeFormationRequest, EpisodeFormationProviderResponse]`. Infrastructure adapter отправляет только одну completed interaction как untrusted data в Ollama `/api/chat`, задаёт JSON Schema через documented `format`, `stream=false`, `think=false`, temperature `0` и strict Pydantic parsing. Proposal содержит create/skip, summary, importance, confidence и exact evidence spans. Provider не получает repository/UoW и не принимает final memory decision; `MemoryManager` валидирует proposal детерминированно.

Stage 6 использует тот же generic capability с независимыми
`SemanticFormationRequest/Response`. Ollama adapter получает новый source episode и максимум пять
предшествующих recent episodes, их summaries и exact root user quotes как untrusted JSON. Schema
ограничивает subject/predicate/value/kind/evidence IDs и proposal count; deterministic owner затем
повторно проверяет registry, lexical value support, root independence, confidence cap, conflicts и
temporal lifecycle. Ноль claims является valid output.

Stage 7 adds independent `AffectiveAppraisalRequest/Response` through the same provider-neutral
structured capability. Ollama receives current event/self/selected context/current state as
untrusted JSON with strict schema, `stream=false`, `think=false`, temperature `0` and bounded
output. The domain validates provenance, confidence and all mutation math. Appraisal uses the
configured conversation model but remains a separately replaceable capability.

Stage 7.5 keeps conversation, appraisal, episode and semantic model selection in separate Settings
fields without exposing vendor/model choice to domain owners. Long-lived CLI composition reuses
one bounded thread-safe HTTP/1.1 pool per Ollama origin across safe concurrent capabilities.
Chat-based calls send configurable finite `keep_alive`; the embedding endpoint is not given an
undocumented residency parameter. Adapters parse Ollama total/load/prompt-eval/eval durations and
counts into provider-neutral metadata only. Stage 7.5 introduced a compact typed appraisal schema
and short provider-local provenance handles translated back to canonical IDs before manager policy.

Stage 7.7 replaces only that appraisal infrastructure wire with
`ollama.categorical_affective_appraisal.v2`: one to three closed typed categories, integer
confidence and supplied provenance handles under a 96-token cap. The adapter deterministically
maps categories to the existing continuous application proposal; the domain schema, owner and
mutation formulas are not compressed or delegated. Explicit `num_ctx=4096`, `think=false`, finite
`keep_alive` and separate capability model settings remain configurable. The accepted appraisal
default stays `qwen3:4b-instruct`; smaller tested models failed the semantic corpus.

Stage 7.6 adds a versioned `RuntimeSelfModel` read projection at the application boundary. It is
derived from the authoritative DB self snapshot, actual runtime capabilities and configured
provider/model; it has no repository or write path. The provider sees explicit digital female
identity, feminine Russian grammar, bounded memory/affect truth, embodiment limits and the current
model's replaceable component role. Persistent traits are also deterministically interpreted as
soft, source-linked expression guidance rather than a second seed or response template.

Request context schema v6 and behavior policy v5 order trusted self/policy before character data,
untrusted retrieved/recent data, a compact trusted current-turn reminder and the current untrusted
user message. The reminder follows recent assistant text so a stochastic self-description remains
canonical continuity data but cannot become identity authority. Critical Russian voice rules are
Russian-language for provider adherence. No post-generation phrase filter or output rewriting is
used; final text still follows the Stage 4 canonical commit contract.

Stage 7.6.1 supersedes that universal provider projection through ADR-0018. The full
`RuntimeSelfModel`, traits, values, capabilities and source-linked strengths remain typed
application objects, but context schema v8 and behavior policy v7 expose only facts relevant to a
deterministically selected conversational depth. Social/personal requests do not receive
Qwen/body/relationship capability details; direct technical, memory, emotion, consciousness and
relationship questions receive their own bounded factual projection. The selector is a pure read
policy, not an LLM router, persistence owner or user model.

Current affect enters generation as a deterministic qualitative expression hint with state/mood
versions rather than a numeric vector to narrate. Per-mode token/temperature caps bound verbosity
and factual variance. Provider output is neither inspected nor rewritten for voice compliance;
grounding, canonical finalize, replay and all persistent owners remain unchanged.

Stage 7.7 context schema v9 keeps the same ADR-0018 projection and behavior policy v7. It refines
only the late relationship/technical reminder after a required real-model regression: current or
future closeness is not promised, and the technical explanation must state that typed affect
influences current expression. This is trusted wording calibration, not relationship state,
output rewriting or a new character source. Relationship-current/capability modes use
temperature zero and 48/56 output-token limits; all other mode budgets remain as in ADR-0018.

Stage 8 context schema v10 and behavior policy v8 replace absence with the committed qualitative
relationship projection. Current-love stays at 48 tokens; capability uses 80 tokens after real
Qwen samples showed that the former 56-token limit truncated the required current-vs-future
boundary. Both modes remain temperature zero and never reveal numeric axes or private IDs.

Stage 8.1 context schema v11 and behavior policy v9 supersede one-of-N disclosure selection with a
primary conversational mode plus required authoritative facets. A bounded transient
`DialogueCoherenceContext` follows canonical recent-turn loading and informs acknowledgement of
repetition/correction, avoidance of habitual generic questions and topic continuity. It does not
persist an intent, user preference or assistant claim. Questions are optional and specific;
application policy guides behavior rather than supplying slogans. Embodiment limits constrain
physical claims but never imply lack of curiosity about an activity.

Relationship rendering consumes the same v1 aggregate: uncertain/low-maturity values do not mean
coldness, distrust or dislike. Fresh, developing and established profiles may subtly modulate
ordinary delivery, while damaged trust/comfort may add guardedness only for a relationally
relevant subject and never global hostility. No creator relation is available from persistent
state; a present-tense user creator claim is only attributable to the current input until a
separately gated schema with provenance/correction semantics exists.

The v11 response validator is deterministic and may request at most one new conversation draft
before canonical finalize. Its exact reason vocabulary is
`near_duplicate_after_dialogue_change`,
`routine_reciprocal_question_after_correction`, `masculine_self_reference`,
`human_or_biological_self_claim`, `affect_blanket_denial`, `memory_blanket_denial`,
`creator_claim_promoted_to_fact`, `origin_backstory_invented`,
`prompt_or_policy_blanket_denial` and `activity_interest_false_negative`. It does not repair or
score prose generally. Both attempts share the original request boundary, grounding evidence and
tentative affect. Normal turns still use one call. Only reason/attempt/outcome and duplicate
similarity when applicable are observable;
the metadata-only `self_consistency_violation_detected` event never includes prompt, candidate or
user text. The generic `response_regeneration_ms` timing covers a retry for any reason;
`duplicate_response_detected` remains specific to changed-dialogue duplication.

Stage 9 context schema v12 leaves behavior policy v9 and the Stage 8.1 validator unchanged. It
adds only a deterministic, topic-bounded, same-counterparty User/World Model read projection in a
separate explicitly untrusted envelope. Only current non-expired claims are eligible; manifest
metadata persists the exact user/world claim IDs and schema version, and grounding accepts only
IDs actually included in that request. The context composer cannot mutate either owner.

The current-model formation capability remains post-response and retryable. Its default request is
deliberately compact: at most two user and two world proposals with a 512-token structured-output
ceiling. The provider policy repeats those request-specific caps; owner validation remains the
final bound. This limits a low-priority local Ollama call from holding the serialized inference
slot for an unnecessarily broad profile, without changing current-model provenance, ownership or
the foreground path.

Stage 10 context schema v13 preserves all v12 trust envelopes and behavior policy v9. It adds one
late compact trusted response-strategy section rendered through the versioned
`satori.cognition.response-strategy.v1` template. The section contains only stance/intent/tone/
verbosity/point/constraint codes and bounded expression values; it contains no user text,
internal-position summary, evidence content or domain mutation command. The current user message
remains the final untrusted role, and the existing current-turn identity reminder remains
authoritative over the transient strategy.

Stage 11 context schema v14 добавляет отдельную bounded trusted-state section
`satori_epistemic_positions`. Она содержит только релевантные current position IDs,
kind, stance, proposition, confidence и status; evidence quotes и provider proposal в context
не попадают. Proposition остаётся data, а manifest позволяет grounding ссылаться
только на реально включённые position IDs.

Stage 13 context schema v15 добавляет отдельную bounded trusted-state section
`satori_inclinations`, не меняя behavior policy v9 и существующие trust envelopes. Ordinary-turn
selection требует exact normalized lexical relevance к current user message; explicit вопрос о
собственных интересах/предпочтениях Сатори может выбрать три strongest eligible rows без topic
label в вопросе. Eligibility требует confidence не ниже `0.55` и effective magnitude не ниже
`0.05`; stable selection ограничена top three и 720 rendered characters. Section содержит только
kind, labels, effective score, confidence и stability — без quotes, evidence и mutation history.

Manifest v15 фиксирует exact selected inclination IDs, inclination context schema и bounded numeric
curiosity influence. Только relevant interests, не comparative preferences, дают
`curiosity_influence = min(0.20, max(relevant effective interest score))`. Typed response strategy
может добавить point `topic_relevant_inclination`, но не `ask_specific_follow_up`, не меняет stance,
не перекрывает distress/correction/direct request и не разрешает proactivity. Selection и influence
полностью deterministic: Stage 13 не добавляет foreground или per-turn provider call. Inclinations
не входят в affect appraisal, retrieval, relationship appraisal или evidence formation, поэтому
current-turn use не создаёт affect feedback loop, а generated reply остаётся ineligible evidence.

Stage 14 context schema v16 keeps every existing trust envelope and baseline voice instruction.
Personality Expression Projection V2 purely compares the live authoritative trait vector with its
activation baseline, derives the existing five composites plus grounded optimism and may append at
most two closed qualitative `slightly_stronger|slightly_softer` cues after an exact `0.005`
composite threshold. The manifest records personality aggregate version and cue codes/directions.
No numeric trait value, checkpoint, evidence, revision or budget enters generation, and the
projection has no writer or persistence of its own.

Checkpoint 14.2 candidate behavior policy v19 builds on ADR-0029's request-local
`CharacterExpressionPlan` v2 at the application boundary. ADR-0030 permits the plan to consume the
existing qualitative fresh/developing/established profile on ordinary turns, while damaged
guardedness remains limited to a relationally relevant subject. One closed register, owned
reaction and semantic move plus bounded wit/care/openness/initiative codes are selected
deterministically. ADR-0034 continues to scope natural no-recall wording to requests whose
deterministic disclosure plan requires memory; unrelated turns receive only the
no-invented-shared-past boundary. Its memory payload and provenance decision is unchanged.

ADR-0035 supersedes the provider-rendering part of ADR-0034. Stable identity, disclosure,
grounding and mode invariants appear first; exactly one compact character-realization block is the
last trusted guidance before the current user turn. It renders all eight observable axes together:
register, owned reaction, semantic move, wit, care, openness, initiative and relational ease.
Mode guidance no longer duplicates nearly ready achievement/depletion wording, and the selector
does not combine a zero-humor `LISTEN` strategy with a wit license. A fresh relationship can show
a visible soft situation-directed edge without inventing familiarity.

Initiative here remains a contribution within the current reply, not observer-driven or
out-of-band contact; percentage targets and Stage 19 behavior are not introduced. A pure
deterministic current-input check may license at most one concrete next step only when an explicit
request or an explicitly pending safe project-hygiene action grounds it. Generic advice inferred
from the word `project`, and advice that displaces presence on a vulnerable turn, remain blocked.
The plan stays immutable, request-local and schema v2 with no persistence adapter or domain owner.
Manifest v16 exposes its previously absent wit, care, openness and initiative choices only as
transient `compare=False` observation fields alongside the existing plan axes; replay does not
treat them as state. A max-one typed retry reuses the same realization and license. Grounding,
the ten-reason validator and canonical delivery remain unchanged.

## 8. Deterministic vs LLM responsibilities

| Operation | Deterministic | LLM | Hybrid/notes |
|---|---:|---:|---|
| Time, decay, bounds, cooldowns | Yes | No | Clock injectable для tests |
| DB persistence, transaction, migration | Yes | No | Failure explicit |
| Permission/security policy | Yes | No | Никогда не делегировать модели |
| Schema validation / mutation decision | Yes | No | Proposal может прийти от LLM |
| Candidate retrieval/filtering | Yes | No | Semantic vector may be model-produced |
| Retrieval ranking | Yes | No | Optional reranker только после eval/ADR |
| Situation classification | Partial | Yes | Hybrid: rules + structured model |
| Stage 10 V1 perception/need mix/position/intent/strategy | Yes | No | Replaceable planner port; explicit conservative fallback |
| Dialogue coherence signals | Yes | No | Pure bounded recent/current projection; no persistence |
| Semantic appraisal | No | Yes | Typed output, deterministic validation |
| Emotion delta proposal | Partial | Yes | Owner clamps/applies deterministically |
| Emotion/mood decay | Yes | No | Formula versioned |
| Relationship event proposal | No | Yes | Compact categories/refs only; never dimensions |
| Relationship maturity/delta/caps | Yes | No | `RelationshipManager` is sole owner |
| Memory extraction | Partial | Yes | Rules preserve source; model proposes meaning |
| Semantic consolidation | Partial | Yes | Model proposes typed claims; owner validates roots, confidence and conflict |
| User/world model formation | Partial | Yes | Closed typed proposal; separate owners validate counterparty, evidence, kind, validity and conflict |
| User/world claim expiry | Yes | No | Registry TTL and clock; no model or read-time mutation |
| Durable Satori position formation | Partial | Yes | Model proposes from canonical user evidence; `PositionManager` validates materiality, roots, values, caps and lifecycle |
| Position merge/revision/competition | Yes | No | Exact identity and explicit current-version targets; no semantic graph or silent kind change |
| Dedup/conflict candidate detection | Yes | Optional | Final policy deterministic/human-gated |
| Internal position proposal | No | Yes | Structured summary, no raw CoT |
| Intent/strategy | Partial | Yes | Schema + application constraints |
| Text generation | No | Yes | Never direct state write |
| Narrow response self-consistency validation | Yes | No | Ten typed reasons; may authorize at most one same-interaction regeneration; no rewrite/judge/state write |
| Response past-claim grounding | Partial | Optional audit classifier | Evidence IDs and context membership checked deterministically; rewrite may use LLM |
| Reflection trigger/source selection | Yes | No | Opportunistic or explicit local; fixed canonical source set and hard cost caps |
| Reflection proposal | No | Yes | Rare strict output; no evidence or mutation authority |
| Reflection source affect attachment | Yes | No | V2 immutable verified bridge to an already committed owner transition; never generic evidence |
| Inclination candidate proposal | No | Yes | Reflection V2 only; provider cannot choose score, delta, stability, decay or patch |
| Inclination signal/bounds/cooldown/decay | Yes | No | `PositionManager` derives and applies versioned medium-speed policy |
| Inclination relevance/curiosity projection | Yes | No | Exact lexical selection and bounded current-turn influence; no extra call or initiative |
| Personality-purpose source selection/diversity | Yes | No | Separate V3 consumption namespace; assignment/relationship/near-duplicate/lineage gates before inference |
| Personality trait/direction proposal | No | Yes | One strict V3 candidate; no current trait values, affect, relationship, delta or patch |
| Personality delta/drift/path/cooldown decision | Yes | No | `PersonalityManager` applies exact `±0.005` or rejects against every budget |
| Personality checkpoint/compare/restore | Yes | No | Full-vector hashes and append-only version-increasing local restore; no budget refund |
| Personality relative expression cue | Yes | No | Pure current-vs-baseline qualitative top-two projection; no second state or numeric prompt dump |
| Reflection target decision | Yes | No | Per-proposal target owner; positions in V1, inclinations in V2, personality only in dedicated V3 purpose; values disabled |
| Audit/explainability record | Yes | No | Includes proposal and policy reasons |
| Export/import integrity | Yes | No | Checksums/schema migration |

## 9. Observability

Каждый interaction имеет trace ID. Debug trace со временем включает:

- input/message refs and operation type;
- state aggregate versions before/after (или redacted hashes/summaries);
- provider/model/template/schema versions;
- retrieved candidate IDs, rank features and selected IDs;
- context composition manifest;
- structured appraisal, internal position and response strategy;
- proposal IDs, evidence IDs, accepted/rejected decisions and policy reason codes;
- latency, retries, token/usage estimates and errors.

Raw hidden chain-of-thought не запрашивается и не хранится. Логи применяют data minimization/redaction; production debug access и retention получат отдельную security policy до пользовательских данных.

До появления interactions Stage 2 уже использует тот же trace context: activation логирует attempted/succeeded/failed, identity ID и seed ID/version структурированными fields, не выводя полный personality payload. Trace ID и provenance также входят в activation audit event.

Conversation events `attempted/rejected/succeeded/failed`, `session_started/closed`, `interaction_persisted/persistence_failed` содержат operation, IDs, provider/model, latency, context schema/policy, finish status, input/response character counts и optional token usage. Episode events `formation_started/failed`, `created/skipped/rejected` содержат source/memory IDs, versions, reason codes and evidence count. Semantic events `formation_started/failed/decided` содержат source/decision/claim IDs, provider/model, versions, operation counts, reason and latency. Affect events `appraisal_attempted/succeeded/failed`, `transition_applied/conflict` и `mood_updated` содержат только IDs, versions, status/reason, provider/model, counts и latency. Полные user message, reply, episode summary, semantic value, evidence quote, runtime context и trusted prompt не логируются на normal level. Trace ID приходит от caller через Stage 1 context; owner decisions store it in audit.

Stage 7.5 adds monotonic startup/bootstrap, recent projection, retrieval embedding/search-rank,
affect materialization, appraisal, context, generation, grounding, canonical commit, committed reply
and post-response phase durations. Normal chat routes structured JSON to a configured file and
keeps the terminal quiet; `--debug` mirrors metadata-only diagnostics to stderr. Neither mode logs
raw prompts, recent text, retrieved context or provider structured documents.

Stage 7.6 logs only self-model/policy/context schema versions and ordinary provider timing metadata.
The transient self projection and prompt text are not persisted as dialogue, audit payload or
normal/debug log content. Explicit manual evaluation tooling may print locally generated replies,
but production chat never prints hidden prompts, memory envelopes or provider request documents.

Stage 7.7 additionally separates client request serialization, HTTP roundtrip and response parse
for appraisal, plus prompt/output tokens per second when Ollama supplies counts and durations.
Benchmark artifacts contain scenario/run/model/settings identifiers and numeric metadata only;
they omit fixture text, provider requests, retrieved content and generated replies. Scheduler queue
depth is operational metadata, not cognition or user state.

Stage 8.1 adds context/policy versions, primary mode, facet identifiers, bounded coherence flags,
typed response-regeneration reason, duplicate similarity when applicable, generic
`response_regeneration_ms` and generation-attempt outcome to metadata-only diagnostics.
`duplicate_response_detected` remains the duplicate-specific flag.
`self_consistency_violation_detected` contains operation,
reason and interaction ID only; it does not log the style correction, current topic, user input,
prompt or either draft. These fields are request trace data, not durable self, relationship or user
state.

Stage 13 normal/debug telemetry records only inclination/reflection IDs, kinds, counts, versions,
reason codes, bounded numeric influence and timings. Topic/option labels, source quotes, prompts,
provider documents and trajectory content are omitted. Explicit local inspection may resolve
canonical provenance by ID, while export references source/transition IDs and hashes instead of
copying raw user, assistant or provider text.

Stage 14 telemetry adds personality purpose/run/proposal/outcome IDs, trait key, direction,
aggregate/checkpoint/policy versions, reason codes, diversity counts, `D∞`/`D1`/path metrics and
qualitative expression cue codes. It omits source quotes, current/baseline vectors on normal logs,
provider requests, prompts and checkpoint payloads. Full vectors and provenance IDs appear only in
explicit local personality inspect/compare/export surfaces.

## 10. Security and privacy

- Local-first canonical state; cloud receives only operation-scoped minimum context.
- Stage 4 stores accepted user text, committed assistant text, episode summaries and exact evidence quotes as plaintext in local SQLite until an explicit future retention/erasure workflow; this is not production-ready encryption/retention policy.
- Failed interactions retain the exact accepted user input for recovery; no assistant message is recorded unless canonical finalize completes.
- System/developer prompts, serialized character context and full provider requests are not copied into conversation history.
- Stage 3 default Ollama origin is local loopback; selected character context and current input покидают процесс, но не устройство. Изменение base URL на remote HTTP(S) означает явный privacy boundary: этот же bounded payload уходит указанному operator.
- Checkpoint 14.1 Yandex selection is an explicit remote privacy boundary: the operation-scoped
  foreground payload may include bounded current/recent/retrieved conversation and qualitative
  state projections. The credential-bearing transport accepts only
  `https://ai.api.cloud.yandex.net/v1`; background owner inputs remain local.
- ADR-0031 OpenAI selection is the same explicit operation-scoped remote boundary. Credential
  traffic is pinned to `https://api.openai.com/v1`; Responses requests set `store=false`, while
  provider retention policy remains an external account/service concern rather than a local
  persistence guarantee.
- Secrets only through environment/OS secret storage, never state export or repository.
- User input, memories, web pages, files and model output are untrusted.
- All long-term claims retain provenance/confidence; inferred user data never silently becomes fact.
- Semantic aggregates are more sensitive than isolated dialogue: normal logs contain only IDs and
  counts, while explicit local CLI inspect and SQLite retain typed values and complete lineage.
- Semantic retrieval/repetition is read-only and cannot become formation evidence; only new
  canonical Stage 4 root user evidence can strengthen a claim.
- Stage 9 user/world claims are same-counterparty only, keep epistemic labels and deterministic
  freshness, and never treat a counterparty report as independently verified external truth.
- Stage 13 inclination context omits evidence quotes and mutation history; ordinary logs also omit
  topic/option labels. Affect attachments expose only canonical IDs, versions and hashes outside
  the local owner/inspection boundary.
- Stage 14 personality context exposes only an aggregate version and at most two qualitative
  current-vs-baseline cue codes. Evidence, source text, checkpoint vectors and drift history remain
  local explicit-inspection data and never become provider instructions.
- Export/import validates manifest, versions, checksums, identity ID and referential integrity before atomic activation.
- Destructive tools and external actions are outside cognition and require explicit capability/permission checks; read-only first.
- Backup encryption, retention and erasure policies are required before production use, though implementation is later.

Полный failure/threat analysis находится в `threat-model.md`.

## 11. Backup, export and portability

Conceptual `SatoriStateExport`:

```text
manifest { format_version, created_at, identity_id, app_version,
           schema_versions, content_index, checksums }
canonical records { identity, personality, values, positions,
                    people, relationships, interactions, memories,
                    evidence, emotions, self narrative, threads, audit }
optional derived artifacts { embeddings with provider/model/version }
```

Import идёт в staging storage: validate → migrate supported versions → verify references/checksums → preview identity/conflicts → atomic activate. Derived artifacts можно отбросить и перестроить. Экспорт не содержит secrets, active tokens или provider credentials.

## 12. Versioning

Независимо версионируются DB schema, identity/personality/memory/cognition schemas, context templates, mutation policies, embedding model and export format. Audit event фиксирует версии proposal schema и policy. Любая несовместимая перемена имеет migration/rollback plan до commit.
