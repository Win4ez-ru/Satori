# Persistent state model and ownership

Это target ownership model с конкретизированными persistent families Stage 0–13 и принятым
Stage 14 mutation/checkpoint contract. Transient request projections currently reach context
schema v15; Stage 14 implementation advances it to v16. Сущности более поздних stages не обещают
соответствие «одна сущность — одна таблица».

## 1. Универсальные правила

Каждый aggregate имеет stable ID, schema version, timestamps и aggregate version. Любое derived claim имеет provenance/confidence. Изменяемые долгосрочные состояния имеют историю или могут быть восстановлены из append-only events. Только owner создаёт `accepted` decision; application unit of work физически коммитит decision, mutation и audit атомарно.

Mutation speed classes:

- **Fast:** секунды–часы; частые bounded updates и deterministic decay.
- **Medium:** дни–месяцы; meaningful-event evidence, cooldown/rate limit.
- **Slow:** месяцы–годы; repeated independent evidence, строгий drift budget и обязательный audit.
- **Append-only:** не «скорость», а неизменяемая запись наблюдения/решения; correction создаёт новую связанную запись.

## 2. Семейства и source of truth

| State family | Source of truth | Speed | History/audit |
|---|---|---|---|
| Identity/activation | Identity aggregate | Slow/immutable core | Every change; activation immutable |
| Personality traits | Personality aggregate + change events | Slow | Required, reversible by event replay/migration |
| Values | Value records + change events | Slowest | Required; stricter policy/ADR before mutation |
| Beliefs/opinions/hypotheses | Position records + evidence links | Medium | Required for confidence/content changes |
| Preferences/interests | Separate SatoriInclination aggregates + own evidence/revisions | Medium | Required for material change |
| Goals | Goal records | Medium | Status history required |
| Emotion | Current emotional vector + emotional events | Fast | Event trace; compactable under retention policy |
| Mood | Current mood projection inside affect aggregate | Fast/Medium (hours) | Formula/version recorded in transition |
| Emotional concepts | Concept + prototype/evidence | Slow | Required; creation threshold/history |
| Person/user model | Epistemically typed claims | Medium | Provenance/confidence and correction history |
| Relationship | Per-person aggregate + events | Medium | Every mutation audited |
| World model | Current situation claims | Fast/Medium | Provenance, validity interval, supersession |
| Interaction log | Session/message/interaction records | Append-only | Corrections append; retention policy later |
| Memories | Memory records, evidence and links | Append-only + lifecycle status | Creation/merge/supersession audited |
| Self model | Structured projection + source refs | Slow | Every material revision audited |
| Autobiographical narrative | Narrative events/links | Append-only/Slow | Required provenance |
| Unfinished threads | Thread aggregate | Medium | Open/update/resolve history |
| Reflection | Run + input set + proposals | Append-only | Always audited/idempotent |
| Audit | Audit events | Append-only | Tamper-evident strategy before production |

## 3. Data ownership matrix

