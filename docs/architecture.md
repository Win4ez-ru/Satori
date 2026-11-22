Current delivery override — 2026-10-19: Character development remains paused. D1 Desktop
Foundation retains [ADR-0079](decisions/0079-desktop-first-companion-delivery.md); the user's
continuation opens bounded D2.1 local push-to-talk under
[ADR-0080](decisions/0080-local-push-to-talk-and-committed-speech.md). Its bounded local engineering
delivery is verified; physical voice and public release gates remain open. Desktop and voice remain adapters over the existing Core, with one canonical
state system. Older version/status descriptions below are historical. See the desktop/voice
boundaries at the end and `progress.md` for current evidence.

# System architecture

Статус на 2026-10-03: реализованы Stage 0–14; Checkpoint 14.1 принят, последний
provider-accepted baseline внутри 14.2 — v10. Character остаётся в Checkpoint 14.3; текущий
implementation candidate — v34 (ADR-0054), рабочая reference — v33. Foundation пройден,
локальные проверки завершены с неудачами; шесть слепых пар ожидают human feedback; provider/human gate
не принят. Stage 15 остаётся заблокирован.
Исторические разделы ниже описывают версии на момент их Stage, не текущую композицию.
Расширенный North Star: ADR-0046, `north-star-audit.md`, `roadmap-north-star.md`.

Parallel Foundations scope (2026-10-02/03): ADR-0053 opens 14.4.1 whole-state recovery independently
of Character acceptance. `application.recovery` owns narrow stored-snapshot/artifact ports;
SQLite backup/strict staged validation and private file publication are infrastructure adapters.
Recovery does not construct conversation/provider services. Current evidence and both active
boundaries are in `progress.md`; format and limitations are in `recovery.md`.

Production continuation (2026-10-03, ADR-0055): Foundations 14.4.2a adds immutable `MemoryScope`
at canonical session/history, episode/index and semantic boundaries. Complete verified lineage
is checked before provider selection and again before projection writes; old unattributed/mixed
records remain quarantined. Scoped keys reuse the semantic manager and conflict/confidence policy.
Character consumes its unchanged bounded eligible-memory projection. Session attribution is not
principal authentication. Contract/migration/recovery details: `memory-scope.md`.

Foundations 14.4.2b.1 (ADR-0056) extends the same MemoryManager with explicit ACTIVE↔ARCHIVED
transitions, metadata-only digest-bound review and atomic version/decision/audit. New episodic
admission remains ACTIVE-only; retained semantic support uses complete canonical proof independently
of episode lifecycle. Conditional writes prevent late index/semantic ABA commits. Standalone
controls compose no provider or Character service. Canonical source exclusion is a separate runtime under ADR-0057; physical erasure and
authenticated access remain separate gates. Contract: `memory-lifecycle.md`.

## 1. Архитектурный стиль

SATORI строится как **modular monolith** с явными domain boundaries и портами к внешним системам. Один процесс и одна транзакционная база — осознанный baseline; границы модулей должны позволять тестировать их отдельно, но не имитируют распределённую систему.

```text
CLI / Qt desktop + PTT draft adapter
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

Это логическая карта, не inventory всех запланированных interfaces. Текущие поверхности — CLI и
native Qt desktop; bounded local PTT проходит отдельную D2.1 verification. Production HTTP API,
avatar и external action adapters остаются будущими capability.

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

The interactive indicator is presentation-only: a TTY cycles `Сатори печатает.`, `..`,
`...` while the foreground turn is pending, while a redirected/non-TTY stream emits one stable
line. Cancellation and every exception path stop and clear the animator before any later output.
For an exact allowlist of transient transport, response-contract and grounding failures the CLI
may print versioned deterministic `[Сатори не ответила]` recovery copy. This is UI chrome,
not a `SatoriReply`: it never enters history, recent context, affect, evidence or post-response
work and never causes provider retry/fallback. Credential, model, quota, rejected request/refusal,
unknown and persistence failures remain explicit; an exhaustive enum-partition test fails closed
when a new provider reason is not deliberately classified.

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
validation и declared-claim gate. A completed envelope with `done_reason=length` is still a typed
`GenerationFailed`: safe metrics remain observable, but the partial text never reaches grounding
or canonical commit.

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
derives a conservative non-reasoning output remainder from total output minus reasoning tokens and
fails closed if that upper bound cannot be enforced or exceeds the visible-reply cap. ADR-0044
clarifies that this is not proof of exact visible-text tokenization; the historical
`visible_output_tokens` metric name remains a compatibility key. Total output remains the billing
usage value. The two caps and token split are transient metadata-only provider metrics, not
persistent state or a new owner. A parsed post-response failure may cross the typed error boundary
with immutable numeric usage and closed observed/completed/tier facts for exact cost accounting,
but never with output text, body, prompt, credentials or raw reasoning; failed calls remain invalid
sample evidence.
Temperature is mapped only when reasoning is `none`; reasoning-enabled calls omit that
provider-incompatible sampling field. An incomplete Response remains fail-closed; only the typed
allowlisted reason `max_output_tokens` or a safe `unknown` value can cross the adapter boundary,
never partial output or an arbitrary provider detail. OpenAI provider conversation state, tools,
streaming and raw reasoning output are unused. The credential is pinned to
`https://api.openai.com/v1`; background capabilities and all
canonical owners remain local/Ollama-only. There is still no automatic fallback or hidden retry.
The immutable digest-bound V27/Terra sample failed direct human provider-fit review; Terra remains
unaccepted rather than awaiting a rerun of that consumed gate. Its next possible evidence is a
paired V27/V28 Checkpoint 14.3 comparison that must be separately planned and authorized.

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
late compact trusted response-strategy section rendered through intent/template registry V1,
template ID `satori.cognition.response-strategy` and schema 1. The section contains only stance/intent/tone/
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

Checkpoint 14.2 keeps policy v19 and `CharacterExpressionPlan` schema v2 as immutable historical
evidence. ADR-0036 then activated candidate policy v20 and plan schema v3 in production
composition.
The plan still consumes only authoritative personality guidance and bounded qualitative
cognition, affect and relationship projections. Stable identity, disclosure, grounding and mode
invariants remain earlier in the request, and exactly one character-realization block remains the
last trusted guidance before the current user turn.

V20 treats the existing semantic move as a factual/continuity anchor rather than the reply's own
content. Three orthogonal request-local axes then select Satori's substantive contribution,
motivational posture and maximum pressure. The closed combination contract prevents, for example,
quiet presence with a protective-stop posture or firm pressure without directly evidenced harmful
overextension. Negated or quoted cues fail closed. Explicit listen-only language and serious
distress disable ordinary motivation; completion plus ordinary depletion permits only one gentle
recovery step and does not establish a hidden cause, unfinished project or remaining workload.

Relationship familiarity may change only relational ease and the realization of already selected
care or wit; it cannot raise pressure. Immediate repetition keeps the repetition anchor, including
when a directly evidenced safety boundary also requires a protective stop. Initiative remains a
contribution inside the current reply, not observer-driven or out-of-band contact; percentage
targets and Stage 19 behavior are not introduced.

The single late v20 realization orders Satori's contribution before its factual anchor and asks
for at most two short complete sentences. The target achievement and listen-sensitive v20 paths
use a 128-token application cap; historical policy caps remain reproducible. This is response
topology, not a template: there is no golden wording or output rewrite. A completed Ollama
envelope with `done_reason=length` raises a typed `GenerationFailed`, preserves safe metrics and
never exposes or commits partial text.

The historical v20 plan remains immutable and has no persistence adapter or domain owner. Manifest
v16 exposes schema-v3 contribution, posture and pressure only as transient `compare=False`
observation fields. Historical v19 evaluators explicitly request schema v2, while the v20
Stage 7.6/8.1 evaluator tracked that production candidate. Its max-one typed retry reused the
byte-identical final realization and did not reselect any axis. Grounding, canonical delivery and
the exact ten-reason validator remained unchanged; no migration or write-back path was added.

ADR-0037 later adds acknowledgement and continuation to request-local plan schema v4. ADR-0038
keeps that schema and derives one pure `CharacterResponseActContract` for candidate policy v22.
The contract collapses overlapping plan axes into one conversational act, one evidence scope and
the existing reference/closure choices. It has no owner, repository or write-back path.

