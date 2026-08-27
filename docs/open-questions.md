# Open questions

Unknowns are explicit decision gates, not hidden assumptions. When decided, create/update an ADR and remove the item only after linking the decision.

## Resolved Stage 1 gates

Recorded in [ADR-0009](decisions/0009-stage-1-toolchain-and-layout.md).

| Question | Resolution |
|---|---|
| Packaging/dependency workflow | `uv`, `pyproject.toml`, committed `uv.lock` |
| Python and OS baseline | Python 3.12 minimum; macOS Apple Silicon primary; portable core for ordinary Linux |
| Package layout | `src/satori`, not generic `app` |
| Distribution posture | Private; no open-source license added |

## Resolved Stage 2 gates

Recorded in [ADR-0010](decisions/0010-explicit-activation-and-initial-self.md).

| Question | Resolution |
|---|---|
| Exact activation flow | Explicit `ActivateSatori`; one primary DB slot; repeat is typed `AlreadyActivated`; bootstrap/read/migration never activate |
| Identity seed serialization | Strict versioned package JSON mapped to typed input, canonical SHA-256 provenance; DB is authoritative after activation |
| Stage 2 persistence representation | Normalized identity, trait, value and minimal audit records committed atomically; immutable read snapshots |

## Resolved Stage 3 gates

Recorded in [ADR-0011](decisions/0011-stage-3-conversation-context-and-provider.md).

| Question | Resolution |
|---|---|
| Initial conversation provider/model | Local Ollama `/api/chat`; configured `qwen3:4b-instruct` baseline for the 8 GB Apple Silicon machine; optional real smoke, fake/HTTP contracts in CI |
| Stage 3 session representation | Stateless Option A: one current input → one reply; no recent window, provider thread or persistent interaction record |
| Runtime character context | Versioned typed projection of DB identity name, all traits/values and explicit absent capabilities; configured bound, no persistence metadata |
| Response and trust layering | Plain bounded text result; separate trusted policy, trusted application character data and untrusted user role; typed provider errors |

## Resolved Stage 4 gates

Recorded in [ADR-0012](decisions/0012-conversation-history-and-episodic-formation.md).

| Question | Resolution |
|---|---|
| Raw interaction retention/redaction policy | Exact accepted user and committed assistant text is retained as local SQLite plaintext; no automatic redaction/expiry/deletion in Stage 4; no hidden prompts or message content in logs; production data remains gated on encryption, retention and erasure policy |
| Canonical finalize vs episode transaction | Pending input commits before inference; assistant pair/status finalizes atomically before delivery; derived episode formation commits separately and may be retried without losing history |
| Stage 4 session/model-context behavior | Implicit one-turn session by default or explicit multi-turn container; membership persists, but no recent history or memory enters generation context before Stage 5 |
| Episode grounding/idempotency v1 | One proposal per source/version; exact quotes from user messages, bounded scores/summary and minimum importance 0.5; terminal create/skip/reject decision deduplicated by source interaction + formation version |

## Resolved Stage 5 gates

Recorded in [ADR-0013](decisions/0013-episodic-retrieval-and-grounded-context.md).

| Question | Resolution |
|---|---|
| Embedding model and index | Provider-neutral exact space; Ollama `embeddinggemma:300m`/768 default; derived JSON vectors and portable exact cosine scan in SQLite; no extension/service before scale evidence |
| Ranking and context budget v1 | Raw cosine threshold 0.55; semantic top 32; `0.80 semantic + 0.10 importance + 0.10 30-day-half-life recency`; top 4 under 2400-character canonical memory payload |

## Resolved Stage 6 gates

Recorded in [ADR-0014](decisions/0014-semantic-memory-evidence-and-consolidation.md).