| Entity/state | Owner | Readers | Allowed writer | Mutation policy | Audit |
|---|---|---|---|---|---|
| `Identity` | IdentityManager | All via read model | IdentityManager only | Explicit migration; core fields immutable after activation | Required |
| `PersonalityTrait` | PersonalityManager | Cognition, appraisal, reflection | PersonalityManager only | Slow, evidence threshold, max delta, cooldown, drift budget | Required |
| `Value` | ValueManager (personality boundary) | Cognition, reflection | ValueManager only | Slowest; no autonomous mutation until dedicated ADR/evals | Required |
| `Belief/Opinion/Hypothesis` | PositionManager | Cognition, self model | PositionManager only | Evidence/confidence/conflict policy; kind cannot silently change | Required |
| `SatoriInclination` (`preference`/`interest`) | PositionManager | Cognition, context, reflection target adapter | PositionManager only | Reflection V2 affect-backed diversity, deterministic delta/cooldown/budget/neutral decay | Required |
| `Goal` | GoalManager | Cognition, threads, self model | GoalManager only | Explicit source/status transitions | Required |
| `Person/UserClaim` | UserModelManager | Cognition, relationship, memory | UserModelManager only | Fact/inference/hypothesis preserved; correction supersedes | Required for semantic claim |
| `Relationship` | RelationshipManager | Conversation context/read CLI | RelationshipManager only | Canonical event, maturity/saturation/per-person bounds; no message counter shortcut | Required |
| `RelationshipEvent` | RelationshipManager | Read CLI, audit | RelationshipManager only | Append-only transition, canonical evidence-linked | Required |
| `EmotionalState` | EmotionManager | Cognition, expression, context | EmotionManager only | Fast bounded delta + deterministic decay | Trace required |
| `Mood` | EmotionManager | Cognition, context | EmotionManager only | Versioned deterministic/aggregated policy | Required for material update |
| `EmotionalConcept` | EmotionManager | Cognition, self model | EmotionManager only | Repeated cluster evidence; not one event | Required |
| `WorldClaim` | WorldModelManager | Cognition, threads | WorldModelManager only | Provenance + valid/superseded lifecycle | Required for durable claim |
| `Session/Message/Interaction` | InteractionLog | Memory, cognition, audit | InteractionLog repository via use case | Append-only; idempotent request ID | Trace/audit metadata |
| `Memory` | MemoryManager | Context, relationship, self model | MemoryManager only | Source required; lifecycle state; no unsupported content | Required |
| `MemoryEvidence/Link` | MemoryManager | All state owners validating evidence | MemoryManager only | Append-only/superseding, referential integrity | Required |
| `EpisodicMemoryEmbedding` | Retrieval index use case | Episodic retrieval only | Retrieval index repository only | Derived; exact space; idempotent upsert/rebuild; never canonical evidence | Metadata trace, no domain audit |
| `SemanticClaim/Evidence/Revision` | SemanticMemoryManager | Context and explicit semantic reads | SemanticMemoryManager only | Closed predicates; root-user evidence; deterministic confidence; merge/supersede/dispute | Required |
| `SelfModel` | SelfModelManager | Cognition, context | SelfModelManager only | Slow evidence-backed projection revision | Required |
| `SelfNarrativeEvent` | SelfModelManager | Cognition, memory | SelfModelManager only | Significant event + source refs | Required |
| `UnfinishedThread` | ThreadManager | Cognition, observer later | ThreadManager only | Explicit open/update/resolve; aging deterministic | Required |
| `ReflectionRun/Source/Attempt/Proposal/Outcome` | ReflectionCoordinator (application lifecycle) | Target adapters, audit | Coordinator records reflection lifecycle only; target owners decide domain mutations | Versioned fixed source set/hash; V2 optional immutable affect attachment; deterministic trigger/cost and terminal outcomes; no direct domain write | Always |
| `AuditEvent` | AuditRecorder | Diagnostics/export | Unit of work only from owner decision | Append-only in same transaction as change | Always |

Manager names denote domain owners, not necessarily runtime classes. Related owners may live in one module, but their write contracts remain distinct. Entries in the Readers column mean immutable snapshots supplied by application orchestration; they do not permit direct repository access or reverse module imports.

### Stage 2 concrete ownership

До появления evolution managers единственная разрешённая запись identity/personality/values — internal construction внутри explicit `ActivateSatori`. Она использует один `InitialSelfRepository.add` в общей Unit of Work и не является generic mutation API. После activation эти families read-only; повторный вызов отвергается до изменения state. Future `PersonalityManager`/`ValueManager` не может переиспользовать seed как reset и потребует своих proposal/policy/audit contracts.

Authoritative Stage 2 representation:

```text
Identity
  identity_id, name, activation_time, identity_version
  seed_id, seed_schema_version, seed_content_hash

Personality
  schema_version, aggregate_version
  traits[key] { current value, activation baseline }

Values
  schema_version, aggregate_version
  items[key] { strength, description, origin=initial_seed }

InitialSelfSnapshot v1 = Identity + Personality + Values
```