For reaction-only social turns, the final provider block no longer renders the semantic move as a
factual recap. Current/recent dialogue still supplies the referent as untrusted input data, while
the trusted envelope permits only Satori's own reaction and prohibits new user/world assertions,
causal inference, consequences, timelines, intentions and further-work claims. Precise answers may
still use supplied trusted context; other ordinary acts remain limited to explicit current input.
Historical v19-v21 renderers remain explicitly selectable by their policies. Generated text is
still canonical and unrewritten, and retry reuses the same selected act/evidence envelope.

ADR-0039 introduces plan schema v5 rather than changing v4 in place. Ordinary explicit depletion
now selects a practical move with bounded supportive pressure, while serious distress and an
explicit listen-only request keep quiet-presence precedence. The derived response-act schema stays
unchanged; only a schema-v5 practical move receives `explicit_input_only` grounding. Candidate
policy v23 renders one late four-part action/evidence/voice/stop block instead of concatenating the
historical anchor, reaction, register, care, wit, motivation and relationship prose. The plan is
still transient, provider output is not fed back into it and the exact max-one retry reuses the
same rendered block.

ADR-0040 keeps v19–v23 reproducible but replaces their current-candidate realization path for
policy v24. V24 derives one immutable `CharacterDeliveryDecision` directly from the completed
typed response strategy, qualitative affect, bounded relationship projection and narrow
current-turn signals; it creates neither a legacy `CharacterExpressionPlan` nor a derived
`CharacterResponseActContract`. Policies v10 and v19–v23 keep cognition intent/template registry V1
with template ID `satori.cognition.response-strategy` and schema 1. Only v24 selects intent/template
registry V2 with template ID `satori.cognition.response-substance` and schema 2. The decision copies
the cognition registry, primary intent, ordered tags, required point codes, complete
forbidden-claim boundary, response verbosity, stance and uncertainty exactly. It fails closed when
strategy/intent is absent, registry or template identity drifts, a required cognition field changes,
or the selected goal/voice/grounding/continuation/pressure topology reverses the cognition position.
It is transient manifest
metadata only, with no repository, manager, persistence or provider write-back path.

The implementation keeps those change reasons separate: `character_delivery_contracts` owns the
closed schema and invariants, `character_delivery_selection` owns the pure policy choice, and
`character_delivery_rendering` owns provider-facing wording. `character_delivery` is only a thin
compatibility facade. This preserves the public application API without combining contract,
selection and rendering into a new god module.

The v24 provider projection has one cohesive baseline derived from canonical personality and
exactly one final character director. That director jointly renders goal, voice, grounding,
continuation and pressure. The V2 response-substance template renders cognition-owned intent/tags,
required points, forbidden claims and verbosity inside this same director. Cognition remains the
authority for response substance, stance and uncertainty; the historical V1 strategy prose is not
rendered as a second competing reply-shape block. The legacy plan, response-act and realization
blocks are likewise absent from a v24 request.

Grounding and initiative are separate axes: grounding limits what the reply may claim, while
continuation controls whether the same foreground reply completes, advances, opens, guards or
closes the exchange. Reaction-only grounding can therefore support a personal celebration or a
new conversational direction without inventing event details. Ordinary depletion permits presence
first and at most one optional low-cost suggestion grounded in the current input; explicit
listening or serious distress remains presence-only. Relationship and affect may modulate warmth,
wit, openness or reserve, but never truth, uncertainty or important practical/technical help.

The current `RelationshipExpressionContext` v2 adds only a closed request-time `recent_strain`
boolean. `GetRelationshipForSession` derives it from at most the latest two owner-committed
relationship transitions ordered by canonical `resulting_state_version`: it remains true for a
latest applied negative transition or for a latest repair immediately following a negative
transition from the same relationship aggregate with no processed-interaction gap,
provided the current state's processed-interaction count still equals the latest transition's
after-count. A subsequent terminal processed source ends the arc. True strain selects the effective
profile `guarded_only_when_relationally_relevant`. V24 considers this projection only on an explicit
repair or an answer-required turn; vulnerability precedence remains intact,
and important help becomes guarded rather than withheld. Relationship mutation is still strictly
post-response and future-turn-only. The provider sees the effective profile plus the boolean, never
raw categories, deltas, transition IDs or numeric axes; the projection is not an offense state or
replay authority. Restart reads reconstruct the same boolean from committed transitions, while a
replayed terminal source neither calls the provider nor creates a second transition.

V2 makes three cross-cutting moves cognition-owned rather than character exceptions. Directly
evidenced harmful overextension selects `hold_safety_boundary`; otherwise an exact repeated turn
selects `notice_repetition`; otherwise a clean current-user repair offer may select
`receive_repair`. Safety therefore precedes repetition, and repetition precedes clean repair. A
question, request, correction or challenge cannot be erased by a repair cue. Existing relationship
projection and relevant current guarded evidence may keep reception reserved; V2 forbids an instant
false-warmth reset.

V24 adds no output rewriting, phrase substitution, judge model, retry, state owner or Stage 15
capability. The existing max-one retry reuses the same director byte-for-byte. Its architecture is
subject to the broad offline and employer-demo gates described in `evaluation.md`. The final
employer-demo decision is digest-bound to four distinct module reports and their exact human
reviews under one shared production configuration; one module cannot accept readiness. Non-
generation replay may omit the transient cognition/delivery projection, but never promotes it to
state or uses it to authorize another generation. The later paid v24 `core_emotional` module is
rejected evidence and does not satisfy the four-module gate. Stage 15 remains locked.

ADR-0041 historically made policy v25 the candidate while leaving v24 reproducible. V25 keeps
intent registry V2, requires cognition template registry/schema V3 and requires
`CharacterDeliveryDecision` schema 2. A dedicated typed disclosure contract carries social
current-affect checks, reciprocal warmth and all directly requested self facets, including
`interests`. Its closed `DisclosureRequestKind` distinguishes a request about Satori from an
ordinary social reply: only `SATORI_SELF` produces `SELF_DISCLOSURE_REQUEST`; reciprocal warmth
remains `NONE`. Cognition keeps explicit listen/high-distress/safety precedence; otherwise the
self-request signal selects an answer from trusted self state rather than misreading Satori's
requested affect as a need to console the user.

Schema-2 delivery adds `social_connect`, `self_disclose` and the exact required disclosure facets.
The provider receives one cohesive personal disclosure rather than a state inventory, trait list or
assistant greeting. Missing stable interests remain missing; only owner-approved inclinations or
general current curiosity may be expressed. Cognition template V3 additionally forbids paraphrase,
diagnosis and causal psychology on listening turns, and makes presence-before-advice conditional on
advice actually being selected. A narrow `depletion_follow_through` signal recognizes a direct
stop/defer choice only after the immediately preceding canonical user turn explicitly reported
depletion; it cannot prescribe a second recovery plan.

Migration `0013_conversation_failure_reason` adds a closed provider-neutral reason to new failed
interactions together with safe provider/model identifiers. Raw error messages, response bodies,
partial text, prompts, user content and credentials remain outside persistence. The historical
manual failure can therefore be recovered only as `InvalidProviderResponse`; its exact reason is
unknowable. Future failures distinguish transport/configuration, terminal generation state and
response/adapter contract violations without adding automatic retry or fallback. The real
relationship backlog is likewise untouched: `satori relationship process --limit N` is an explicit
oldest-first, bounded call through the existing Stage 8 owner, not startup work or a migration.

The later separately authorized v25 exact-manual OpenAI gate completed three fresh sessions ×
three turns on 9/9 first-attempt calls. Its 13,748 input and 733 output tokens produce a repository
standard-rate estimate of USD 0.036292; the historical artifact did not retain cache-detail usage,
so this is not a cache-verified exact invoice. It proves the typed social/self-disclosure wire and
closes the historical missing-reply route, but character quality remains unaccepted:
replies repeatedly announced calm/level affect, explained missing stable hobbies and added
polished abstract observations. That sample is historical evidence, not an active prompt target.

ADR-0042 historically made policy v26 the offline production-composition candidate. The architecture
audit found that v25 discarded live personality guidance strengths and all current values after
context assembly, flattened affect/relationship owner reads into coarse profiles and stacked a
canonical-character core, affect/relationship prose, cognition checklist and late director. The
foreground model therefore received a generic, overlapping instruction stack even though the
owned runtime state was richer.