| Question | Resolution |
|---|---|
| Semantic identity and scope | Subject `user` only; closed small predicate registry; typed scalar value plus polarity; epistemic kind remains historical; no generic graph or user/Satori belief domain |
| Evidence independence and confidence v1 | Complete lineage to Stage 4 root user message; roots deduplicated by message; inference requires two messages and interactions; deterministic kind/source-count caps; provider score only lowers a cap |
| Correction and contradiction v1 | Exact identity merges unique roots; newer explicit single-value evidence supersedes non-destructively; inference yields to explicit; competing inferences become disputed; direct correction targets an active same-predicate claim |
| Semantic recall v1 | Active claims are selected only through Stage 5 retrieved evidence episodes, bounded top 4/2000 chars, isolated in a separate untrusted context envelope |

## Resolved Stage 7 gates

Recorded in [ADR-0015](decisions/0015-affective-state-appraisal-decay-and-mood.md).

| Question | Resolution |
|---|---|
| Fast affect and mood spaces | Nine continuous fast dimensions and three distinct mood dimensions; no relationship dimension or user emotion model |
| Decay model | Pure lazy baseline-centred exponential half-life decay with per-dimension v1 parameters; no heartbeat/background job |
| Appraisal authority | Provider returns semantic signals/source refs/confidence only; `EmotionManager` applies deterministic personality modulation, caps, bounds and mood |
| Finalize and retry | Tentative state shapes generation; transition/state/audit and canonical assistant completion commit atomically; completed replay is no-op and stale base conflicts require re-appraisal |

## Resolved Stage 7.5 engineering gates

Recorded in [ADR-0016](decisions/0016-interactive-runtime-context-and-delivery.md).

| Question | Resolution |
|---|---|
| Interactive runtime/session | One process, one explicit session, reused adapters/shared HTTP pools, graceful drain/close |
| Immediate continuity | Bounded canonical completed-pair read projection; not episodic/semantic memory and never full-history stuffing |
| Reply vs derived memory | Canonical assistant/affect commit makes full reply deliverable; episode/index/semantic run afterward through an idempotent in-process processor |
| Token streaming | Deferred because current canonical contract has no durable fragment draft/outbox lifecycle; progress indicator is used instead |
| Ollama residency/telemetry | Finite configurable chat `keep_alive`; documented duration/count metadata only; no undocumented embed residency field |

## Resolved Stage 7.6 engineering gates

Recorded in [ADR-0017](decisions/0017-runtime-self-model-and-character-expression.md).

| Question | Resolution |
|---|---|
| Self-knowledge authority | Immutable runtime projection derived from DB self, actual capability availability and configured provider/model; no new persistence owner |
| Digital female identity | Stable constitutional identity with feminine Russian grammar; not a biological-body claim, roleplay mask or user-selected style |
| Qwen/provider relation | Current replaceable language component is disclosed when relevant and never conflated with persistent Satori identity |
| Recent self-contradiction | Canonical recent assistant text remains continuity data, followed by a compact trusted current-turn reminder; history is never rewritten |
| Personality-to-voice mapping | Deterministic versioned soft guidance with source traits/strengths; no threshold catchphrases or second personality seed |
| Behavioral enforcement | Deterministic request contracts plus sampled real-model corpus; no production banned-phrase filter, output rewriting or LLM self-judge |

## Resolved Stage 7.6.1 engineering gates

Recorded in [ADR-0018](decisions/0018-contextual-self-expression-and-disclosure.md), which
supersedes ADR-0017 for provider projection and evaluation.

| Question | Resolution |
|---|---|
| Full self truth vs natural speech | Complete typed self remains in application; provider receives a compact projection selected by deterministic current-turn disclosure depth |
| Social/personal over-disclosure | Qwen, embodiment and relationship capability facts are absent unless the current question makes them relevant |
| Affect narration | Numeric authoritative state remains internal; generation receives a qualitative tone hint plus versions and cannot mutate either |
| Relationship limitation wording | Current absence is epistemic incompleteness, not love, attachment, future promise or permanent incapacity; no state was added |
| Small-model verbosity/variance | Per-mode output/temperature bounds and configurable default `0.3`; no output repair or second LLM judge |
| Behavioral quality evidence | Eleven-dimension manual rubric plus negation-aware deterministic diagnostics and all raw real-Ollama samples |