Keys — validated lower snake_case records bounded by the versioned seed schema, not arbitrary JSON fields and not DB columns per trait. Canonical v1 validates the exact constitutional key set; relational representation remains extensible for a future explicit schema version. Seed is provenance/input only. Missing required child state for an existing identity is corruption, never a signal to silently reseed.

Physical ownership is enforced by normalized FK-bound tables, a unique checked primary installation slot and a minimal activation audit in the same transaction. Snapshot dataclasses are frozen, contain no ORM objects and expose only keyed reads. There is no setter/change/update method for traits or values.

### Stage 5 derived ownership

`EpisodicMemoryEmbedding` is not a new memory truth or mutable domain aggregate. It is owned by a
dedicated index repository and can read immutable active episodes but cannot change them. Its only
write path is post-commit indexing/backfill/rebuild keyed by exact
provider/model/dimensions/input-schema. Deleting or replacing the entire index loses no canonical
episode, evidence, formation decision or audit. Retrieval is read-only over canonical state and
returns an immutable scored context artifact; it never commits similarity as fact.

### Stage 3 state boundary

Basic Conversation Core не добавляет persistent state family и не пишет существующие. `TalkToSatori` только читает `InitialSelfSnapshot`; provider получает отдельные immutable core request values и не имеет repository/UoW reference. Golden provider-swap сравнивает snapshot до/после и единственный Stage 2 activation audit остаётся единственной записью.

На границе Stage 3 `Session/Message/Interaction` оставались target family, а не таблицами. Stage 3 выбрал stateless single-turn scope, потому что raw interaction retention/redaction gate был назначен Stage 4. Ни input, ни reply, ни provider thread тогда не становились memory или autobiographical evidence.

### Stage 4 concrete ownership

ADR-0012 закрывает Stage 3 gate. `InteractionLog` является единственным writer-owner для:

```text
ConversationSession v1
  session_id, identity_id, kind=implicit|explicit, status=open|closed
  started_at, ended_at

ConversationInteraction v1
  interaction_id, session_id, client_request_id, trace_id
  status=pending|failed|completed, started_at, completed_at
  minimal provider/model/usage/context metadata, failure_kind

HistoricalMessage v1
  message_id, session_id, interaction_id, role=user|assistant
  exact content, created_at, sequence=1|2
```

`client_request_id` unique. Begin transaction creates pending interaction + user message; completed interaction requires exactly one user and one assistant message. Message rows have no update/delete API. Failed intake may have only user message and is retryable. Hidden policy/character/provider prompts are not historical messages.

`MemoryManager` independently owns:

```text
EpisodicMemory v1
  memory_id, source_interaction_id, occurred_at, summary
  importance, confidence, created_at, lifecycle_status=active
  formation_method, formation_version

MemoryEvidence
  evidence_id, memory_id, source_message_id
  provenance_kind=explicit_user_statement, exact quote, observed_at

EpisodeFormationDecision
  decision/idempotency/source IDs, formation/policy versions
  kind=created|skipped|rejected, reason, provider/model/method, trace/time
```

Only completed interactions are eligible. Formation v1 accepts exact spans from user messages; assistant output cannot be event evidence. `source interaction + formation version` is unique for both memory and terminal decision. Create commits episode/evidence/decision/audit atomically; skip/reject commits decision/audit with no memory. Extraction/commit failure has no terminal decision, so replay may retry. Episode formation itself creates no semantic claim, user model, relationship, emotion or personality state.

### Stage 6 concrete semantic ownership

`SemanticMemoryManager` is separate from both `MemoryManager` and the Stage 9
`UserModelManager`:

```text
SemanticClaim v1
  claim_id, structured claim_key, schema/aggregate/normalization versions
  subject=user, registered predicate, typed value, normalized_value, polarity
  kind=explicit_fact|inferred_fact|hypothesis|attributed_statement
  confidence, status=active|superseded|disputed|retracted
  valid_from/valid_until, superseded_by_claim_id
  formation method/version, created_at/updated_at

SemanticClaimEvidence
  semantic_evidence_id, claim_id
  memory_id, memory_evidence_id, root_message_id, root_interaction_id
  source_kind=explicit_user_statement|episode_inference, observed_at

SemanticFormationDecision
  decision/idempotency/source-memory IDs, formation/policy versions
  kind=applied|skipped|rejected, reason and operation counts
  claim IDs, provider/model/method, trace/time

SemanticClaimRevision
  claim/version/decision IDs, created|strengthened|superseded|disputed|retracted
  before/after status/confidence, reason, occurred_at
```

Only this owner may create/merge/supersede/dispute semantic claims. Source memory + formation
version is terminal/idempotent. Root user message is the evidence independence unit; one retry or
one retrieved repetition cannot count again. `Person/UserClaim` remains a future current user
model with expiry/situation semantics and is not implemented by these memory aggregates.

### Stage 7 concrete affect ownership

`EmotionManager` independently owns:

```text
AffectiveStateSnapshot v1
  identity_id, state_version, mood_version, as_of
  emotion_policy_version, appraisal_schema_version, mood_policy_version
  fast vector, mood vector

AffectiveTransition v1
  transition/identity/interaction/source-message/trace IDs
  structured appraisal + source refs + reason codes
  before/after snapshots, applied fast delta, mood delta
  provider/model/method, committed_at
```

One projection row exists per identity. Only an accepted non-zero owner decision increments both
versions and creates one transition/audit. State initialization is deterministic at policy
baselines and has no provider call. Appraisal sees immutable personality and values but cannot
write them. The interaction/affect combined Unit of Work exists only to preserve canonical
reply/state atomicity; physical co-transaction does not merge ownership.

### Stage 7.5 runtime projections are not persistent state

`RecentConversationContext` is an application read projection over canonical completed history,
not a new aggregate. It has no writer-owner, table or migration: `InteractionLog` remains the sole
conversation write path. `TurnPhaseTimings`, provider execution metrics, progress state and the
in-process post-response queue are ephemeral observability/runtime values. They cannot mutate
identity, personality, affect, memory or semantic claims.

Post-response processing invokes the existing `MemoryManager`, embedding-index and
`SemanticMemoryManager` owners through their existing Unit of Work boundaries. Queue membership is
not durable state and is never presented as a completed memory; retryability comes from missing or
terminal source/version decisions in canonical storage.

Stage 7.7 adds only an ephemeral provider reservation queue, benchmark samples and adapter timing
metadata. Inference priority, background grace/aging and categorical appraisal transport have no
table, repository, aggregate version or audit mutation. The continuous proposal that crosses into
application and every persisted affect transition retain the Stage 7 schema and single-writer
owner.

## 4. Conceptual entities

Minimum fields are illustrative and refined before their implementation Stage.

```text
Identity: id, name, activation_at, continuity_version, created_at
PersonalityTrait: key, value, confidence, baseline, aggregate_version
Value: key, strength/priority representation, confidence, evidence_ids
Position: id, statement, kind, confidence, status, evidence_ids, origin
Person: id, aliases, epistemic claims
Relationship: person_id, dimensions, summary, version
Session: id, started_at, ended_at
Message: id, session_id, role, content_ref, timestamp
Interaction: id, client_request_id, trace_id, status, message_ids
Memory: id, type, summary/content, importance, timestamps, lifecycle_status
MemoryEvidence: id, provenance_kind, source_refs, confidence
MemoryLink: from_id, to_id, relation, confidence
EmotionalState: vector, observed_at, formula/policy_version
EmotionalEvent: trigger_refs, prior, proposed_delta, accepted_delta
SelfNarrativeEvent: type, summary, significance, evidence_ids
UnfinishedThread: kind, status, subject, source_refs, due/expected_at
ReflectionRun: idempotency_key, input_refs, model/template versions, status
ReflectionProposal: target_owner, kind, payload, confidence, evidence_ids
AuditEvent: aggregate, before_version, after_version, decision, reason_codes
```