V26 preserves cognition intent registry V2 and template registry V3, advances
`CharacterDeliveryDecision` to schema 3 and adds one frozen `CharacterPresenceProjection` schema
1. The deterministic bridge selects at most three live personality signals, three current value
signals, three qualitative affect signals and three qualitative relationship signals, plus an
exact memory-use license and bounded availability of canonical positions and owner-approved
inclinations. The memory-use license is true only for an actually retrieved memory under final
`trusted_context` delivery grounding, not merely because memory exists or retrieval ran. It then
renders exactly one late `Trusted current-turn presence Сатори` layer. Complete numeric vectors
remain inside their owners; only selected personality/value strengths enter the frozen projection,
and provider/manifest delivery uses bounded qualitative codes, levels and an optional personality-
evolution direction. Historical v25 rendering stays reproducible.

Fresh v26 manifest observability requires decision schema 3, presence schema 1 and mutually
consistent bounded signal tuples. Its exact `character_presence_memory_use_licensed` boolean must
match retrieved-memory status plus final trusted-context grounding. Canonical trait/value presence
meanings are defined once by the runtime-self mapping, and the hardened `RuntimeCharacterContext`
boundary rejects blank/duplicate keys, bool-as-number values, non-finite strengths and values
outside `[0,1]`. Non-generation replay may omit the complete transient decision and presence
projection, but can never promote them to state or generation authority. Provider output has no
write-back route. No owner, mutation path, output rewrite, judge model, extra retry or fallback is
added.

The separately authorized attempt-5 sample later rejected the frozen V26/Terra composition. It is
historical provider evidence, not a phrase target or proof of a Terra model ceiling.

ADR-0043 made policy v27 the then-current offline production-composition candidate inside
Checkpoint 14.2. That now-rejected historical policy advances the transient decision/presence
schemas to 4/2 and reverses the V26 explanation-after-selection order: cognition, complete
validated runtime personality/values,
current affect, scoped relationship and narrow request evidence select one movement before any
provider prose is rendered. Canonical values remain equal immutable guards; live variation is not
falsely attributed to value drift. Personality/value operational meanings are defined once in the
runtime self mapping and reused by the renderer.

V27 adds only two request-local goals: a direct objection grounded in an immediately preceding
canonical assistant turn and a narrow complete topic closure. Assistant history remains untrusted
conversational context and never becomes a canonical position. Safety, listen-only vulnerability,
repetition, repair and relevant guardedness retain priority. Closure may use relationship only for
bounded ease/reserve and continuation; it cannot invent shared history or create global punishment.
Ordinary pressure-free depletion has no default advice and a 96-visible-token ceiling.

The one `Trusted current-turn presence Сатори / operational move v2` block carries one movement,
one voice, directly operational personality effects, exactly one value guard, relevant qualitative
state, the complete cognition-owned point/support/uncertainty/forbidden-claim boundary, grounding
and one ending. Across six identical public inputs it is 6,465 characters versus V26's 11,866
(54.48%). All 40 historical V26 public projections remain byte-stable as an aggregate, and the
historical paid runner is retired before settings, claims, provider construction or network.
V27 changes no foreground port, persistence owner, mutation path, validator reason or retry count.
The separately authorized V27 attempt-2 sample completed 24/24 base calls without retry. Direct
human-only review rejected it: sampled grounding/completeness are strong, but recognizable Satori
presence passes only 6/24 and natural delivery 9/24. Technical correctness does not accept provider
fit.

Stage 15 remains locked: autobiographical state would add another fact source but would not repair
the expression bridge.

ADR-0045 opens Checkpoint 14.3 after that rejection. Policy v28 inserts a pure typed
`CharacterAgencyDecision` between immutable state reads and cognition completion. The decision
selects one current drive, act, subject, initiative scope and lead order from personality/values,
current affect/relationship, prepared cognition intake and supplied owner-approved
position/inclination state. It is neither a persistent goal nor evidence, has no repository, and
expires with the foreground interaction. Cognition still owns every required point, forbidden
claim, safety boundary, stance and uncertainty; delivery schema 5 merges those obligations with
the already selected agency act into one presence-schema-3 provider movement. Retry reuses the
same bytes and provider output cannot feed back into either decision or state.

If cognition completion itself enters fallback after an applied intake, policy v28 replaces the
earlier move before rendering with its only conservative fallback agency topology. Delivery
selection and manifest schema 17 require exact cognition/agency status parity; the fallback does
not erase cognition-owned safety, repetition or uncertainty obligations.

For a complete-topic turn only, v28 may broaden the existing inclination read to at most the
bounded strongest positive owner-approved `interest`. The exact inclination ID and supplied state
remain the only license for an adjacent owned topic; no inclination means current-topic engagement
or a natural stop, never an invented hobby. Activation seed v1, Stage 13 formation rules and every
persistent table remain unchanged. Seed-origin interests require a separate Phase B decision and
are not part of this architecture.

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
| Stage 10 perception/need mix/position/intent/strategy | Yes | No | Replaceable planner port; intent V1 retained for v10/v19–v23, intent V2 for v24–v33; substance template V2 for v24 and V3 for v25–v33; v28 preserves its complete validated support after an upstream request-local agency choice; explicit conservative fallback |
| Current-turn character agency/presence | Yes | No | V28–V33 select one transient schema-1 agency decision before cognition completion, then merge it with unchanged cognition obligations in delivery/presence schemas 5/3; V32/V33 prevent direct-stop interest pivots; existing personality/value/affect/relationship/position/inclination owners remain authoritative; no state write, extra provider call or provider judgement |
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

Checkpoint 14.2 v26 manifests add only presence schema version and bounded qualitative
`code:level[:direction]` signal identifiers for personality, values, affect and relationship. They
also add the exact memory-use-license boolean; they do not store live numeric vectors, rendered
presence text, provider prompt or reply. The signals and license are request diagnostics, not
replay authority or a second state snapshot.

Checkpoint 14.2 v27 manifests version the same transient boundary as decision/presence schemas 4/2,
require exactly one bounded value guard and retain the exact memory-use-license parity. They store
neither the rendered operational-move block nor generated prose and cannot replay a fresh
generation or mutate an owner.

Checkpoint 14.3 fresh policy-v28 generation advances only the manifest boundary to schema 17 and
records the schema-1 agency decision metadata together with delivery/presence schemas 5/3. The
manifest carries closed drive/act/subject/initiative/lead/reason codes and exact selected canonical
source references, never prompt prose or a new state snapshot. Historical policies through V27
remain on manifest schema 16 and reject schema-17 agency authority; non-generation replay may omit
the complete transient agency/delivery/presence set but cannot synthesize it.

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
- File-backed local SQLite state and runtime JSONL logs are created with mode `0600`; parent
  directories created by Satori use `0700`. Existing parent-directory permissions are not changed
  implicitly. Symlink or non-regular final targets fail closed; in-memory and non-SQLite database
  configurations keep their existing behavior.
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
- 14.4.1 implements bounded exact-schema SQLite whole-state export, staged checksum/schema/FK/
  identity validation and digest-bound restoration to a new absent target. Current aggregate JSON
  exports remain partial inspection, now using private atomic artifact publication. Live replacement,
  erasure, encryption and principal enforcement remain later gates; see `recovery.md`.
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

The recovery implementation retains optional embeddings and supports exact 0013–0020 schemas
via separate clean references (ADR-0055/0056/0057/0060/0061/0063/0066). Offline restore never migrates;
ordinary explicit use of an old restored DB invokes its normal migration/quarantine policy.
Unknown versions and logical cross-engine export are rejected. The whole-state
bundle contains stored SQLite plus strict metadata. Offline new-target activation preserves every
canonical row/audit without calling owners or resuming jobs. It does not serialize Character
renderer/version-specific prompts or transient agency/coherence state. See ADR-0053 and `recovery.md`.

### Canonical source policy — implemented under ADR-0057

[ADR-0057](decisions/0057-canonical-source-exclusion-and-trusted-policy-floor.md) defines an
independent installation-local eligibility ledger, derived SQL mirror, bounded cross-process
admission/commit barrier and owner-routed withdrawal. These are implemented independently of
recovery/archive byte transport. The floor remains outside an old main-DB bundle; operational
admission is separate from byte-preserving transport. Mirror lag, owner-repair lag and incomplete
content provenance have distinct fail-closed outcomes. Existing owners retain content/self;
Character continues to consume bounded eligible projections. The
consumer matrix includes actual uncited provider inputs,
paired assistant extraction, reflection context, replay/finalize and later delivery.
Full Foundation and macOS process/scenario gates passed; the actual Linux process gate remains
open. [Source policy](source-policy.md) documents runtime controls and retained-data limits.
Physical erasure and access/encryption remain separate slices.

