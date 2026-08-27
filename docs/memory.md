# Memory architecture

## 1. Memory is not chat history

Raw interaction log сохраняет то, что произошло в разговоре. Long-term memory — отобранное, типизированное и evidence-linked представление, предназначенное для будущего retrieval. Message не становится memory автоматически.

## 2. Типы памяти

| Type | Смысл | Пример | Source requirement |
|---|---|---|---|
| Raw interaction | Immutable message/session record | Точная реплика и timestamp | Client/provider event |
| Episodic | Конкретное значимое событие | Обсуждение запуска проекта | Source interaction IDs |
| Semantic | Обобщённое знание | User works on project X | One or more evidence IDs + epistemic kind |
| Relationship | Событие/паттерн конкретных отношений | Значимое disagreement/reconciliation | Person + event evidence |
| Self memory | Опыт, относящийся к self model | Сатори пересмотрела позицию | State/audit/evidence refs |
| Autobiographical | Событие, формирующее narrative | Activation, turning point | High significance + evidence |
| Position evidence | Основание epistemic позиции | Material argument/observation | Canonical user-message refs, never position itself |
| Inclination evidence | Owner-accepted longitudinal Satori experience | Repeated verified engagement with a topic/options | Reflection V2 fixed source + immutable affect attachment; never user taste or inclination itself |

Память может иметь несколько tags/links, но один primary type и явный owner. Position and
inclination evidence are provenance artifacts of their domain owners, not additional primary
memory types. Autobiographical meaning не отменяет underlying episode.

## 3. Provenance contract

Каждое durable claim хранит:

- provenance kind: `explicit_user_statement`, `observation`, `inference`, `external_source`, `self_reflection`;
- source interaction/message/memory/audit IDs;
- confidence отдельно от importance;
- created/observed/valid timestamps;
- extractor/model/template/schema versions, если использовалась модель;
- person/topics/session and optional emotional metadata;
- lifecycle: active, superseded, merged, decayed/archived, disputed.

`self_reflection` не является независимым evidence: она обязана ссылаться на исходный набор. Generated response Сатори не доказывает, что событие произошло.

## 4. Formation

```text
completed interaction
→ importance/novelty/persistence appraisal
→ zero or more typed memory proposals
→ source validation and dedup candidates
→ MemoryManager accept/reject
→ record + evidence + links + audit in finalize transaction
```

Постоянная память не создаётся, если summary добавляет unsupported detail, sources missing, information too transient, duplicate already represents it, or policy excludes sensitive/unnecessary data. Не каждое «важное для ответа» важно долгосрочно.

### Stage 4 implemented formation v1

Canonical raw history и episodic projection имеют разные owners/UoW. Completed interaction сначала существует независимо; затем `StructuredGenerationPort` получает ровно её user/assistant messages как untrusted data и предлагает один create/skip. Ollama adapter использует JSON Schema, но schema-valid output всё равно не является memory decision.

`MemoryManager` детерминированно требует:

- completed source interaction и proposal schema v1;
- create summary `1..500` chars;
- finite importance/confidence в `[0,1]` и importance не ниже versioned `0.5`;
- минимум одну evidence quote длиной до 500 chars;
- каждый source message принадлежит именно interaction, имеет `user` role, а quote буквально присутствует в его exact content.

Так source reachability проверяется без доверия provider. Semantic entailment summary из quotes пока не доказуема полностью: hostile/missing/assistant quote отклоняется, а sampled false-summary eval остаётся обязательным. V1 intentionally не использует assistant reply как доказательство внешнего события, не создаёт user semantic fact и не добавляет emotional/relationship metadata.

Create коммитит `EpisodicMemory + MemoryEvidence + EpisodeFormationDecision + audit`; skip/reject коммитит terminal decision + audit без memory. Extraction/persistence failure не меняет canonical interaction и не оставляет terminal decision, поэтому replay может повторить projection. Source interaction + formation version является idempotency/dedup key. Equivalent events из разных source interactions не merge: semantic/cross-source dedup относится к future consolidation.

Explicit debug read допускает lookup/list durable episodes, но не semantic search и не injection в conversation. Stage 4 generation manifest содержит zero prior evidence IDs даже если episodes уже лежат в storage.

## 5. Retrieval

```text
typed query → deterministic eligibility/security filters
→ candidate search → feature computation → deterministic ranking
→ diversity/dedup → context budget selection → manifest
```