## 5. Typed mutation envelope

Каждое proposal содержит:

```json
{
  "proposal_id": "stable-id",
  "proposal_schema_version": 1,
  "idempotency_key": "operation-and-source-derived-key",
  "target_owner": "PersonalityManager",
  "target_id": "trait:curiosity",
  "kind": "personality_delta",
  "payload": {"delta": 0.01},
  "confidence": 0.84,
  "evidence_ids": ["evidence-id"],
  "expected_aggregate_version": 7,
  "origin": "reflection",
  "created_at": "timestamp"
}
```

Owner возвращает typed decision: accepted/rejected, normalized applied payload (может быть clamped), reason codes, policy version, before/after version. Missing evidence, stale version, duplicate idempotency key, invalid bounds или cooldown дают explicit reject/conflict, а не best-effort write.

## 6. Epistemic model

Обязательные различия:

| Kind | Meaning | Может стать fact автоматически? |
|---|---|---:|
| Observation | Зафиксированный вход/событие | Нет, остаётся source evidence |
| Fact | Утверждение с достаточным trusted/explicit source | Только по policy |
| Belief | Позиция Сатори о мире | Не применимо |
| Opinion | Оценочная позиция Сатори | Не применимо |
| Hypothesis | Проверяемое предположение | Нет |
| Preference | Сравнительная склонность | Нет |
| Inference about user | Вывод из поведения | Нет |

Противоречие не перезаписывает старую запись: создаётся competing/superseding claim с evidence и resolution status. Confidence не заменяет provenance.

## 7. Stage 7 concrete affective state

`EmotionManager` является единым writer-owner для двух раздельных continuous spaces:

```text
FastAffectiveState v1
  valence [-1, 1]
  arousal, tension, curiosity, interest, amusement,
  concern, frustration, situational_confidence [0, 1]

MoodState v1
  valence [-1, 1]
  energy, tension [0, 1]
```

In Stage 7 these dimensions are absent from affect. Stage 8 adds them only to the separate
counterparty relationship aggregate described below; they remain absent from emotion/mood.
Personality seed не создаёт emotion seed: neutral baselines определены affect policy v1,
а personality модулирует только reactivity. Mood получает малый one-way impulse от
accepted fast delta и затухает медленнее; mood→emotion feedback на Stage 7 нет.

```text
current event + immutable self + selected memory + current materialized state
→ provider-neutral semantic appraisal proposal
→ EmotionManager provenance/confidence/modulation/caps/bounds
→ tentative transition + expression snapshot
→ atomic canonical reply + current state + transition + audit
```

Current projection stores schema/state/mood versions, `as_of` and emotion/appraisal/mood policy
versions. Transition stores source interaction/message/trace IDs, structured appraisal, applied
fast/mood deltas, before/after snapshots and provider/method metadata without raw text. Lazy decay
is pure `baseline + (x0 - baseline) × 2^(-elapsed/half_life)`; reads neither write nor increment
versions. Exact v1 parameters and transaction semantics are fixed in ADR-0015.

Expression receives a separate immutable snapshot, cannot write it and should express it subtly
rather than enumerate values or imply physical/relationship state.

## 8. Stage 7.6 transient runtime self-model

`RuntimeSelfModel v1` is an immutable application projection containing name, digital-person
identity kind, female/feminine expression, continuity, available bounded memory and affect
capabilities, embodiment/relationship status, configured provider/model role and explicit current
limits. It is reconstructed from authoritative persistent self plus live composition settings for
each runtime/request.