[ADR-0091](decisions/0091-reviewed-device-binding-recovery.md) adds an explicit offline,
owner-reviewed generation-zero adoption command for device-only descriptor drift. A typed,
digest-bound metadata plan delegates the two-scalar descriptor update to the existing ledger
owner/publisher, with an exact backup, durable one-use claim and separate receipt. Canonical,
floor and lock contents remain unchanged. Ordinary admission retains exact device/inode-pair
validation and never invokes adoption. Matching metadata is not proof of the previous volume;
the operator's explicit trust decision and runtime quiescence remain required.

ADR-0059 opens read-only erasure planning through ErasureInventoryPort and ErasurePlanner.
The SQLite adapter inventories the exact current logical schema under a consistent read lease;
public routes contain owners/table/field names and counts, while one combined store digest binds
all versions/non-versioned rows. A separate routing digest binds actual internal primary keys
and dispositions without exposing descendants. Fresh checking grants no execution permission. The planner
introduces no content/state writer and no provider/Character dependency. Unknown text carriers,
retained numeric history and external retention surfaces are explicit. Future physical owners
must use a floor-first terminal intent; see [erasure](erasure.md).

## Optional previous-exchange appraisal context — ADR-0064

Character's opt-in appraisal context uses one exact canonical completed pair from already loaded
eligible history, verified through existing InteractionLog reads. The bounded immutable core
projection has fixed user/assistant roles and a 2000-character combined content limit. Appraisal
input 2 remains distinct from categorical output 2 / semantic proposal 1. Absent context preserves
the old input and provider wire; default foreground V35, provider/model and state owners remain.
History is interpretation context, not fresh relationship/self evidence or allowed mutation refs.
Existing actual-input admission binds prior USER and assistant-reply dependencies through
dispatch and final commit, including a context-bearing attempt without an affect transition.
No new history/admission port, persistent state, migration or normal-turn inference call is added.

## 12. Versioning

Независимо версионируются DB schema, identity/personality/memory/cognition schemas, context templates, mutation policies, embedding model and export format. Audit event фиксирует версии proposal schema и policy. Любая несовместимая перемена имеет migration/rollback plan до commit.

## 13. North Star extension: planned, not implemented

ADR-0046 preserves all existing owners. The future application path is:

```text
consented source → normalized external event + immutable provenance
→ source-eligible World/User/Memory owner proposals → validated snapshots
→ cognition / bounded task decision → typed action proposal
→ technical grant + risk + payload-bound confirmation
→ executor → observed result / uncertain outcome reconciliation
→ atomic local audit + owner-specific evidence proposals
```

`Source/Event` owns ingestion identity, correlation and correction lineage; `WorldModelManager`
owns claims, not raw provider feeds. `PermissionManager` owns user-issued grants/revocations;
`ActionCoordinator` owns execution lifecycle, not personality or relationship. These are proposed
future responsibilities, not existing classes. Credentials live behind a secret-reference port;
neither prompt nor evidence contains usable tokens. Permission is rechecked at execution, including
after confirmation and retry. An external side effect cannot be atomic with SQLite: persist intent,
use provider idempotency when available and reconcile an unknown result before retrying a write.

Current user-message-only evidence FKs and policies must not be bypassed by inserting fake chat
messages. New sources need a versioned origin/scope contract and separately approved owner
eligibility. External data cannot by default train relationship, personality or inclinations;
self-authored action receipts cannot become independent corroboration. Relationship trust is
never an input that grants capability access. Read permission and external content authority are
different concepts: a permitted email can still be malicious or wrong.

One local canonical writer remains the default. Future devices submit authenticated commands and
cache versioned projections; offline pending operations are not independent personalities or
grants. No broker, generic event-sourced rewrite or microservice split is required by this plan.

## V29 expression calibration inside Checkpoint 14.3

ADR-0047 replaces active provider realization, bounded colloquial closure-cue recognition and
V29 classification of mixed affect/relationship probes into the existing relational reply route,
and correction selection that respects cognition intake precedence.
Policy V29 retains agency/delivery/presence/manifest schemas 1/5/3/17 and cognition intent/template
versions 2/3. The existing decision's voice and personality directions are rendered with relevant
owner affect and relationship state. Ordinary social/interest/affect/achievement/depletion/objection
turns can project established familiarity; missing relationship state remains unavailable and
unrelated technical requests keep identical expression. Distress, listen, correction, repair,
truthful attribution, cognition completeness and stop bounds take precedence over teasing.
Historical V28 request bytes and frozen evaluators remain isolated; no persistent schema or writer
changes. V29 was the implementation default at this checkpoint; ADR-0049 introduces V30 below.

## V30 expression calibration inside Checkpoint 14.3

[ADR-0049](decisions/0049-understated-contextual-expression.md) versions the same peer renderer
and makes V30 the implementation candidate. The selected lead order and full cognition boundary
are realized in one reply, without an obligatory second explanatory passage. Contextual self
disclosure, objection, care and understated closure guidance replace V29 guidance only when
V30 is selected. Agency selection, schemas, state owners, grounding, parameters and retry limits
are unchanged; the new final-block marker participates in the existing byte-preserving retry.
V29 request bytes remain reproducible. Neither default selection nor tests imply human acceptance.

## V31 selected conversational initiative

[ADR-0050](decisions/0050-selected-conversational-initiative.md) makes V31 the implementation
candidate. Only an existing ordinary `advance_current` / `current_exchange` opening with eligible
goal and affect receives more concrete conversational-realization guidance. The same integrated
renderer carries complete cognition obligations and unchanged lead order. Selection frequency,
canonical-interest requirements, persistent owners, schemas, parameters and retry behavior remain
unchanged. Historical V30 rendering stays byte reproducible. The user target of occasional
initiative is evaluated separately from selected-opportunity counts.

## V32 compact reaction and explicit-stop bounds

[ADR-0051](decisions/0051-compact-owned-reaction-and-closure-bounds.md) versions one compact
provider realization of frozen act/subject/initiative/lead and canonical personality/value
directions. The existing cognition support renderer remains complete and appears once; relevant
factual, grounding, affect, relationship and protected-request bounds remain authoritative. Optional
casual topic selection does not grant the provider agency selection or persistent writes.

`CharacterRequestEvidence.closure_kind` is a defaulted typed request-local projection introduced
for V32 and retained in V33. Whole-turn grammar distinguishes `end_discussion` from `claimed_resolution` without
reading history or judging answers. Explicit stop suppresses broad adjacent-interest projection
and pivot at the existing closure priority. Higher existing safety/listen/correction/repair/
guarded priorities remain; claimed resolution keeps the previous bounded canonical-interest option.
Recent dialogue supports realization but creates no Stage 17 unfinished-thread owner. Historical
V31 requests, schemas 1/5/3/17, cognition 2/3, all writers, generation caps and the shared max-one
retry remain unchanged. The V32 final-block marker participates in that existing retry path.
The V32 record retains completed technical verification,
native failures and Diagnostic 7 feedback; no character acceptance followed.

## V33 goal-local news and care realization

[ADR-0052](decisions/0052-owned-news-and-care-realization.md) replaces active compact V32
expression with the V31 provider envelope, principles, ordinary-initiative guidance and restrained
closure plus two goal-local clarifications. Ordinary `acknowledge/current_exchange` with
`celebrate_and_continue` can use a brief own positive evaluation without replaying event facts;
an already selected `advance_current` can develop a specific question or relevant useful help.
Ordinary `care/user_explicit_state` with `practical_care` can express presence through direct
participation, without a compulsory recap, feeling explanation or advice passage. This changes
realization only, not the selected movement or a protected request route.

Matching act/subject/goal alone is insufficient: combined news or depletion with an explicit
task can retain those movements. The existing cognition-derived `answer_required` is passed as
a transient `protected_request` renderer argument. Typed `REQUEST`/`QUESTION` obligations exclude
both V33 clauses and retain unchanged V31 guidance. Historical rendering through V32 ignores the
new argument; no contract schema, evidence parser or selector changes.
Because existing request-cue recognition is incomplete, both clauses also condition short
reaction sufficiency on absence of factual/explanation/work requests in the same message. Such
requests retain complete delivery even if their wording does not acquire a typed signal; this
provider instruction is a bound, not semantic detection or fulfillment evidence.