## Resolved Stage 7.7 engineering gates

Recorded in [ADR-0019](decisions/0019-local-inference-priority-and-categorical-appraisal-wire.md).

| Question | Resolution |
|---|---|
| Shared local inference contention | One heavy call per Ollama origin; conversation/appraisal foreground priorities, episode/semantic derived priorities, short grace and bounded aging |
| Appraisal wire size | Closed categorical infrastructure wire maps deterministically to the unchanged continuous application proposal before owner validation |
| Appraisal model | `qwen3:4b-instruct` remains the independent default; tested 0.6B/1.5B candidates failed semantic quality despite schema work |
| Cheap appraisal gate | Rejected until an evidence-backed conservative classifier demonstrates an acceptably low false-skip rate; current rate remains zero by always appraising |
| Combined inference | Rejected because post-generation appraisal would remove current-event authoritative affect from the same reply |
| Startup warmup | Not enabled: warm load is already small and moving wait to startup would not reduce steady-state prompt/eval cost |

## Resolved Stage 8 gates

Recorded in [ADR-0020](decisions/0020-persistent-counterparty-relationship-model.md).

| Question | Resolution |
|---|---|
| Exact v1 dimensions | Familiarity, trust, comfort, closeness, intellectual respect and non-romantic affection; evidence maturity remains separate |
| Initial meaning | `(0,.5,.5,0,.5,0)` plus maturity zero means little evidence, not distrust/discomfort/disrespect |
| Evidence root | One canonical completed current user message/interaction; no assistant/affect/retrieval/model feedback |
| Update timing | Post-canonical derived work, future-turn only, with relationship scheduler priority above episode/semantic |
| Bounds and growth | Maturity ceilings, saturating confidence-weighted impulses, per-event and signed per-session caps |
| Love/dependency boundary | No love/romance/attachment/dependency/exclusivity primitives; affection never authorizes reciprocity or obedience |
| Historical rollout | Migration marks existing interactions ineligible; no automatic backfill or migration-side LLM |
| Reliability evidence | Reserved domain categories, omitted from single-current-root v1 wire until independent canonical follow-through can be supplied |

## Resolved Stage 8.1 engineering gates

Recorded in [ADR-0021](decisions/0021-dialogue-coherence-and-compositional-disclosure.md), which
supersedes ADR-0018 for provider disclosure selection while preserving its state/trust boundaries.

| Question | Resolution |
|---|---|
| Repetition/correction continuity | Pure bounded `DialogueCoherenceContext` derived per request from current input and canonical recent pairs; no cross-session persistence |
| Mixed disclosure | One primary conversational mode plus every required authoritative facet; critical self facts do not disappear because another mode wins |
| Questions and policy voice | Follow-up is optional and specific; corrections are acknowledged; internal policy guides behavior rather than becoming a catchphrase |
| Relationship unknown | Low maturity/uncertain midpoint remains little evidence; baseline warmth/openness/curiosity comes from personality, not invented intimacy |
| Capability vs curiosity | Physical/visual inability constrains capability claims but never implies disinterest in the user's activity or experience |
| Prior assistant self claims | Canonical continuity data remains untrusted about identity, affect, provider, embodiment and origin |
| Narrow response self-consistency | Closed ten-reason deterministic validator (changed-dialogue duplicate, corrected routine question, masculine self-reference, human/biological self claim, blanket affect/memory denial, creator-fact promotion, invented origin backstory, blanket prompt/policy denial, activity-interest false negative) may make at most one additional provider call in the same interaction with the same tentative affect/evidence set; normal path is one call, no output rewrite/judge/state mutation, metadata-only `self_consistency_violation_detected` for non-duplicate reasons |
| Creator claim now | Acknowledge only as a claim made in the current input while authoritative creator provenance is unknown; neither fabricate, dismiss nor persist it as a creator fact |