It is deliberately absent from the persistence diagram: there is no self-model table, event,
manager, UoW writer or migration. DB identity/personality/values remain authoritative; memory and
affect owners remain unchanged; provider output cannot write the projection back. Relationship and
user-model fields report `not_implemented`, so Stage 7.6 does not create Stage 8 state.

Stage 7.6.1 does not change this object or add stored state. Context schema v8 derives a smaller
mode-relevant conversational projection from the same complete object. Disclosure mode,
qualitative affect hint and per-mode generation settings are transient request data: none has a
table, event, repository, owner mutation or replay effect. Missing relationship state remains
`not_implemented`; natural wording about that absence is not a relationship projection.

Stage 7.7 also adds no state family. Scheduler queue entries and categorical provider objects are
process-local infrastructure values; they are never part of identity, affect, relationship,
memory, semantic claims or the runtime self-model.

Context schema v9 is likewise a transient request projection. Its relationship wording describes
the pre-Stage-8 absence boundary. Context schema v10 supersedes only that relationship projection:
it reads qualitative committed state without acquiring a mutation path. Its technical wording
still reads current affect capability without acquiring a mutation path.

Context schema v11 and behavior policy v9 add two more transient values:

```text
DialogueCoherenceContext
  bounded repetition/similarity/closing signals
  current correction/frustration and session-local style signals
  current activity/topic signal

ConversationalDisclosurePlan
  one primary mode
  zero or more required authoritative facets
```

Both are pure read projections over current input, canonical bounded recent pairs and existing
authoritative self/relationship snapshots. They have no stable ID, table, repository, writer,
event, aggregate version or cross-session persistence. A style correction is not a durable user
preference. An earlier assistant self-claim is not self evidence. Duplicate-generation
similarity and the broader ten-value response-regeneration reason/attempt/outcome are interaction
trace metadata, not a new state family. The narrow validator never writes state, and at most one
additional provider draft may be requested before the existing canonical finalize; a normal turn
still uses one provider call. Non-duplicate reasons emit metadata-only
`self_consistency_violation_detected` without prompt, candidate or user text. The generic retry
timing is `response_regeneration_ms`; `duplicate_response_detected` remains a duplicate-only flag.

There is no persistent `creator`, `created_by` or creator-relationship field in Stages 0–8.1. A
claim made by the current user may remain explicitly attributed to that current input, but it
cannot be committed as self history or user fact through these transient projections. A future
schema requires its own provenance, correction and privacy decision; Stage 8.1 does not infer it.

## 9. Stage 8 relationship state

`RelationshipState v1` is an authoritative versioned projection keyed by Satori identity and an
opaque counterparty ID. Its six `[0,1]` axes are familiarity, trust, comfort, closeness,
intellectual respect and non-romantic affection. Initial `(0,.5,.5,0,.5,0)` plus maturity zero
means little evidence, not distrust/discomfort/disrespect. Love, romance, attachment, dependency,
jealousy and exclusivity are not state fields.

`RelationshipManager` is the sole writer. A provider may propose only categorical events and
canonical source handles. The owner validates confidence/taxonomy, computes evidence maturity,
applies saturating formulas plus event/session caps, clamps bounds and decides apply/skip/reject.
Zero-effect decisions update processing/evidence counters but do not increment `state_version` or
create a transition. Meaningful updates increment exactly once and append a before/delta/after
transition plus audit record in the same UoW transaction.

One decision and at most one transition are unique per interaction. Canonical source order,
optimistic `(state_version, processed_count)` comparison and direct message/interaction foreign
keys protect replay, restart and concurrent processing. Relationship writes are post-response;
conversation reads an immutable qualitative future-turn projection. Memory, personality, affect,
semantic claims and runtime self keep their existing owners.

The Stage 8.1 expression projection does not reinterpret low evidence as a negative relationship.
Maturity zero and uncertain midpoints remain unknown; friendly openness and curiosity come from
personality. Established positive state may add ease, while damaged trust/comfort may add bounded
contextual guardedness. None of these rendering choices changes an axis, counter, transition,
policy cap or ownership rule.