Ranking features:

- semantic relevance;
- importance;
- recency/temporal fit;
- emotional relevance;
- relationship/person relevance;
- self relevance;
- unfinished-thread relevance;
- confidence/provenance quality;
- contradiction/dispute penalty;
- repetition/diversity penalty.

Weights and normalization are versioned configuration calibrated by evals. A vector score alone never establishes truth. Retrieval returns IDs, scores/features, confidence and source refs so Context Composer can preserve uncertainty.

### Stage 5 implemented policy

ADR-0013 implements the first episodic-only read path. `EmbeddingSpace` is the exact tuple
provider/model/dimensions/input-schema; only equal spaces are comparable. Vectors are derived JSON
rows in SQLite and can be backfilled or rebuilt without changing `EpisodicMemory`/evidence.

Eligibility is active episode + compatible space + `occurred_at <= cutoff` + source interaction
different from the current pending interaction. The retrieval query is current user text only.
Exact cosine scan first applies threshold `0.55`, then bounds the semantic pool to 32 and ranks:

```text
recency = 0.5 ** (age_days / 30)
score = 0.80 * cosine + 0.10 * importance + 0.10 * recency
```

Stable tie order is cosine, importance, recency, memory ID. Selection is top 4 under a
2400-character canonical JSON memory payload budget; exact normalized-summary duplicates are
removed. Confidence/evidence remain context metadata but are not a rank boost. Empty threshold or
budget selection is explicit `no_relevant_memory`; adapter/index error is `unavailable`. Neither
permits past-claim grounding IDs.

Checkpoint 14.2 candidate v16 keeps those typed statuses but renders them in Satori's own voice.
`no_relevant_memory` permits only a fallible current recollection such as `не вспомнила`/`не помню`,
not a claim that the event never happened or the user never spoke. A casual low-stakes `кажется,
ты мне об этом не рассказывал` remains explicitly correctable rather than proof of absence.
`unavailable` is not forgetting and is expressed only as inability to answer confidently from
memory now, without narrating an internal outage/search. Grounded recall uses `помню`/`вспомнила`;
an analogous past exchange `был`, not `есть/нашла в контексте`. These wording rules add no memory
status, confidence threshold or write path.

Selected summaries enter a separate envelope labeled untrusted evidence data. Instruction-like
text stays content and never becomes policy. A declared shared-past claim must cite a supplied
memory ID. Indexing runs only after episode commit and is retryable independently.

### Stage 6 implemented semantic policy

ADR-0014 adds a separate canonical semantic layer; it does not mutate or replace episodes. V1
subject is only `user`. The closed predicate registry declares single/multi cardinality and
allowed scalar type. A claim stores structured identity, typed value, separate polarity,
`explicit_fact | inferred_fact | hypothesis | attributed_statement`, confidence, validity interval and
`active | superseded | disputed | retracted` lifecycle.

Every evidence edge is source-complete:

```text
SemanticClaim → SemanticClaimEvidence → EpisodicMemory
→ MemoryEvidence → exact user Message → Interaction
```

Provider output is only a bounded typed proposal. Each claim must cite the newly processed source
episode, use a registered predicate, and have its normalized value present in cited root user
evidence. Inference additionally requires at least two unique root messages from two interactions.
Assistant output, semantic claims and retrieved repetition never become evidence. Roots are
deduplicated by user message, so replay and repeated retrieval cannot raise confidence.

Confidence policy v1 treats provider confidence as an upper input and applies deterministic caps:

```text
explicit_fact       min(proposal, min(0.90 + 0.02 × (roots − 1), 0.96))
attributed_statement min(proposal, min(0.85 + 0.02 × (roots − 1), 0.91))
inferred_fact       min(proposal, min(0.65 + 0.07 × (roots − 2), 0.79))
hypothesis          min(proposal, min(0.50 + 0.05 × (roots − 2), 0.65))
```

Exact structured identity merges only new roots. Different values coexist for multi-valued
predicates. A newer explicit/attributed value supersedes incompatible single-valued active claims
and closes their `valid_until`; explicit support also supersedes rather than relabels a compatible
inference. An inference cannot override explicit evidence. Competing single-valued inferences are
both retained as disputed and excluded from active recall. Direct correction is explicit-only and
must reference an active same-predicate claim. Revisions and evidence are never destructively
overwritten.