## Decide before the named Stage

Stage 11 belief/opinion identity, merge and contradiction semantics are resolved by
[ADR-0024](decisions/0024-evidence-linked-satori-positions.md): exact normalized identity,
canonical material evidence, explicit versioned revision/challenge/competition and no semantic
graph or provider-created fact.

Stage 12 reflection cadence, cost, immutable source set, cycle prevention and owner routing are
resolved by [ADR-0025](decisions/0025-bounded-reflection-runs-and-owner-routing.md): opportunistic
weekly automatic eligibility plus bounded explicit local processing, canonical leaf-only inputs
and resumable per-proposal owner transactions.

Stage 13 preference/interest decay and evidence diversity are resolved by
[ADR-0026](decisions/0026-evidence-backed-satori-inclinations.md): a separate identity-global
inclination aggregate under `PositionManager`, Reflection V2 sources with immutable affect
attachments, explicit multi-session/span thresholds, owner-derived bounded deltas, exact
cooldowns/rolling budgets and pure neutral-centred decay. User tastes, relationship state,
assistant/provider output and existing inclinations remain ineligible evidence; context influence
is relevant-turn-only and cannot enable proactivity.

Stage 14 trait distance, cumulative budget, checkpoint and rollback semantics are resolved by
[ADR-0027](decisions/0027-bounded-personality-evolution-and-checkpoint-restore.md): complete-vector
`D∞`/`D1` plus non-refundable cumulative path, exact `±0.005` owner steps, separate rolling/
lifetime/activation/approved-checkpoint bounds, immutable full-vector checkpoints, explicit
append-only approval and version-increasing restore. Personality evidence uses a separate 90-day
Reflection V3 purpose without affect, relationship or inclination feedback, and context v16 adds
only bounded qualitative current-vs-baseline cues.

Checkpoint 14.1 foreground provider routing, secret target and first cloud scope are resolved by
[ADR-0028](decisions/0028-yandex-ai-studio-foreground-provider.md): Ollama remains default; Yandex
AI Studio is opt-in and limited to foreground conversation; credentials are pinned to the
canonical endpoint; automatic fallback and every structured/background cloud call remain deferred
until real A/B and cost/privacy evidence exists.

| Question | Decision gate | Evaluation/input required |
|---|---:|---|
| Creator attribution/role schema, provenance and correction semantics | Stage 9 or later dedicated ADR | Distinguish activation origin, developer attribution and user claim; correction, conflict, privacy and export fixtures |
| Intent representation: open tags vs registry/taxonomy | 10 | Pipeline implementation experience and observability needs |
| Autobiographical significance policy | 15 | Narrative quality vs over-retention evaluation |
| Emotional concept clustering/prototype semantics | 16 | Sufficient longitudinal emotional data |
| Quiet hours/rate limits/user controls for proactivity | 19 | UX consent model and annoyance-risk study |
| Durable fragment streaming/outbox semantics | 20 or earlier separately authorized work | Draft/fragment persistence, cancellation, retry and client delivery failure-mode prototype |
| TTS/STT/avatar/tool vendors and formats | 20+ | Core stability plus platform benchmarks |
| Backup encryption, key management, erasure and production retention | Before real-user production data | Threat model, platform keychain options, legal/product posture |
| Yandex structured routing, automatic fallback and enforceable ruble budgets | After checkpoint 14.1 real A/B | Per-capability schema quality, retry/idempotency matrix, latency/token/cost distributions and privacy review |

## Can defer beyond v0.1

- Whether rare offline reflection should run while no interaction is active.
- Emotional concept user-visible naming and language.
- Live2D vs equivalent parameter-driven avatar runtime.
- Native client technology and multi-device sync architecture.
- Vision model routing and visual-memory retention.
- Calendar/tasks/files/web/computer integrations and permission UX.