## 10. Stage 9 user and world model state

`UserModelManager` and `WorldModelManager` own distinct counterparty-scoped claim families. Both
use canonical completed user messages as their only evidence roots, but neither reads or writes the
other owner's repository through domain code. A shared application coordinator can commit their
typed plans atomically without becoming a third owner.

```text
UserModelClaim v1
  identity_id, counterparty_id, claim/aggregate IDs and versions
  subject=counterparty, closed predicate, typed value and normalized identity
  epistemic kind, confidence, lifecycle, validity/freshness and supersession

WorldModelClaim v1
  identity_id, counterparty_id, claim/aggregate IDs and versions
  subject_kind=project|situation|commitment|outcome, bounded label
  predicate=status, closed status value
  epistemic kind, confidence, lifecycle, validity/freshness and supersession

ModelClaimEvidence / ModelClaimRevision / ModelFormationDecision
  direct canonical message/interaction roots
  append-only owner history and terminal source/version processing
```

Only current, non-expired same-counterparty claims can enter conversation. Read-time freshness is
pure and excludes a due claim even before deterministic expiry maintenance persists an `expired`
revision/audit. Correction and later state changes supersede rather than erase. Semantic memory
continues to own durable historical knowledge; relationship continues to own Satori's stance;
neither can become fresh model evidence.

## 11. Stage 11 Satori positions

`PositionManager` — единственный writer-owner identity-global позиций Сатори. Evidence
сохраняет canonical interaction/message/counterparty root, но counterparty не делит
мировоззрение Сатори на несовместимые персональные копии.

```text
SatoriPosition v1
  identity_id, position_id/key, schema/aggregate/policy/formation versions
  proposition + normalized proposition, immutable kind/stance
  deterministic confidence, active|competing|superseded|retracted
  optional value, competition and supersession links, timestamps

PositionEvidence / PositionRevision / PositionFormationDecision
  exact canonical roots and normalized signatures
  append-only before/after lifecycle and terminal source/version processing
```

Provider может только предложить typed belief/opinion/hypothesis. Owner проверяет
точные quotes, current-source participation, materiality, independent interactions/signatures,
immutable value link, confidence cap и explicit target version. Facts остаются пустым
typed boundary до появления independently verified ingestion. Exact merge, challenge,
supersession и competing hypotheses не переписывают history; decision, mutation,
revision и audit коммитятся атомарно.

## 12. Stage 13 Satori inclinations

`PositionManager` — единственный writer-owner identity-global `SatoriInclination`. Inclinations
являются sibling aggregates epistemic positions: они используют ту же owner boundary, но отдельные
records, evidence, revisions и mutation policy. `ReflectionCoordinator` может маршрутизировать
strict candidate, однако не рассчитывает score и не имеет inclination repository.

```text
SatoriInclination v1
  identity/inclination IDs, kind=interest|preference
  normalized topic OR canonical unordered option pair
  score anchor, confidence, stability, state_as_of
  schema/policy/aggregate versions and lifecycle timestamps

InclinationEvidence / InclinationRevision
  canonical reflection/source/root/interaction/transition IDs and hashes
  deduplicated normalized quote signature
  append-only accepted evidence and before/after trajectory

ReflectionSource V2 affect attachment
  affective_transition_id, resulting affective_state_version,
  affective_signal_hash
```

Interest score находится в `[0, 1]`; comparative preference хранится одной signed величиной в
`[-1, 1]` для canonical option pair. Confidence, stability и score независимы. Owner формирует
record только после Stage 13 diversity gate, сам выводит experience/utility из owner-approved
appraisal attachment и применяет versioned event caps, cooldown, rolling budget и materiality.
Provider передаёт только strict candidate, fixed source IDs, confidence и optional exact target
version; score, delta, decay, stability и generic patch запрещены.