On V33's actual `close` movement with `claimed_resolution`, the exact V32 claimed-resolution
guide accompanies the V31 restrained-ending guide. Current typed evidence is required by the
V33 renderer; other acts/kinds cannot acquire this clause. History supports realization without
an answer detector, demand to repeat a question or persistent pending-thread state.

Full cognition support remains complete and rendered once. V33 retains V32's conservative typed
closure grammar/direct-stop bounds, narrow completeness-prefix correction and genuine mixed
style/direct-self scope support. Historical branches, schemas 1/5/3/17, cognition 2/3, self/state
owners, native parameter policies, validator reasons and the shared max-one retry remain.
The new expression marker uses that same retry path; no persistent state, migration, extra model
call, judge or output rewrite is introduced. Verification
records Foundation success and pending local terminal evidence; the frozen paid diagnostic
still requires a new grant. Preference for V31 in
Diagnostic 7 is a delivery reference, not character acceptance or proof of a selector bug.

## Character V34: mixed affect at the expression boundary

ADR-0054 replaces only the active peer renderer's coarse affect sentence with the existing
ordered at-most-three `CharacterAffectSignal` values and qualitative levels. The same frozen
agency/cognition chooses the action; these shades jointly influence expression within it.
`EmotionManager` keeps numerical state, appraisal, decay and canonical commit;
`RelationshipManager` retains separate counterparty evidence, post-response processing and
recent-strain authority. An affect vector does not establish offense, cause, target or relational
change. No owner/read schema or persistent writer changes. Historical V33 requests remain exact.
Foundations recovery under ADR-0053 consumes storage contracts independently of this projection.

## Character V35: mixed affect before reaction freeze

ADR-0058 supersedes active V34 selection invariance. One pure request-local projection normalizes
existing tension/frustration/negative-valence excess by owner baselines/event caps and derives a
bounded readiness budget. Agency keeps current interest/motive while bounding discretionary
advance; compatible ordinary voice uses the same canonical projection, and optional continuation
respects the frozen agency instead of reopening solely through familiarity. Cognition obligations,
current appraisal and independent relationship authority remain unchanged. No persistent state,
owner/read schema, provider call, output rewriting or causal-offense claim is added. Historical
V34 requests remain exact; verification is tracked in the V35 record.

## Optional contextual repair targeting before agency

[ADR-0073](decisions/0073-contextual-repair-targeting-before-agency.md) adds a separate default-off
V35 interpretation candidate. [ADR-0075](decisions/0075-repair-frame-and-addressee-before-admission.md)
replaces only its semantic wire/admission with v2 focal speech-act/frame/addressee/basis fields.
Application-derived supported direct repair toward Satori admits the cue; valid material
uncertainty withholds it even when the lexical cue is true. These categories do not prescribe
emotion or forgiveness. It reads canonical current USER and the optional exact latest pair,
without emotional or relationship state as evidence of intention. A supported repair or other
classification sets the same transient cue in Character evidence and rebuilt cognition intake.
The retrieval plan must remain identical; final agency precedes completed cognition as before.
EmotionManager appraisal and relationship ownership retain their independent authority.

[ADR-0076](decisions/0076-conversational-agreement-before-repair-targeting.md) changes only the
opt-in interpretation protocol: the untrusted prior pair is presented before the current turn,
and the proposed activity/current contribution establish the focal frame before targeting.
An invited fragment need not repeat a draft label; current cancellation, live repair or topic
change can supersede the prior activity. A prior invitation is context, not a user obligation
or technical grant. The same v2 dimensions and paired source quotations carry the result; no
new activity state, owner, semantic call or expression rule is introduced. Model understanding
requires a new separately authorized target gate; scripted transport checks do not establish it.

The cognition owner's existing pure position selector also governs eligibility: only applied
ANSWER intake admits this candidate; uncertainty, listening, collaboration and fallback retain
their route. Recognized obligations and closure skip interpretation. Missing latest context cannot fall back
to an older exchange. Exact quote validation establishes reachability, not semantic accuracy.
Complete source admission precedes actual transport, follows the operation and protects commit.
Explicit compound abstain, invalid output and ordinary outage preserve baseline; valid material
uncertainty sets the effective cue false. Source errors fail the interaction.
The existing foreground retry reuses the interpretation; replay performs no new call. Separate
numeric metrics and the content-free reply trace, including the four v2 categories, are transient,
with no new persistent schema.
The candidate is isolated from current-position, prior-dialogue-relation and appraisal-history
experiments. Disabled wire requests remain exact; native and target-API/human gates are separate.

## Optional prior-dialogue relation before agency

[ADR-0069](decisions/0069-prior-dialogue-relation-before-agency.md) opens a separate isolated V35
candidate. A deterministic baseline preview limits eligibility, then a single complete source-input
receipt precedes one semantic relation operation. Application quote validation binds canonical IDs
and deterministic offsets. Fresh admission precedes final agency selection; only a supported
still-relevant prior move can license an ordinary owned-move-first lead. The final selection precedes
completed cognition compatibility and the unchanged foreground/retry/finalization path.

The history owner exposes one additive content-free scoped latest-completed header read. It does
not admit body content, alter existing readers or add a write path. An unavailable latest slot cannot
be replaced by an older question. No persistent thread, emotion, relationship or personality source
is added. Invalid/abstain/material/outage preserve baseline; source-policy failure remains fatal.
The flag is default-off and incompatible with the current-position/appraisal-context candidates.

## Optional current position before expression

[ADR-0067](decisions/0067-bounded-current-position-before-expression.md) introduces a default-off
V35-only content experiment after existing agency and completed-cognition compatibility. One
explicit semantic operation receives the existing immutable request and its complete admitted
source footprint. A strict none/view proposal supplies at most one provisional thought to the
final generator as distinct untrusted data. Existing act/subject/initiative/lead, canonical state
and cognition obligations remain authoritative. Fresh enabled metadata uses manifest 19 without
proposal prose; semantic usage/timing is separate, retry reuses the datum, replay omits it.
No owner, migration, persistent position, affect/relationship write, tool or output rewrite is
added. This isolates content realization; it does not prove agency choice or emotional continuity.

## Character V37: context use in generic owned replies

ADR-0065 adds an opt-in expression candidate from V35, replacing one movement line only for
applied `owned_response/respond/current_exchange`. Supplied facts are shared premises rather
than compulsory introductory reply parts; requested recap, quote and useful premise repetition
remain valid. Agency selection, complete cognition support, grounding and native caps remain.
V37 uses existing agency 2 / manifest 18 structures with no cue; V36's cue behavior is V36-only
and the production V37 builder rejects a supplied cue. Historical V35/V36 rendering remains
reproducible. No additional model call, owner, persistent state, rewrite or retry reason is added.
Default V35 and the optional appraisal-context candidate remain independent. The implementation
does not establish reduced recap or Character acceptance without sampled target-provider review.

## Canonical source admission — Foundations 14.4.2b.2

SourcePolicyManager owns the independent installation ledger and derived SQL disposition mirror.
Operational UoWs acquire a short cross-process shared lease before transactions and bind to the
current independently trusted floor. Selection, immediate dispatch after scheduler/pool waits,
retry, final commit and replay use the same actual-input provenance contract. No locks span
provider awaits. Exclusion takes the exclusive lease and commits the floor first, then materializes
the DAG and routes typed withdrawal through existing owners. Owner repair generations are separate
from mirror freshness. Missing floor/receipt/current owner version denies, rather than using an
older allow projection. Cache/retained vectors carry no independent disclosure authority.

Migration 0016 stores only main projection/receipts; the independent policy_0001 ledger is excluded
from ordinary whole-state bundles and its files are reserved recovery/export targets. A selected
older main backup must reconcile against the current installation floor before providers/jobs.
Historical numerical self/relationship state remains; text-bearing unsafe knowledge is withheld
and retired by its own owner. Character consumes the same eligible context contracts, without
version-specific coupling. Full contract and operator controls: [source policy](source-policy.md).


### Bounded owner body handling — ADR-0061 / 14.4.2e