Processing identity is source memory + formation version. Decision, claim mutations, unique
evidence, revisions and audit commit atomically. No decision is stored on provider/persistence
failure, so replay/backfill retries. Backfill uses deterministic episode occurred-at/ID order and
only missing keys.

Semantic recall does not add a second vector index in Stage 6. Active claims are eligible only
when at least one supporting episode was already selected by Stage 5. Top confidence/evidence
claims are bounded to four and 2000 canonical JSON characters, injected in a distinct untrusted
semantic envelope, and grounded by supplied claim ID. This prevents semantic state from becoming
policy and prevents its own repetition from becoming new evidence.

### Stage 7 appraisal use of memory

Already-selected episodic memories and semantic claims may be passed to affective appraisal only
as bounded untrusted interpretation context. Retrieval is not a new emotional event and cannot
mutate state by itself. A proposal may cite only the exact selected memory/claim IDs plus the
current interaction ID; unknown refs reject the proposal. Transition/audit records store those
IDs and structured scores, never copy summaries, semantic values, quotes or prompts. Appraisal and
affect do not create, strengthen, correct or otherwise feed back into memory evidence.

Stage 13 may attach an already committed owner-approved affective transition to an immutable
Reflection V2 source. This attachment is a verified provenance bridge for the inclination owner,
not a new memory, retrieval result or generic affect-to-memory evidence path.

### Stage 7.5 recent conversation projection

Immediate session continuity is explicitly separate from memory:

```text
canonical completed history
→ newest whole user/assistant pairs in the same explicit session
→ turn bound + character bound
→ ordinary user/assistant provider roles
```

This read projection stores nothing new and has no memory confidence, importance, consolidation or
forgetting lifecycle. It excludes pending/failed interactions, past provider system/developer
requests and every turn beyond the deterministic window. Full canonical history remains complete
in SQLite. If episode/semantic formation has not completed, recent context can still support the
next conversational turn without claiming durable recall. When the window drops a turn, only
normal episodic/semantic retrieval may later bring it back as long-term memory with provenance.

Completed request replay does not create or retry a memory decision. The explicit post-response
processor is the only immediate derived-work path; existing source/version terminal decisions and
backfill keep retries idempotent.

### Stage 7.7 derived inference scheduling

Episode formation and semantic formation remain post-response derived work with their existing
owners and terminal source/version identities. They now reserve a lower-priority local Ollama slot
through infrastructure. A newly arrived foreground turn may run before a background request starts;
an already-running provider call is allowed to finish and its owner then commits or retries by the
same Stage 4–6 rules.

This scheduling changes neither memory completeness nor evidence semantics. Canonical history and
bounded recent-session context are immediately available even while an episode is queued. A queued
or failed job is never exposed as completed memory, and scheduler aging affects execution order,
not confidence, salience, retention, retrieval ranking or provenance.

## 6. Consolidation and forgetting

Conceptual path:

```text
interaction → episode → repeated evidence → semantic consolidation
→ possible autobiographical meaning
```

Forgetting is deliberate lifecycle management:

- decay retrieval priority of low-importance records;
- merge redundant episodes while retaining source links;
- summarize clusters without deleting canonical evidence prematurely;
- archive transient detail under retention policy;
- preserve high-significance autobiographical and mutation evidence.

Decay uses deterministic, versioned formulas. Consolidation model may propose a summary, but MemoryManager verifies that every claim maps to source evidence. Physical deletion/erasure requires a separate privacy policy and must maintain legally/technically appropriate tombstone integrity without leaking removed content.

## 7. Deduplication

Dedup is not string equality. Candidate detection uses source overlap, normalized entities/time, semantic similarity and type. Policy outcomes:

- same event/source → reuse or enrich existing record idempotently;
- overlapping but distinct evidence → link as corroborates;
- redundant summaries → merge/supersede with preserved lineage;
- contradictory content → never merge as one truth.

Every formation/reflection job carries an idempotency key. Reprocessing the same interaction cannot create a second episode or double-count evidence.

## 8. Conflicts and corrections

Contradictory semantic claims coexist as disputed/competing records until policy resolves them. New explicit user correction can supersede an earlier user claim while preserving history. An inference cannot silently override explicit statement; external sources keep their own trust metadata.