Reflection V2 сохраняет all-or-none affect attachment вместе с fixed source до inference и
включает его в V2 source-set hash. При load/routing проверяются identity, interaction, source
message, transition version и signal hash. Missing/invalid attachment не портит Stage 12 source,
но делает его неeligible для inclination evidence. V1 runs/sources остаются readable и resumable
по исходным schema/hash rules; attachment не превращает affect или reflection artifact в generic
evidence.

Migration `0011_satori_inclinations` создаёт отдельные aggregate/evidence/revision tables, nullable
attachment columns у reflection source, новый proposal target `satori_inclinations` и nullable
conversation-manifest fields для context v15. Existing rows получают explicit `not_requested`
compatibility semantics там, где это необходимо. Migration не вызывает provider и не создаёт
historical inclination backfill.

Один accepted proposal коммитит inclination create/update, deduplicated evidence, before/after
revision, terminal reflection outcome и metadata/provenance audit в одной target-specific positions
Unit of Work. Rejection коммитит только outcome и audit. Idempotent proposal/outcome identity плюс
exact expected aggregate version исключают double application после crash/restart. Inclination
evidence никогда не выдаётся Stage 12 reflection-source query.

Stored score — anchor at `state_as_of`; effective score является pure neutral-centred exponential
projection. Read не пишет и не увеличивает aggregate version, а следующий owner mutation сначала
materializes decay на explicit UTC instant. Context v15 получает только immutable eligible
projection. Inclinations не читаются affect appraisal, retrieval, relationship/user/world
formation или future reflection evidence; generated response также не evidence. Поэтому
current-turn relevance не может самоподтвердиться через affect feedback.

## 13. Stage 14 personality evolution

`PersonalityManager` — единственный post-activation writer существующего identity-global
`Personality` aggregate. Activation baseline каждого trait и Core Values остаются immutable.
ReflectionCoordinator владеет только V3 run lifecycle и не получает personality repository.

```text
PersonalityChangeProposal V1
  exact trait key, increase|decrease, provider confidence
  fixed personality-purpose citations with support/counterevidence roles
  expected personality aggregate version

PersonalityEvidence / PersonalityRevision
  canonical root/session/lineage/hash provenance
  before/delta/after versions, drift/path metrics and owner reason

PersonalityCheckpoint / CheckpointApproval / RestoreEvent
  complete canonical trait vector + hash
  explicit budget-origin approval
  append-only version-increasing restore lineage
```

Reflection V3 purpose `personality_evolution` has a separate root-consumption namespace and uses
the Stage 12 canonical position/important-episode allowlist without affect attachments. It rejects
direct trait assignment, user mirroring, explicit relationship material, Stage 13 inclination
evidence and every generated/derived feedback source. V1/V2 general runs retain their original
hash, source and resume semantics.

One accepted proposal changes one trait by exact owner-derived `±0.005`. Owner policy independently
checks ninety-day longitudinal diversity, per-trait/global cooldown, rolling/lifetime path,
activation `D∞`/`D1` and last-approved-checkpoint `D∞`/`D1`. Reversal never refunds path budget.
Mutation, evidence, revision, resulting checkpoint, reflection outcome and audit are atomic;
rejection stores no personality evidence or state.

Restore requires an explicit local checkpoint ID/hash and expected current version. It appends a
new revision/event/checkpoint and aggregate version instead of overwriting history. Expression
Projection V2 is a pure read model over current versus baseline traits; context v16 exposes at
most two qualitative cues and the exact personality aggregate version, never checkpoint/evidence
history or numeric drift.

## 14. Recovery and portability

Canonical records + append-only events + versions позволяют:

- rebuild projections and derived indexes;
- detect partial/corrupt import before activation;
- explain a mutation through evidence and audit;
- preserve identity across provider replacement and restart;
- migrate schema without silently re-seeding personality.

Отсутствие audit/provenance для core mutation считается corruption и блокирует автоматическое принятие состояния до repair/migration policy.