MemoryManager retains immutable episode/evidence headers and stores terminal summary/quote
bodies as SQL NULL. RetrievalIndex removes every vector space for approved targets. Ordinary
domain records keep nonblank content contracts; unavailable bodies use a separate historical
header and cannot enter retrieval, processing, reactivation or new evidence. Production 0018
SQL guards prevent body resurrection, header mutation and later quote/vector insertion.

A separate SourcePolicyManager body action/revision and opaque whole-head digest precede
mutation. Main application receipts and body bindings acknowledge verified effective targets,
independently of source repair and intent preparation. Fresh approvals bind exact private
selectors/preimages/terminal skeletons; one bounded transaction calls the two existing owners.
Restore/replay validates actual NULL bodies, exact evidence headers, retained numerical fields
and vector absence. Changed targets require a fresh action; superseded approvals stay immutable.
Controls compose zero providers and depend on source eligibility, not Character implementation.
See [body handling](erasure-bodies.md) for coverage, limits and remaining physical surfaces.

### Retained audit metadata — ADR-0071 / 14.4.2h

Existing owner transactions append a prospective companion binding through one shared typed
infrastructure path. Main0023 adds no columns to `audit_events` and no independent policy/action
head. Closed event shapes and exact owner/identity/canonical scope/cardinality proofs admit
retained structural/numerical metadata; unbound legacy, unsupported prose and ambiguous scope
remain UNKNOWN. Supplied actual-input lineage is checked without granting learning authority.

The scoped read-only coverage port composes source admission and an indexed bounded inventory,
without providers or Character implementation dependencies. Metadata, available Appraisal content
and UNKNOWN rows have distinct existing-owner routes. Terminal Appraisal uses ADR-0070's complete
mixed-head/paired-material/input proof. Coverage never substitutes for source/body admission,
permissions, physical erasure or authenticated access. See [retained audit metadata](retained-audit-metadata.md).

### Whole-store retention and readiness — ADR-0074 / 14.4.2i

The read-only identity-scoped census assesses every logical row in all 60 admitted current-main
tables through the existing 15 owners. Each row has one category: supported retained material,
declared owned content or UNKNOWN. Structural metadata, numerical history and terminal bodies
are separate overlapping facets; complete coverage may still contain owned content. Closed
owner/field-role contracts classify exact shapes and references without treating scalar knowledge
as historical numerical state. Embedding vectors have their own bounded finite, nonzero
1–8192-dimension contract, separate from ordinary structured metadata limits.

The inventory verifies the installation, source mirror and actual complete six-action body head
inside the same read transaction before loading its census, including when no runtime callback
is supplied. One transaction-local indexed `RetentionContext` binds the schema, snapshot, exact
store and row material; it cannot certify another store or a later head. The strict public path
denies stale, pending or tampered admission. Existing privileged owner preparation has a separate
historical-assessment factory: verified floor actions permit attribution during recovery, but
pending material receives no current terminal-body badges and never advances the body head.

Fresh `status`/`check` bind the reviewed scope, heads, schema, full logical material and exact
classification. They neither reserve execution nor issue a grant. Physical erasure remains false
and physical readiness remains blocked. This slice adds no writer, durable readiness state,
ledger, migration or Character dependency; historical action and preparation-v1 formats remain
unchanged. See [retention contracts and controls](retention.md) and
[ADR-0074](decisions/0074-whole-store-retention-and-readiness.md).

### Terminal dialogue and historical attribution — ADR-0063 / 14.4.2f

InteractionLog adds terminal SQL NULL for selected canonical USER, paired and COMPLETE-dependent
assistant bodies. The explicit policy_0004 upgrade extends the same single body head with a
closed dialogue action, preserving old episode hashes. Main 0019 adds owner/SQL guards and keeps
normal contentful domain types distinct from metadata-only headers. Actual terminal bodies,
retained skeletons and pre-erasure evidence witnesses must match the trusted complete mixed head;
ACKs alone do not admit restored data. One shared coordinator invokes existing owner writers and
acknowledges the whole head atomically. Pre-commit faults roll back main changes while the floor
decision remains durable. Uncertain commit or post-commit verification can leave fully terminal
bodies with a pending result; status/resume require fresh actual proof of the effective approved
whole head. Pending does not certify body availability.

Exact private retained-row witnesses permit historical inventory/audit after canonical NULL,
never new learning, provider input or grants. History filters available completed pairs before
limits; independent available USER extraction is preserved. Terminal replay cannot regenerate a
reply, and post-response checks headers before contentful reads. This boundary changes no
Character state or implementation contract. See [dialogue handling](erasure-dialogue.md).

### Terminal derived knowledge — ADR-0066 / 14.4.2g

The explicitly opened knowledge vertical extends the same mixed body coordinator and durable
floor. Existing semantic/current-model/position owners write exact reviewed NULL transitions;
normal domain types remain contentful and unavailable records expose typed metadata headers.
Full parent/evidence/revision fingerprints retain numeric trajectories, versions and membership.
The explicit policy_0005 envelope proves old Dialogue witnesses transitioning to terminal
position evidence, including live same-scope evidence whose canonical USER is still available.
Later Dialogue actions preserve that historical proof without authorizing live learning.

Main0020 admission proves the exact owner/schema and complete mixed head. Ordinary reads,
current-input admission, merge/decay and key reservation independently deny terminal bodies.
Character receives the existing available-context interfaces; no candidate-specific prompt or
state is added. Verification and residual gates are recorded in [erasure knowledge](erasure-knowledge.md).


## Desktop application boundary — D1 / ADR-0079

Qt Widgets owns presentation, draft text, process supervision and UI preferences. An owned
QProcess transports bounded typed JSONL requests over stdin/stdout to `satori.desktop.worker`;
there is no HTTP listener or remote API. The serial worker calls existing composition/use cases,
providers, canonical history, memory owners and source-policy admission. Each process has its
own responsibility; the Core stays a modular monolith and has one canonical writer path per owner.

Paths are absolute and selected explicitly. Existing profiles are used in place, never copied,
automatically enrolled, repaired or replaced. Creating a new identity is a separate onboarding
action. Preferences store no credentials or domain state. D1 selects local Ollama only and does
not inherit `.env` or SATORI_* settings that could silently enable cloud disclosure. Existing CLI
provider configuration remains unchanged; cloud GUI needs later Keychain/disclosure work.

Replies appear only after existing Talk commit. Derived memory work and its errors are reported
separately and drained on graceful shutdown. Process failure yields unknown outcome, never a
claim of cancellation or automatic resend. Bounded crash restarts do not replay user input.
Window close keeps the tray application available; explicit Quit drains the owned process.
Opt-in login launch applies at the next macOS login and does not override Quit with KeepAlive.

The delivered local `.app` includes Python/Qt and allowlisted migration/seed resources, and
passes local ad-hoc signature verification. It excludes models, canonical state, credentials
and private configuration. Developer ID distribution/notarization remains a separate release
gate. The source-linked launcher remains a developer alternative. Tasks, notification dispatch, connectors, permission/grant owner,
cloud GUI, memory erasure GUI, Windows packaging and mobile are later roadmap slices.

## Local voice boundary — D2.1 / ADR-0080 and ADR-0081

The Qt desktop owns microphone permission, deliberate held capture, transient audio, draft
presentation and optional speech delivery. QtMultimedia capture feeds a separate owned speech
worker through inherited pipes. It does not enter the Core worker as audio, add an owner/schema,
alter a Character prompt or give a provider new access. Capture is off at startup; permission is
requested only on the first PTT gesture, and a successful grant requires another deliberate press.
Mono PCM16 input is bounded to 30 seconds /3 MB and has no recording-file or diagnostic-body path.

The quality correction selects Russian GigaAM v3 E2E RNNT int8 through ONNX-ASR CPU execution.
Pinned third-party public export weights occupy approximately 226 MB outside the signed app and canonical profile.
On startup an owned read-only `--check-only` worker can verify existing resources without download,
inference or directory creation. Successful SHA-256 validation enables capture; a cheap cached
file-metadata fingerprint detects later changes without hashing weights on the GUI polling loop.
The recognition worker rechecks integrity before use. Only an explicit preparation action downloads
the fixed revision/resources: verified HTTPS, bounded streaming, exact sizes/hashes, owned staging
and atomic no-replace publication. Existing incomplete/unrelated directories are preserved.