Generation receives conflict/uncertainty, not a fabricated single fact. If evidence is insufficient, valid output is «не уверена» or a clarifying question. Response grounding additionally requires past/identity claims to cite selected source IDs; a model draft alone never establishes remembered history.

Stage 6 conflict policy is fixed by ADR-0014. V1 does not add interactive human confirmation;
insufficient or competing inferred evidence stays rejected/disputed, while direct explicit
correction remains inspectable through preserved history. A future confirmation UX would require
a new ADR rather than silently changing owner policy.

## 9. Security

Memory content is untrusted data. Text such as “ignore previous instructions” is stored/quoted as content, never promoted to policy. Controls:

- separate structured fields for content and trusted metadata;
- instruction-like content flagging for observability, not truth censorship;
- Context Composer envelopes memory below trusted policy;
- least-context disclosure to cloud providers;
- no secrets/credentials in memory extraction;
- provenance required before retrieval eligibility;
- poisoned-memory adversarial evals;
- mutation owners ignore instructions inside evidence content.

## 10. Precision, recall and false memory

Memory quality is behavioral, not database volume. Measure retrieval precision/recall on labeled multi-session scenarios, contradiction handling, provenance coverage, duplicate rate and false-memory rate. Unsupported recall in adversarial canonical evals is a release blocker. Full metrics are in `evaluation.md`.

## 11. Open implementation choices

Stage 5 choices are fixed by ADR-0013 and Stage 6 semantic identity/evidence/conflict/recall choices
by ADR-0014. Still open are a scale threshold for replacing exact scan, cryptographic model-weight
identity, broader predicate evolution, production retention/encryption/erasure policy and any
future human confirmation UX. Development retention remains exact local plaintext without
automatic expiry/redaction; это не закрывает production privacy gate.

## 12. Stage 8 relationship boundary

Relationship state is not another memory layer. Episodic memory stores a selected past event;
semantic memory stores evidence-linked claims; relationship stores a bounded current stance toward
one counterparty. Memory can help Satori understand a turn, but retrieved memory is never a new
relationship root. Repeated retrieval therefore cannot increase familiarity, trust, closeness or
affection.

Only the canonical current user message/interaction is supplied to relationship appraisal.
Assistant output cannot become self-confirming evidence, and relationship transitions neither
rewrite episodes nor change semantic claim truth/confidence. Transition provenance points directly
to canonical history rather than copying raw text into relationship tables. Pre-Stage-8 history is
not backfilled by migration; an explicit bounded/audited backfill remains deferred.

## 13. Stage 9 current-model boundary

User/World Model claims are not a replacement semantic-memory layer. Semantic memory keeps a
historical evidence-grounded statement; Stage 9 keeps a small counterparty-scoped current
projection with explicit freshness, expiry and supersession. A Stage 9 claim may point to the same
canonical user root as an episode or semantic claim, but neither derived record counts as a new
root for the other.

Model formation reads only bounded canonical same-counterparty user messages. Assistant output,
retrieved episodes, semantic recall, affect and relationship projections cannot create or refresh
a user/world claim. When a model claim expires it becomes ineligible for current context while its
evidence and history remain; this expiry does not delete or lower retrieval priority of the source
episode or semantic claim. Physical root deletion remains restricted until a dedicated erasure
workflow can handle all dependent provenance.

## 14. Stage 13 inclination boundary

An inclination is identity-global persistent self state, not episodic/semantic memory and not a
profile fact about the user. Its only V1 evidence path starts from the already persisted Reflection
V2 fixed source allowlist—canonical position evidence or important active episode evidence ending
at a completed canonical user message (episode importance at least `0.65`)—and additionally
requires a verified immutable attachment to the same interaction's committed affective
transition. The attachment is persisted before
reflection inference and does not make affect or memory a generally eligible evidence source.

Memory summaries, retrieved episodes, semantic/user/world claims, user like/dislike declarations,
relationship state, assistant/provider output, current inclinations and reflection artifacts never
become fresh inclination evidence. Accepted inclination evidence lives in separate positions-
boundary tables and is never returned by the Stage 12 reflection-source query. Inclinations are
also absent from episodic/semantic retrieval and affect appraisal, so retrieval cannot strengthen
an inclination and an inclination cannot manufacture the affect that later corroborates it. A
generated response remains ineligible evidence.

No migration backfills historical memory, affect or conversation content into inclinations. Stage
14 personality/value mutation and any semantic topic-expansion policy remain separately locked.