Russian transcription is an uncertain hypothesis for the visible editable draft. Confidence is
reported as unknown rather than a calibrated accuracy claim. Separate explicit Send uses the
unchanged text application boundary and canonical commit; raw audio or an unreviewed hypothesis
does not become fresh memory, self or relationship evidence. Generation/profile/session/editor
fences reject stale callbacks. Unrelated Core inspection may be busy without discarding a valid
same-scope draft. Editing, cancellation, focus loss/window hide, permission loss, scope changes
and runtime shutdown stop capture/recognition; retries never automatically send the draft.

The rejected system-voice default is replaced by an owned speech process. The local Qwen3-TTS
1.7B CustomVoice candidate was also rejected by human audition and remains explicitly experimental.
After the user's cloud authorization, preferred setup is ElevenLabs v4 with session-only
credentials, visible reply-text disclosure/retention/cost notice and automatic speech off.
The frozen candidate has an editable compiled Marina default; its timbre was not accepted by
the user. Nadia was accepted as a baseline in a public website demo, with speed/alternatives
still under review. The installed personal voice ID is Nadia and overrides that binary default;
automatic speech remains off. This demo does not establish actual application API access or quality.
Legacy system autoplay does not transfer to cloud. A fixed speaker preserves voice identity;
a frozen desktop projection reads the current committed reply's exact
existing delivery/affect manifest and derives bounded engine-only prosody instructions. There is
no new emotion estimate/owner, state write, appraisal call or Character prompt change. Unknown
metadata yields neutral delivery. A deterministic outgoing speech copy removes decorative emoji
and Markdown without modifying canonical text. Private bounded sequence-checked PCM records feed
Qt native playback, with stateful resampling and immediate reset on stop; neither replies nor
audio are written to diagnostics/files. Instruction routing does not prove acoustic quality.
Playback is opt-in and starts only for a new correlated successful committed reply. History,
startup, replay and errors cannot trigger speech. Playback stop changes delivery only; the
canonical reply remains committed. New PTT, scope changes and Quit stop owned playback.

The frozen engineering gate and local installation pass; the quality record
separates engine/fixture evidence from physical microphone/permission checks, speaker audibility
and natural voice review. Public binary-license notices, signing/notarization, clean-machine and
privacy release gates remain open. D2.1 does not complete full D2, historical Stage 20, hands-free
or realtime/barge-in; it opens no D3 tools, Stage 15 or Character improvement.

## Session calendar adapter — D3.1 / D4.1 / ADR-0082

The user's 2026-10-20 continuation opens a local selected-calendar display under
[ADR-0082](decisions/0082-session-calendar-read-and-assistant-foundation.md). A pure application
coordinator owns only transient principal/profile/session grants and attributed snapshots. It
checks exact account/calendar identity, expiry, request generation and bounded results before
and after native I/O. There is no new canonical state owner, schema, user-root admission,
provider context or TTS path. Calendar titles remain untrusted local display data.

The desktop owns a cancellable one-shot Swift/EventKit helper over bounded private pipes.
The original ADR-0082 operations are status, explicit permission request, catalog and
selected-event read. The application and helper declare modern Calendar access usage;
macOS grants full access while the application narrows reads to selected calendars.
App/tab startup does no calendar I/O. Disconnect/profile change/Quit or expiry clears the
transient view and fences late results. Restart restores neither grants nor event bodies.
Metadata-only audit cannot contain event titles, source names or native error bodies.

[ADR-0090](decisions/0090-reviewed-schedule-import-and-iphone-calendar-client.md) adds a
separate reviewed, create-only shift import. A local Vision result or portable JSON becomes
editable rows, never a Core message. A fresh exact-calendar preflight and one-use payload-bound
confirmation precede a journaled native commit. Unknown outcomes block replay and permit only
reconciliation; a fresh EventKit store verifies successful local creation. The iPhone client
uses the same portable row fingerprints with its own current device-local calendar identity.
One device creates the first batch and the other verifies delivery. Local save/readback does
not prove iCloud delivery, and the foreground opt-in refresh is not background synchronization.

This does not complete durable external ingestion, privacy/encryption, generic actions,
mail/messages/calls or conversational source grounding. Real account acceptance requires the
user's OS consent and a selected-calendar read/revoke scenario; compilation and fixture tests
cannot substitute for it. Status and evidence remain in `progress.md`.

## Coordinated assistant integration — ADR-0083 / ADR-0084 / ADR-0085

Status on 2026-10-20: the three bounded units below are implemented and frozen. The coordinated
fresh noneditable quality, exact Foundation gate (7625 passed/four opt-in Ollama skips), final
build/artifact/private native UI, app-only installation and scoped actual installed normal UI
pass. This bounded engineering delivery is complete; real account and acoustic acceptance
remain NOT_RUN. The
integration record binds 7629 unique cases,
788 frozen inputs, 346 wheel files and the final bundle. The previous D4/7206-pass gate remains
historical. Character is paused, Stage 15 stays closed, and ordinary typed/ASR drafts retain
their explicit canonical Send path.

**Calendar local-display card — [ADR-0083](decisions/0083-transient-calendar-dialogue-cards.md).** An explicit Calendar action
in the conversation view previews the exact query, selected sources, date/window and IANA zone.
It reuses the existing Calendar coordinator as the sole read/grant/snapshot owner. A separate
one-use local-display permit binds principal, chat/session, proposal, resources, grant generation
and request; a read grant alone does not authorize a conversation-view card. One fresh read is
admitted through one correlated result path. The dialogue session owns only its temporary
permit, receipt and plain-text answer, never a second calendar grant or read owner.

The card deterministically renders an agenda or computes the UTC interval union/complement
within one civil day and existing event/byte bounds. Derived gaps are labelled only as
intervals without events in the selected calendars; they do not assert personal availability.
Partial results cannot justify positive gaps. Denied, failed, stale, unavailable and cancelled
results remain distinct from a complete empty result. Source coverage, observation/expiry time,
window and timezone stay visible. Revoke, selection/profile/chat changes, expiry, cancellation
and newer work clear the card and fence late callbacks without overwriting an edited draft.
This local display does not invoke Send, LLM/provider or post-response work, persist a source
answer, create a USER/ASSISTANT interaction, enter memory or emit TTS.

**Cloud speech admission and public auditions — [ADR-0084](decisions/0084-cloud-speech-admission-and-public-auditions.md).** A deterministic
guard admits the outgoing rendered speech copy before any cloud connection. Residual square
brackets, including compatibility equivalents, literal decorative emoji and the enumerated
unsupported presentation residue produce a closed text-only outcome. Literal code, negations
and operators are not silently deleted to bypass admission. Only admitted content receives the
existing closed delivery tags from this exact committed reply. Canonical text and existing
Character/affect owners stay unchanged. This is a syntactic boundary, not proof of semantic
instruction immunity or acoustic quality; refusal triggers no automatic retry or fallback.

An immutable catalogue provides four authored public audition cases: neutral, curious, subtle
humor and reserved. The existing manual preview route and bounded owned speech worker make the
selected phrase/provider/quota disclosure explicit. This adds no free-text TTS instructions,
automatic paid generation, Voice Design operation, saved voice, model change or new accepted
timbre. Existing saved Voice IDs, autoplay choices, session-only credentials and cancellation
boundaries remain authoritative. Provider transport tests and public phrases remain separate
from authenticated same-speaker emotional and human voice acceptance.

**Offline selected-mail foundation — [ADR-0085](decisions/0085-offline-selected-mail-foundation.md).** The user selected Gmail.
The integrated scope is pure typed selection/read/freshness policy and bounded Gmail MIME/header
projection to untrusted plain text. It reuses the local principal type and has a separate
ephemeral mail owner: exact account/message IDs, selection revision, request/grant/generation,
expiry and single admission bind results. Thread IDs, names and headers do not widen scope.
Revoke/account/profile changes, rollback, stale or replayed results fail closed. Audit is closed
metadata without addresses, headers or bodies.

The parser retains source/span attribution, completeness and explicit unsupported outcomes;
repeatable headers differ from duplicate singleton headers. Sender/header claims remain claims.
It does not execute HTML, fetch remote images/URLs, parse attachments or embedded messages, or
silently turn truncation/unsupported content into complete evidence. No mailbox UI, OAuth,
credentials, network adapter, real mailbox read, summarizer, remote draft/send, persistence,
Core/provider projection, memory learning or TTS belongs to this unit. Future Gmail readonly
OAuth would be mailbox-wide; selected-message narrowing would remain application policy and
needs its own account-consent and credential implementation.

Telegram's private-adapter prototype and the call workstream's real WebRTC engine loopback with
synthetic media stay in their exclusive scratch paths. They are not installed connectors or
real account/counterparty tests. The coordination register
records ownership; `progress.md` records the current integration and gate status.

## Foreground Gmail connection — ADR-0086

Status on 2026-10-20: bundle 6 installed under
[ADR-0086](decisions/0086-foreground-gmail-connection-and-selected-reading.md), with its exact
7904/5 engineering gate preserved in the account record.
Calendar five-source read/refresh and Gmail Connect/list20 pass scoped actual checks; original
selected-mail previews reject. [ADR-0087](decisions/0087-bounded-mail-alternative-selection-and-diagnostics.md)
partially supersedes MIME-alternative decoding/projection version. Its header-value whitespace
and version are superseded in part by [ADR-0088](decisions/0088-mail-header-horizontal-whitespace.md).
Source v3 implementation/focused checks and scoped private account flow pass; final engineering
and installed acceptance remain pending. Source-panel results do not accept installed bundle 6.

The design adds a disconnected Mail surface with no startup OAuth, account discovery, listing
or source read. Explicit Connect admits a bounded Desktop installed-client JSON and uses the
system browser, PKCE S256, one-use random state and an owned ephemeral 127.0.0.1 callback.
Exact path/Host/state/attempt correlation and a 120-second bound fence cancellation/profile/
quit races. Requested scopes are only openid, email and Gmail readonly; fixed verified-TLS
Google endpoints provide token redemption, granted-scope checks and UserInfo subject identity.
Email, display names and message claims are not account identity. No redirect, ambient cookie/
auth/proxy credentials or automatic retry belongs to transport.

Only session process memory holds credentials. There is no Keychain, token file, refresh,
remember-login or restart restore. Local Disconnect clears local references/views immediately;
separate confirmed remote revoke reports its own outcome and is serialized against reconnect.
Owned workers keep network waits off the GUI thread and finish bounded cancellation/shutdown.

A separate explicit action lists one INBOX page of at most 20 IDs/allowlisted metadata headers,
without snippets, bodies, thread expansion, pagination or attachments. The user then selects
at most ten exact messages and explicitly reads them through the existing MailAccessCoordinator
and MIME projection. Connection generation/token expiry and exact request/resource bounds are
rechecked before and after I/O. Read grants remain at most one hour, pending requests 30 seconds
and snapshots five minutes; selection edits invalidate the old grant/view. The account scope
is Google mailbox-wide readonly with narrower selected-message application policy.

Plain-text local previews keep provenance, completeness and untrusted sender/header claims.
No mail content enters canonical Send/history, Core/model context, memory, self/relationship
learning or TTS. Inference, generated summaries, drafts, sending, marking read, label changes
and background sync remain outside this increment. Calendar's existing grant/read/local-card
owners and normal editable ASR/typing/explicit Send remain unchanged. Telegram/calls continue
in their separate chats. Character is PAUSED; Stage 15 remains closed. Registration details,
addresses, client IDs/secrets and account content are excluded from shared documentation.

## Bounded mail alternative selection and diagnostics — ADR-0087

[ADR-0087](decisions/0087-bounded-mail-alternative-selection-and-diagnostics.md) is accepted for
alternative selection and closed diagnostics. [ADR-0088](decisions/0088-mail-header-horizontal-whitespace.md)
now selects `gmail-local-text-v3`; final Foundation/build/full-gate/install remain pending. Validate the
bounded MIME structure once, then choose usable plain text before decoding unused HTML
alternatives. A malformed/oversized selected plain candidate rejects; empty/unsupported plain
may use the existing fallback, and every decoded candidate consumes the same budget. Limits
remain 64 parts/depth8, wire 512 KiB per message/1 MiB per batch, decoded 64/128 KiB and text
16000/24000 characters. ADR-0088 changes only literal header-value HTAB admission below; all
other header/plain-text control rules remain strict.

An undecoded alternative is source-attributed `alternative_not_decoded`, with unvalidated body
encoding/size/charset/text, and forces partial completeness. Whole-payload provenance remains
bound; versioned projection/span digests describe admitted text. Optional closed reasons pass
through existing parser/owner/controller lifecycle, clear on actions/success/cancel/disconnect/
profile changes and expose no source values, header names, MIME paths, actual sizes, IDs or
exception payloads. Scope, deadlines, atomic batches and source/Core/TTS isolation do not widen.

The earlier diagnostic `body_encoded_limit` proves a budget rejection, not its exact MIME
branch. Compatibility source v2 reads the newsletter criterion partial, while a separate
notification still rejects `invalid_header/header_controls`. Source v3 checks below do not
identify the exact earlier character or establish installed bundle-6 read success.

## Mail header horizontal whitespace — ADR-0088

[ADR-0088](decisions/0088-mail-header-horizontal-whitespace.md) permits only literal HTAB in raw/
decoded header values and their typed claim, retained losslessly under `gmail-local-text-v3`.
Existing bounded unfolding stays unchanged. Bare CR/LF, NUL, DEL, other controls, Unicode format/
surrogate characters, header names, identifiers and invalid metadata retain strict rejection;
counts/lengths, whole-tree/wire/decode/text bounds, scope and atomic batches do not widen.

Private source v3 actual OAuth/list20, two visible-criterion exact-one nonempty partial reads,
new-list clearing without auto-read, explicit fresh read and local Disconnect/owned-process
exit pass. Historical IDs were not retained; no cryptographic case-continuity claim follows.
Source-focused 575 cases and source Ruff/mypy pass. This is
**SOURCE_ACCOUNT_FLOW_PASS_SCOPED / FINAL_ENGINEERING_PENDING**: final master Foundation/build/
full-gate/install remain pending, installed bundle 6 unchanged. No Core/normal profile, model,
memory, TTS, persistence, remote write or Send participates in the private source panel.

## Discarded HTML format controls — ADR-0089

[ADR-0089](decisions/0089-discarded-html-format-controls.md) defines a source candidate,
`gmail-local-text-v4`; installed bundle 7 remains v3 with its scoped account flow and explicit
unsupported-HTML limitation. Master Foundation/build 8/artifact/install and an actual rejected-
message reread remain pending. Its codepoint, position and historical identity are unknown.

Only literal U+00AD, U+200B, U+200C, U+200D, U+2060 and U+FEFF may be omitted in data whose output
the existing deterministic local stack already discards. This is not a browser-invisibility,
CSS-correctness or harmlessness claim. Clean complete recognized named/numeric references with
an exact token and semicolon preserve v3 admission of all non-bidi Cf in that same context.
This literal/entity asymmetry is explicit; ordinary entity output/optional-semicolon behavior
is retained. Raw Cf in markup, attributes, comments, declarations, processing instructions,
malformed entity syntax or visible text rejects. The declared 18 directional deny set includes
deprecated U+206A–U+206F; it is a conservative policy, not a Unicode 12-property claim. Encoded
bidi and decoded attribute-control rejection intentionally tighten v3.

Raw positional/token fences prevent malformed syntax from obtaining dropped-data authority;
angle/reference-like rawtext can remain conservatively unsupported. Whole MIME/raw-body decode,
wire/text/stack bounds and selection behavior are unchanged, including every discarded byte.
Typed part-attributed omission `html_discarded_format_controls` forces partial completeness and
processing_eligible=False; no value/position is logged. Original raw digests remain bound and
v4 projection/span provenance cannot reuse an old DTO. No canonical rewrite, new source fetch,
permission, model/Core context, memory, persistence, TTS, draft or remote write is introduced.
See the synthetic ledger.


## Unified desktop source workspace (ADR-0092)

The ordinary desktop composes Calendar, session-only Gmail and a separately owned Telegram
worker in one window. A small content-free SourceStatus projection feeds the home UI; grants,
principal/generation identity, read admission and snapshots remain owned by the existing
source controllers. Navigation and status inspection do not connect, read or renew access.
Telegram runtime engineering qualification binds the fixed packaged worker/schema/SDK/policy
bytes and is separate from actual account acceptance. Protected input travels only over its
owned private process channel. The self-contained worker entrypoint executes a single fixed
verified source resource with the application's bundled interpreter. Source data never enters
canonical Send/history, memory, model or TTS. Shared profile/hide/quit transitions revoke views
and retain helpers until their actual completion. Context, location and maps have a separate
preparation workstream; no capture or general source-processing executor is enabled here.
