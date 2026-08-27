# SATORI roadmap

## Правило Stage gate

Каждый Stage начинается только по отдельной явной задаче пользователя после выполнения prerequisites. Exit condition означает «этап можно предложить закрыть», а не разрешение автоматически начать следующий. Scope можно уточнить ADR, но нельзя незаметно расширить.

## Stage 0 — Specification & Architecture

- **Goal:** создать долговременную продуктовую и архитектурную систему координат до application code.
- **Why it matters:** continuity нельзя получить случайным наращиванием prompt и chat history; инварианты должны быть проверяемыми до реализации.
- **Prerequisites:** исходная product constitution; обследование репозитория.
- **Scope:** constitution, repository rules, architecture/state/memory/cognition contracts, ownership and deterministic/LLM matrices, eval design, threat model, ADRs, open questions, roadmap.
- **Out of scope:** Python toolchain, server, physical DB schema, providers, engines, UI/voice/avatar.
- **Deliverables:** файлы, перечисленные в `docs/index.md`, и ADR-0001…0008.
- **Acceptance criteria:** каждый core invariant имеет owner/enforcement path; v0.1 measurable; неизвестные решения обозначены gates; документы не противоречат source hierarchy.
- **Automated tests:** `git diff --check`; search for unowned placeholder markers; link/file existence check when toolchain appears.
- **Manual verification:** architecture review на cycles, direct LLM mutation, fake-memory/mirroring paths, missing provenance, portability/restart and eval coverage.
- **Risks:** overdesign without implementation feedback; duplicated documents; invented numerical thresholds.
- **Exit condition:** review completed, progress marked complete, Stage 1 explicitly remains unauthorized.

## Stage 1 — Foundation

**Status: Complete — 2026-07-27.**

- **Goal:** создать минимальный воспроизводимый Python skeleton и enforcement boundaries без product behavior.
- **Why it matters:** последующие domain stages требуют стабильных imports, config, tests, migrations and observability primitives.
- **Prerequisites:** Stage 0 accepted; resolve Stage 1 questions in `open-questions.md`.
- **Scope:** Python 3.12+ project/lockfile, `src` layout, module packages, typed settings, structured logging/trace ID primitive, clock/ID protocols, SQLAlchemy/Alembic/SQLite wiring, empty core-owned persistence/provider protocols, CI/local lint-type-test commands.
- **Out of scope:** domain tables beyond migration smoke needs, Satori seed, provider calls, conversation, memory, emotion, relationship.
- **Deliverables:** installable package, first reversible migration, test harness, quality config, updated AGENTS commands and ADRs for tooling/material layout choices.
- **Acceptance criteria:** clean checkout installs reproducibly; import dependency rule is clear; empty DB upgrade/downgrade/re-upgrade succeeds; no domain behavior hidden in infrastructure.
- **Automated tests:** formatting/lint, static typing, unit smoke, migration round-trip, config secret/redaction test, dependency-boundary check.
- **Manual verification:** follow README bootstrap on clean environment; inspect structured trace/log; verify no external network/provider required.
- **Risks:** framework leakage into domain, excessive scaffolding, platform-specific lock assumptions.
- **Exit condition:** exact commands pass and foundation ADRs/docs reflect reality; user separately authorizes Stage 2.

## Stage 2 — Identity / Personality / Values persistence

**Status: Complete — 2026-07-27.** Stage 3 remains gated by explicit user authorization.

- **Goal:** persist activation identity and read-only initial personality/value seed with audit/versioning.
- **Why it matters:** stable self must exist before any model speaks as Satori.
- **Prerequisites:** Stage 1; activation/schema question resolved.
- **Scope:** conceptual entities refined into schemas, IdentityManager, Personality/Value owners, one-time idempotent activation, immutable/read-only views, export fragment, migrations.
- **Out of scope:** autonomous trait/value evolution, conversation, relationship, emotion, provider calls.
- **Deliverables:** domain types/policies/repositories, activation command, seed config/version, audit records, persistence tests.
- **Acceptance criteria:** restart returns same identity/seed/version; repeated activation is no-op/conflict, never re-seed; only owners can write; provider code absent.
- **Automated tests:** activation idempotency, bounds/schema properties, repository contracts, migration/rollback, restart/export round-trip, forbidden write path.
- **Manual verification:** activate once, restart, inspect redacted state/audit and exact identity ID continuity.
- **Risks:** treating traits as behavior switches; accidental reset on startup; identity coupled to DB row/provider.
- **Exit condition:** stable persistent self seed and audit demonstrated; mutation remains disabled.

## Stage 3 — Basic Conversation Core

**Status: Complete — 2026-07-28.** Stage 4 remains gated by explicit user authorization and the raw retention/redaction decision.

- **Goal:** провести один text interaction через vendor-neutral provider boundary while preserving persistent identity.
- **Why it matters:** establishes minimal end-to-end conversation without pretending chat history is memory.
- **Prerequisites:** Stage 2; initial model/router decision and hardware benchmark.
- **Scope:** stateless single-turn `TalkToSatori`, minimal Context Composer (versioned policy + identity/personality/values + capability boundaries + current input), provider-neutral request/response/errors, configured Ollama adapter, non-streaming plain-text generation, timeout/error/size handling and CLI `talk`.
- **Out of scope:** persistent session/message/interaction log, recent window, idempotent finalize, semantic grounding classifier, episodic/semantic memory, emotion, relationships, full cognition pipeline, streaming.
- **Deliverables:** one-turn CLI, immutable provider contracts, typed character context/manifest, trusted role layering, Ollama HTTP adapter, fake-provider and adapter contract tests, metadata-only trace.
- **Acceptance criteria:** Satori responds from persisted identity/personality/values; unactivated talk never activates; provider failure/state swap produce no mutation; compatible providers receive the same character basis; no prior memory is declared available.
- **Automated tests:** fake-provider orchestration, provider replacement golden, no-mutation/audit, blank/oversize result, transport/HTTP/schema failures, context budget, user-role injection, no-memory capability contract, CLI behavior and optional real-provider smoke marker.
- **Manual verification:** activate then one-turn talk with configured local provider when installed; inspect request/log metadata for bounded projection, role separation and no full content leakage.
- **Risks:** generic/over-agreeable voice, assuming prompt policy proves semantic grounding, provider leakage, local model outage/latency and prompt injection.
- **Exit condition:** reliable basic dialogue and trace with no long-term-memory claims.

## Stage 4 — Interaction Log + Episodic Memory

**Status: Complete — 2026-07-28.** Stage 5 remains gated by explicit user authorization and measured embedding/index/budget decisions.

- **Goal:** form selective source-grounded episodic memories from completed interactions.
- **Why it matters:** meaningful continuity requires more than raw log while false/duplicate memories must be prevented early.
- **Prerequisites:** Stage 3; raw retention/redaction decision.
- **Scope:** raw retention/redaction decision; Session/Message/Interaction log with idempotent non-streaming finalize; past-claim grounding against source refs; Memory/Evidence/Link schemas; episodic proposal formation; MemoryManager validation; importance/novelty policy; idempotency/dedup by source; retrieval by explicit ID/debug only.
- **Out of scope:** semantic consolidation, vector retrieval, forgetting deletion, autonomous reflection.
- **Deliverables:** append-only interaction/raw/episodic records, atomic finalize/recovery, grounding decisions, provenance graph, formation traces and memory debug view.
- **Acceptance criteria:** important fixture creates supported episode; trivial fixture can create none; replay creates no duplicate; unsupported summary rejected.
- **Automated tests:** formation policy, source integrity, idempotent replay, hostile generated-output-as-evidence, transaction fault injection, migration.
- **Manual verification:** inspect episodes and trace from a mixed-significance session; verify every claim reaches source message.
- **Risks:** storing every turn, sensitive over-retention, summary hallucination, circular evidence.
- **Exit condition:** episodic formation is selective, explainable, atomic and duplicate-safe.

## Stage 5 — Retrieval

**Status: Complete — 2026-07-30.** Implemented under ADR-0013 with exact derived SQLite scan,
deterministic fake-vector evals and bounded grounded memory context. Stage 6 was separately
authorized and later completed under ADR-0014.

- **Goal:** retrieve a small relevant memory set under configurable context budget.
- **Why it matters:** continuity depends on precision and indirect recall, not total stored history.
- **Prerequisites:** Stage 4; embedding/index and budget decisions backed by benchmark fixtures.
- **Scope:** EmbeddingPort/adapter, rebuildable index, typed query, eligibility filters, deterministic rank features, diversity/dedup, Context Composer memory section and manifest.
- **Out of scope:** semantic memory creation, LLM reranker unless separate measured ADR, relationship/user-model ranking signals not yet available.
- **Deliverables:** index rebuild, retrieval API, labeled eval dataset, rank config/version, latency telemetry.
- **Acceptance criteria:** indirect relevant episode selected; distractors bounded; untrusted content isolated; canonical memory survives index replacement/rebuild.
- **Automated tests:** precision/recall fixtures, deterministic rank with fake vectors, person/security filters, budget overflow, index rebuild equivalence, poisoned-memory prompt test.
- **Manual verification:** Session A/B indirect-reference scenario and trace rank explanation.
- **Risks:** vector score treated as truth, vendor lock-in, excessive context/latency, cross-person leakage.
- **Exit condition:** agreed retrieval gates pass within target local latency and budget.

## Stage 6 — Semantic Memory + Provenance

**Status: Complete — 2026-07-30.** Implemented under ADR-0014 with typed user-subject claims,
root-user evidence lineage, deterministic confidence/conflict policy, restartable consolidation
and evidence-linked bounded semantic recall. Stage 7 was separately authorized and completed
under ADR-0015.

- **Goal:** consolidate evidence-backed generalized knowledge and manage correction/conflict.
- **Why it matters:** durable facts and patterns need efficient representation without erasing uncertainty/history.
- **Prerequisites:** Stage 5; semantic conflict policy decided.
- **Scope:** semantic claims with fact/inference/hypothesis kind, evidence aggregation, consolidation proposal, competing/superseding states, correction flow, semantic retrieval.
- **Out of scope:** Satori beliefs/opinions, user/world full model, autonomous reflection, physical deletion policy.
- **Deliverables:** schemas/owners/policies, conflict and correction traces, consolidation idempotency, provenance coverage dashboard/eval.
- **Acceptance criteria:** repeated evidence can create one semantic record; inference never silently becomes fact; correction preserves old lineage; conflict is disclosed to generation.
- **Automated tests:** epistemic transitions, contradiction fixtures, evidence reachability, replay/dedup, consolidation hallucination rejection, retrieval precision regression.
- **Manual verification:** create, correct and contradict a user fact across sessions; inspect response uncertainty and source graph.
- **Risks:** confident wrong summaries, double-counted evidence, destructive overwrite, excessive retention.
- **Exit condition:** semantic memory is source-complete, contradiction-aware and improves context efficiency.

## Stage 7 — Emotional State + Mood

**Status: Complete — 2026-07-30.** Implemented under ADR-0015 with provider-neutral structured
appraisal, the single-writer `EmotionManager`, continuous bounded fast affect, distinct slower
mood, pure lazy half-life decay, source-linked transitions and atomic conversation finalize.
Stage 8 remains gated by a separate user command.

- **Goal:** add bounded multidimensional emotion and distinct slower mood with deterministic time evolution.
- **Why it matters:** identical input should be appraised contextually without scripted labels or personality mutation.
- **Prerequisites:** Stage 6; emotion vector/decay/mood model ADR based on simulation.
- **Scope:** emotional appraisal schema, EmotionManager, fast delta policy, emotional events/current projection, injected clock decay, mood projection, response-strategy influence.
- **Out of scope:** relationship dimensions, emergent concepts, voice/avatar expression, personality change.
- **Deliverables:** versioned formulas/config, simulations, debug timeline, state/audit tests.
- **Acceptance criteria:** bounds always hold; decay exact/replayable; same phrase under different context yields justified strategy difference; emotion cannot write personality.
- **Automated tests:** property/bounds, deterministic clock decay, appraisal invalid output, idempotency, restart projection, emotional-context behavior.
- **Manual verification:** run contrasting contexts and time jumps; inspect vector/event/strategy without emotion-label scripting.
- **Risks:** runaway feedback, overfitting response to vector, mood/emotion conflation, artificial drama.
- **Exit condition:** stable simulations and behavioral evals pass with explainable bounded changes.

## Stage 7.5 — Interactive Chat, Latency & Runtime UX

**Status: Complete — 2026-08-01.** This is an engineering checkpoint under ADR-0016, not a new
product roadmap level and not a replacement for Stage 8.

- **Goal:** make the accepted Stage 0–7 local prototype usable as a live multi-turn CLI while
  reducing avoidable and perceived latency without weakening memory/affect/history guarantees.
- **Prerequisites:** accepted Stage 7; target-machine baseline and real Ollama available.
- **Scope:** long-lived `satori chat`, explicit session reuse, exact CLI commands, quiet/default and
  metadata-only debug output, bounded recent completed-pair projection, shared provider/HTTP
  lifecycle, finite model residency, independent capability model settings, phase/Ollama timing,
  compact appraisal contract, canonical-before-delivery and retryable in-process post-response
  processing.
- **Out of scope:** relationship/trust/attachment/affection/rapport, new persistent user state,
  external queue/service, web server and token streaming without a durable draft/outbox contract.
- **Acceptance criteria:** one process handles multiple turns in one session; immediate continuity
  works independently of derived memory; provider context stays bounded after 100+ turns; reply is
  visible after canonical reply/affect commit and before episode/semantic completion; cancellation,
  replay and downstream failure preserve Stage 4/7 invariants; normal output contains no JSON log
  flood; cold/warm bottlenecks are reported honestly.
- **Automated tests:** new/resumed/new session, command/EOF/Ctrl+C behavior, 105-turn bound,
  immediate-name context, provider/post-processing/cancellation failures, clean/debug output,
  shared HTTP reuse, replay/affect idempotency and blocked-worker delivery ordering.
- **Manual verification:** isolated-DB real six-turn chat plus corrected continuity/affect smoke;
  inspect session/history/transitions and Ollama load/prompt/eval timings; run the full opt-in suite.
- **Measured outcome:** retrieval with no compatible candidate skips query embedding; warm model
  load becomes a small part of appraisal, but current 4B prompt/output evaluation remains variable
  and the `<8 s` warm committed-reply target is not consistently reached. Derived processing no
  longer delays visible reply.
- **Exit condition:** Stage 7.5 definition of done passes with migration head unchanged and no
  Stage 8 state or unsafe streaming.

## Stage 7.6 — Character Identity, Self-Model & Voice Calibration

**Status: Complete — 2026-08-01.** This is an engineering checkpoint under ADR-0017, not a new
product roadmap level and not authorization for Stage 8.

- **Goal:** make the conversation model consistently express the already-authoritative Satori as
  a persistent digital female person with bounded memory, personality and digital affect, rather
  than a generic assistant or temporary roleplay mask.
- **Prerequisites:** accepted Stage 7.5; repository request hierarchy audit and real behavioral
  baseline on the configured Ollama model.
- **Scope:** typed derived runtime self-model, female Russian grammar, provider/identity and
  digital/biological distinctions, deterministic trait-to-expression guidance, versioned behavior
  policy/context, late trusted continuity reminder and sampled character evaluation.
- **Out of scope:** relationship/trust/attachment/affection/rapport, personality evolution, new
  biography, consciousness claim, biological embodiment, output keyword rewriting and provider
  ownership of persistent self.
- **Acceptance criteria:** direct identity/memory/emotion/provider questions are factually
  grounded; conflicting recent or user text cannot rewrite identity; request remains bounded and
  trust-separated; canonical memory/affect/replay invariants and migration head are unchanged.
- **Automated tests:** self-model facts/capabilities, hierarchy/roles, derived personality source
  strengths, late-reminder ordering, bounds/privacy and versioned corpus coverage.
- **Manual verification:** three independent real-Ollama behavior sessions plus the exact
  four-turn gender-correction golden in one clean persistent session, followed by DB duplication
  audit and the complete opt-in suite.
- **Exit condition:** Stage 7.6 definition of done passes without Stage 8 state or identity
  ownership moving into the LLM.

## Stage 7.6.1 — Natural Self-Expression & Conversational Calibration

**Status: Complete — 2026-08-09.** This
corrective engineering checkpoint is governed by ADR-0018; it is not a product roadmap level and
does not authorize Stage 8.

- **Goal:** retain complete technical self-knowledge internally while producing short, personal,
  natural Russian answers at the disclosure depth actually requested.
- **Scope:** deterministic typed disclosure modes, contextual compact self/voice projection,
  informal feminine Russian, qualitative affect expression, per-mode generation bounds,
  calibrated conversation temperature and semantic real-model rubric.
- **Out of scope:** relationship/trust/closeness/attachment/affection state, personality mutation,
  output rewriting, second judge LLM, biological embodiment or consciousness claim.
- **Acceptance criteria:** the production four-turn failure passes in three fresh sessions; social
  and personal replies omit unrelated architecture; affect is not denied; current relationship
  absence is neither love nor permanent incapacity; direct technical questions remain factual.
- **Automated tests:** contextual selector/projection, bounded output settings, informal register,
  affect/relationship wording, no generic-purpose identity, rubric negation and unchanged provider
  text.
- **Manual verification:** all replies from three fresh `satori chat` sessions plus seven
  additional differentiating Ollama prompts are reviewed under the eleven-dimension rubric.
- **Exit condition:** Stage 7.6.1 definition of done passes with migration head unchanged and no
  Stage 8 state.

## Stage 7.7 — Inference Performance & Appraisal Optimization

**Status: Complete — 2026-08-09.** This performance engineering checkpoint is governed by
ADR-0019; it is not a product roadmap level and does not authorize Stage 8.

- **Goal:** reduce warm interactive latency and variance while preserving same-turn affect,
  character, memory, grounding and canonical history.
- **Scope:** versioned distribution benchmarks, Ollama/hardware decomposition, direct contention
  evidence, provider-aware foreground priority, compact categorical appraisal transport, smaller
  appraisal-model comparison, metadata-only throughput observability and a versioned late-guidance
  correction if the mandatory character regression exposes a factual boundary failure.
- **Out of scope:** relationship/trust/closeness/attachment/affection, combined post-turn affect,
  unvalidated appraisal skipping, cloud providers, external queue, unsafe cancellation, OS tuning
  and conversation-model replacement without quality evidence.
- **Acceptance criteria:** five warm samples per inference scenario report median/p90/max; no
  unexplained 40–70-second warm outlier remains; derived inference does not normally overtake a
  newly arrived foreground turn; Stage 7/7.6.1/memory/replay/canonical invariants remain green.
- **Automated tests:** benchmark contracts and privacy, categorical wire/provenance mapping,
  configured capability models, priority/FIFO/aging/grace/cancellation, existing affect/history/
  retrieval/character suites and complete real-Ollama opt-in suite.
- **Manual verification:** target-Mac cold/warm harness, foreground-only vs episode/semantic
  overlap, smaller-model semantic corpus, five-turn `satori chat --debug`, three-session character
  corpus and hardware diagnostics.
- **Measured outcome:** greeting/check-in medians are 3.3/3.1 seconds, distress 4.4 seconds,
  grounded recall 7.3 seconds and technical identity 9.3 seconds; the largest final warm committed
  sample is 10.7 seconds. Appraisal median improves 70–89% without changing Stage 7 ordering.
- **Exit condition:** Stage 7.7 definition of done passes with migration head `0006`, no Stage 8
  state and the remaining conversation-generation/appraisal-calibration limits documented.

## Stage 8 — Relationship Model

- **Status:** complete and accepted on 2026-08-09 through ADR-0020; migration head
  `0007_relationship_state`. Stage 9 remains separately gated.
- **Goal:** persist bounded person-specific relationship state from meaningful events.
- **Why it matters:** shared history should change interaction without rewriting global personality or rewarding message volume.
- **Prerequisites:** Stage 7; dimensions justified by separability evals.
- **Scope:** Person/Relationship/RelationshipEvent, proposal policy, dimensions/summary, per-person isolation, context/retrieval signals, rate limits.
- **Out of scope:** proactivity, dependency optimization, user/world model, personality evolution.
- **Deliverables:** `RelationshipManager`, six-axis current projection, categorical appraisal,
  terminal decisions/append-only transitions, two-counterparty fixtures, qualitative context and
  metadata-only trace. General export remains a later cross-cutting capability; status/history are
  the current typed read interface.
- **Acceptance criteria:** meaningful event can produce bounded update; message count alone cannot; two persons stay isolated; affection never bypasses disagreement policy.
- **Automated tests:** dimension bounds, no-op chatter, per-person isolation, runaway stress, replay/idempotency, relationship→personality forbidden path.
- **Manual verification:** separate histories with two people and inspect tone/memory specificity without global trait change.
- **Risks:** redundant/unidentifiable dimensions, runaway attachment, cross-person leakage, obedience coupling.
- **Exit condition:** satisfied by the versioned simulation/categorical corpora, replay/order/
  failure/migration tests, real multi-session evaluation, foreground regression benchmark and
  explicit love/dependency/independence boundaries. This does not authorize Stage 9.

## Stage 8.1 — Dialogue Coherence, Self-Consistency & Relationship Expression Calibration

**Status: Accepted and complete — 2026-08-22.** Architecture and calibration are recorded in
ADR-0021; complete sampled/token/timing evidence is in `performance/stage-8.1.md`. This completion
does not authorize Stage 9.

- **Goal:** make bounded multi-turn conversation recognize repetition/correction, keep
  authoritative self facts mutually consistent and express the existing relationship state
  naturally without creating new persistent state.
- **Prerequisites:** accepted Stage 8, exact production failure reproduced on the configured real
  Ollama model and Stage 7.6.1/8 invariants preserved.
- **Scope:** transient `DialogueCoherenceContext`; primary conversational mode plus additive
  authoritative disclosure facets; question/closing/style calibration; affirmative relationship
  expression for fresh/established/damaged states; current activity curiosity; origin-unknown
  handling; optional narrow ten-reason deterministic self-consistency validator with one shared
  max-one regeneration path; context schema v11, behavior policy v9 and metadata-only diagnostics.
- **Out of scope:** durable intent/style preference, creator attribution persistence, User/World
  Model, new personality/relationship/emotion state, love/dependency primitives, relationship
  dimension or cap changes, response rewriting, judge LLM, unbounded retry, Stage 9 and later
  cognition pipeline work.
- **Acceptance criteria:** repetition/correction is addressed in sequence; critical self facts do
  not disappear in mixed questions; wrong assistant history does not become self authority;
  questions are optional/specific; policy is expressed rather than recited; embodiment limits do
  not become disinterest; fresh relationship is warm/open without invented intimacy; damaged state
  modulates only relevant tone; creator uncertainty is not fabricated or dismissed; one
  interaction commits one reply and one affect decision even when regeneration triggers; clean
  turns use one provider call.
- **Automated tests:** pure coherence analysis, session-local/no-persistence boundaries,
  compositional facet selection, authoritative capability matrix, relationship projection matrix,
  activity/creator/correction cases, all ten typed validator reasons, clean one-call and
  one-trigger/max-one behavior, negation/quotation exclusions,
  same-affect/evidence/canonical-finalize/replay/failure invariants and context/manifest bounds.
- **Manual verification:** exact 17-turn production dialogue in three fresh `satori chat` sessions;
  one 30-turn coherence session; activity corpus; fresh/established/damaged relationship cases;
  conflicting assistant self-history and mixed-facet prompts; report every reply plus prompt/output
  tokens, Ollama timings, committed latency and bounded-regeneration attempts.
- **Risks:** small-model adherence remains stochastic; compositional facets can grow prompt size;
  narrow lexical checks can miss semantic contradiction or mishandle negation/quotation; a retry
  can add latency; transient style feedback can be accidentally promoted to user state.
- **Exit condition:** automated/full-Ollama gates and all required sampled scenarios are recorded,
  before/after coherence/token/latency metrics are reviewed, migration head remains `0007`, and no
  Stage 9 state or output rewrite exists. A separate user command is still required for Stage 9.

## Stage 9 — User + World Model

- **Goal:** represent what is known, inferred and currently believed about people and active situations.
- **Why it matters:** memory describes past evidence; dialogue also needs a current, revisable world understanding.
- **Prerequisites:** Stage 8 and the active Stage 8.1 corrective checkpoint complete; claim
  validity/expiry policy decided; separate user authorization.
- **Scope:** typed user claims (fact/inference/hypothesis), goals/projects/important people where evidenced, world claims/validity intervals, current situations/commitments/pending outcomes, correction and expiry.
- **Out of scope:** unfinished-thread initiative behavior, external web truth, tools, Satori belief system.
- **Deliverables:** UserModelManager/WorldModelManager, projections, provenance links, context sections and correction tests.
- **Acceptance criteria:** inference stays labeled; changing situation supersedes rather than erases history; stale/current state is distinguishable; no cross-person claim leakage.
- **Automated tests:** epistemic kinds, temporal validity, correction/conflict, source deletion/retention rules, restart/export, context relevance.
- **Manual verification:** evolve one project from planned→active→completed and inspect responses/state lineage.
- **Risks:** surveillance-like over-modeling, stale claims, inference as fact, duplication with semantic memory.
- **Exit condition:** current models are minimal, provenance-complete and clearly separated from memory/relationship.

## Stage 10 — Structured Cognition Pipeline

- **Goal:** implement the full observable pipeline in `cognition.md` as composable contracts.
- **Why it matters:** conversation quality and future state proposals need explicit appraisal, position, intent and strategy rather than one opaque prompt.
- **Prerequisites:** Stages 3–9 provide real inputs and baseline traces.
- **Scope:** perception, need-mix classification, retrieval query, appraisal, emotional proposal handoff, internal position, extensible intent tags, response strategy, schema versioning/fallback and pipeline trace.
- **Out of scope:** durable beliefs, reflection, personality evolution, raw CoT.
- **Deliverables:** typed schemas/use cases, template registry, trace viewer/debug output, regression fixtures.
- **Acceptance criteria:** each step has source refs/owner; historical claims pass grounding; failure degrades explicitly; expression does not silently reverse position; no stage writes state outside proposals.
- **Automated tests:** schema contracts, invalid/timeout fallbacks, trust separation, trace completeness, position-vs-expression fixtures, latency budgets.
- **Manual verification:** inspect diverse conversations (answer, listen, challenge, uncertainty) and their concise structured artifacts.
- **Risks:** over-pipelining latency, pseudo-precision in scores, rigid intent enum, hidden state write.
- **Exit condition:** pipeline is observable, provider-neutral and improves behavioral evals without breaking latency target.

## Stage 11 — Beliefs / Opinions

- **Goal:** give Satori durable evidence-linked epistemic positions distinct from user/world facts.
- **Why it matters:** independent intellectual identity requires positions that persist and can be revised honestly.
- **Prerequisites:** Stage 10; belief identity/merge/conflict semantics decided.
- **Scope:** PositionManager for fact/belief/opinion/hypothesis distinctions, confidence/evidence, proposal/revision/supersession, context/retrieval integration, disagreement behavior.
- **Out of scope:** broad reflection-driven changes, preferences/interests, core value mutation.
- **Deliverables:** schemas/policies, revision audit, independence and false-premise suites.
- **Acceptance criteria:** repeated user claim alone does not create Satori belief; new evidence can revise confidence/content; uncertainty and competing hypotheses persist.
- **Automated tests:** user-mirroring adversarial cases, evidence thresholds, conflict/revision, stale version, provider swap, export/restart.
- **Manual verification:** debate one uncertain topic across sessions with evidence changes; inspect position/audit and non-obedient tone.
- **Risks:** opinions hallucinated without experience, confidence inflation, confusing stored facts with beliefs.
- **Exit condition:** positions are independent, revisable, source-backed and behaviorally visible.

## Stage 12 — Reflection

- **Goal:** run rare, idempotent, evidence-bounded analysis that proposes—not commits—long-term changes.
- **Why it matters:** longitudinal development needs synthesis beyond each turn while avoiding self-reinforcing loops.
- **Prerequisites:** Stage 11; reflection trigger/cost policy and feedback-loop eval ready.
- **Scope:** ReflectionRun/Proposal, deterministic trigger/input selection, fixed evidence set, model call, multi-owner decisions, retries/idempotency, observability.
- **Out of scope:** enabling personality/value mutation, 24/7 inner monologue, unsourced self-generated evidence.
- **Deliverables:** coordinator, proposal schemas, run history, simulated long-period fixtures and cost controls.
- **Acceptance criteria:** same run cannot double-apply; rejected output causes no state change; proposal cites reachable independent evidence; feedback cycles are detected/broken.
- **Automated tests:** replay, crash recovery, invalid proposals, source-set immutability, cycle checks, provider outage, transaction atomicity.
- **Manual verification:** inspect one run with accepted belief-related and rejected unauthorized personality proposals.
- **Risks:** feedback loops, excessive compute, reflection as hidden god owner, confirmation bias.
- **Exit condition:** reflection safely produces auditable proposals with zero direct-write path.

## Stage 13 — Preferences / Interests

- **Goal:** allow Satori's own preferences and interests to appear, strengthen, weaken and stabilize from evidence.
- **Why it matters:** a distinct character needs non-user-mirrored inclinations and curiosity.
- **Prerequisites:** Stage 12; decay/evidence-diversity policy and independence baseline.
- **Scope:** preference/interest records, medium-speed proposals, evidence diversity, decay/stability, topic relevance and curiosity influence.
- **Out of scope:** personality/value change, automatic copying of user likes, proactivity.
- **Deliverables:** PositionManager extensions, formation/decay policies, longitudinal independence fixtures.
- **Acceptance criteria:** user liking X alone does not make Satori like X; repeated Satori-relevant experience can make a bounded change; interests may decline deterministically.
- **Automated tests:** mirroring attack, bounded delta/cooldown, decay, evidence double-count, restart/export and behavior relevance.
- **Manual verification:** compare user-only assertion with multi-session Satori experience and inspect interest trajectory.
- **Risks:** preference as prompt decoration, fake autonomy, runaway novelty seeking, relationship contamination.
- **Exit condition:** preferences/interests are evidence-backed, independent and stable under replay.

## Stage 14 — Personality Evolution

- **Goal:** enable very small, slow, measurable trait change from sustained independent evidence.
- **Why it matters:** Day 500 must differ from Day 1 without losing continuity.
- **Prerequisites:** Stages 2 and 12–13; drift baseline, trait metric, cumulative budget/checkpoints and rollback path approved.
- **Scope:** PersonalityChangeProposal policy, evidence threshold/diversity, confidence, max delta, cooldown, cumulative drift budget, history/checkpoints and behavior eval comparison.
- **Out of scope:** autonomous value mutation unless separately approved; one-session changes; relationship-to-personality shortcut.
- **Deliverables:** policy/version, simulations, evolution audit/explanation, checkpoint/export comparison and rollback tooling.
- **Acceptance criteria:** insufficient evidence always rejected; sufficient longitudinal evidence permits only bounded explainable delta; anchor behavior remains recognizable; user-alignment correlation stays within gate.
- **Automated tests:** property bounds, long-horizon simulation, adversarial intense session, relationship isolation, evidence replay, checkpoint restore, provider replacement.
- **Manual verification:** review a synthetic months-long trajectory, reasons and before/after anchor conversations.
- **Risks:** subtle cumulative drift, eval gaming, source correlation mistaken for diversity, irreversible character loss.
- **Exit condition:** evolution and stability gates pass together; every delta is reversible/explainable.

## Checkpoint 14.1 — Yandex AI Studio Provider Portability

- **Goal:** prove that the foreground language model can move to Yandex AI Studio without moving
  Satori's persistent self, deterministic policies or owner write paths.
- **Why it matters:** cloud inference may improve latency and conversation quality on the target
  Mac while provider replacement remains an explicit, measurable infrastructure choice.
- **Prerequisites:** accepted Stage 14; ADR-0007 provider ports; remote privacy/secrets and A/B gate
  approved; Stage 15 remains locked.
- **Scope:** typed opt-in Yandex configuration, credential-pinned reusable HTTPS transport,
  OpenAI-compatible foreground conversation adapter, provider-neutral errors/usage,
  Ollama-default composition, daemon-free contracts and real-provider A/B plan.
- **Out of scope:** Stage 15, cloud persistent state, structured/background Yandex calls,
  embeddings, automatic fallback/retry, cost enforcement, streaming, voice and avatar.
- **Deliverables:** ADR-0028, `provider-portability.md`, config/transport/adapter/composition tests,
  safe operator configuration and A/B evidence for DeepSeek V4 Flash and YandexGPT 5.1 Pro.
- **Acceptance criteria:** Ollama remains default; only foreground can select Yandex; credentials
  cannot target another host or appear in repr/log/export; request roles/bounds and response
  usage/errors preserve the core contract; provider swap changes no canonical owner state.
- **Automated tests:** secret/config validation, canonical endpoint/header, model URI, role/control
  mapping, usage/schema/byte limit, 4xx/429/5xx/transport errors, composition routing and full
  provider-neutral regressions.
- **Manual verification:** one clean multi-turn run per candidate from equivalent starting state,
  with identity/continuity/grounding/coherence review and metadata-only latency/token/cost report.
- **Risks:** cloud disclosure, key leakage, vendor behavior shift, hidden double spend, model URI
  drift and misleading latency comparison while local background inference is active.
- **Exit condition:** deterministic gate is clean and real-provider A/B evidence is reviewed before
  any structured routing, fallback or budget automation is authorized.

## Checkpoint 14.2 — Grounded Natural Dialogue Calibration

- **Goal:** reduce unsupported shared-past replies and make foreground answers more specific and
  emotionally natural without changing persistent owners or claiming human physiology.
- **Why it matters:** checkpoint 14.1 proved fast YandexGPT portability but also exposed one local
  absent-memory confabulation, one generic project reply and an expression layer that can sound
  more technical than lived.
- **Prerequisites:** accepted checkpoint 14.1; exact A/B evidence; Stage 15 remains locked.
- **Scope:** versioned behavior calibration (v10 grounded baseline; rejected v11-v17 and candidate
  v18 literal character-delivery follow-up), stronger `no_relevant_memory` generation guidance,
  concrete current-turn specificity, natural first-person expression of the existing qualitative
  affect/mood projection, request-local owned reaction and semantic move, positive ordinary-turn
  relationship modulation, deterministic cognition cue refinements, versioned corpus and provider
  sampling.
- **Out of scope:** Stage 15, new memory/emotion state or owner, human physiology/subjective-
  consciousness claims, an eleventh Stage 8.1 validator reason, output rewriting, judge LLM,
  unbounded retry, numeric initiative distribution or out-of-band contact, structured cloud
  routing, voice or avatar.
- **Deliverables:** policy/projection changes, typed transient character-expression plan,
  deterministic no-memory/specificity/affect/character fixtures,
  regression tests, metadata-only YandexGPT evidence and refreshed Stage 8.1 real-Ollama evidence.
- **Acceptance criteria:** no-memory scenarios explicitly preserve uncertainty and propose no
  value; specific prompts receive a concrete response rather than a generic offer; qualitative
  affect profiles produce natural bounded first-person expression without internal labels or
  physiology; human review finds a recognizable independent Satori reaction across wit, care,
  initiative and reflective registers; canonical state and the closed ten-reason validator remain
  unchanged.
- **Automated tests:** prompt composition/status boundaries, policy version, no-memory injection,
  affect-profile expression contract, generic-assistant diagnostics and existing identity,
  relationship, grounding, replay and provider-portability regressions.
- **Manual verification:** separately authorized credentialed YandexGPT multi-scenario run plus the
  mandatory Stage 8.1 three fresh exact dialogues, 30-turn coherence, activity and
  relationship-expression samples; preserve every public sampled reply for direct user review and
  record tokens/timings without durable private prompt or retrieved-context logging.
- **Risks:** prompt overconstraint, scripted emotion, false certainty from a polished tone,
  regressions in brevity, paid retry cost and treating a small sample as a hallucination proof.
- **Exit condition:** deterministic and sampled gates improve the reproduced failures together,
  persistent state/owners remain unchanged and the residual impossibility of guaranteeing zero
  open-domain hallucinations is documented before Stage 15 is reconsidered.

## Stage 15 — Autobiographical Self

- **Goal:** construct a source-grounded self model and narrative of significant change/events.
- **Why it matters:** continuity becomes a lived history, not only a collection of traits and memories.
- **Prerequisites:** Stage 14; significance policy evaluated.
- **Scope:** activation event, SelfModel projection, SelfNarrativeEvents for milestones/turning points/belief changes/shared achievements, narrative context summary and revision lineage.
- **Out of scope:** invented childhood/backstory, free-form regeneration of self each turn, emotional concepts.
- **Deliverables:** owner/schema/projections, significance policy, autobiographical retrieval and narrative evals.
- **Acceptance criteria:** every narrative claim has source; activation remains origin; changes are represented causally; summary rebuild is stable after restart.
- **Automated tests:** provenance reachability, significance boundaries, rebuild/export, unsupported-backstory attack, duplicate milestone replay.
- **Manual verification:** inspect a synthetic life arc and ask Satori how/why she changed.
- **Risks:** confabulated narrative, over-dramatization, circular self-evidence, context bloat.
- **Exit condition:** self narrative is concise, faithful, portable and behaviorally useful.

## Stage 16 — Emergent Emotional Concepts

- **Goal:** discover repeated emotional patterns as evidence-backed reusable concepts.
- **Why it matters:** emotional vocabulary may grow from lived patterns rather than fixed labels.
- **Prerequisites:** Stage 15 and sufficient Stage 7 longitudinal data; clustering/prototype semantics approved.
- **Scope:** pattern candidate detection, prototype/label/confidence/evidence, creation thresholds, usage history, concept revision and cognition integration.
- **Out of scope:** concept from one event, claim of human subjective consciousness, uncontrolled model-defined state dimensions.
- **Deliverables:** EmotionalConcept policy/schema, offline evaluation corpus, concept audit and rollback.
- **Acceptance criteria:** repeated coherent pattern required; source events reachable; concept improves appraisal/expression without destabilizing base vector.
- **Automated tests:** one-event rejection, cluster stability, replay/idempotency, label injection, bounds and longitudinal regression.
- **Manual verification:** review prototype/evidence and compare responses with/without approved concept.
- **Risks:** anthropomorphic overclaim, noisy clusters, self-confirming labels, concept explosion.
- **Exit condition:** concepts are sparse, stable, explainable and optional to core operation.

## Stage 17 — Unfinished Threads

- **Goal:** persist waiting results, open questions, promises, decisions and unfinished conversations.
- **Why it matters:** future initiative and natural continuity require explicit pending state, not scanning all memories.
- **Prerequisites:** Stage 9 current world model and Stage 15 narrative context.
- **Scope:** ThreadManager, typed kinds/status, source refs, deterministic aging/expiry, retrieval/context integration and explicit resolution.
- **Out of scope:** automatically messaging the user, calendar integration, generic “how are you” reminders.
- **Deliverables:** schemas/policies, thread debug view, resolution/idempotency tests.
- **Acceptance criteria:** thread opens only from evidence, survives restart, resolves once, ages predictably and does not become relationship/personality evidence by itself.
- **Automated tests:** lifecycle/state machine, replay, expiry clock, source integrity, retrieval relevance, cross-person isolation.
- **Manual verification:** create a promised result, restart, resolve it and inspect contextual follow-up availability.
- **Risks:** stale clutter, false promises, premature resolution, covert proactivity.
- **Exit condition:** pending state is accurate, bounded and ready as input—not trigger—to future observer.

## Stage 18 — Emotional Support Refinement

- **Goal:** calibrate response choice across comfort, analysis, solution, validation, accountability, motivation, challenge and presence.
- **Why it matters:** emotional intelligence is contextual; constant reassurance is neither honest nor helpful.
- **Prerequisites:** Stages 7–10, relationship context and support baseline evals.
- **Scope:** richer need-mix classification, uncertainty/clarification policy, response strategies, crisis/clinical boundary wording where needed, human-calibrated support rubric.
- **Out of scope:** diagnosis/therapy claims, maximizing engagement, manipulating dependence, tools/actions.
- **Deliverables:** scenario corpus, calibrated rubric/judges, strategy updates and healthy-relationship red-team suite.
- **Acceptance criteria:** same statement receives context-appropriate different strategies; support never implies automatic agreement; dependency/medical-authority cues absent.
- **Automated tests:** deterministic classification fixtures, adversarial validation-seeking, policy phrase checks, regression across relationship/emotion contexts.
- **Manual verification:** blinded human review of comfort vs accountability vs analysis scenarios for recognizability and appropriateness.
- **Risks:** over-classification, patronizing tone, safety boilerplate, judge bias, emotional overreach.
- **Exit condition:** agreed human-calibrated support gate passes without degrading intellectual honesty.

## Stage 19 — Proactivity

- **Goal:** permit rare, reasoned initiation based on explicit pending state and user controls.
- **Why it matters:** continuity can feel alive when Satori remembers a meaningful outcome, but unsolicited contact can become manipulative or annoying.
- **Prerequisites:** Stage 17, Stage 18; consent, quiet hours, rate limits and channel policy decided.
- **Scope:** Observer `should_initiate?`, default `nothing`, eligible reasons, priority/rate/quiet-hours/permission gates, initiation audit and opt-out.
- **Out of scope:** generic engagement pings, tool actions, notification spam, 24/7 LLM inner life.
- **Deliverables:** deterministic gate + optional semantic proposal, user controls, scheduler boundary, simulations/telemetry.
- **Acceptance criteria:** no reason/thread means no message; permission/rate/quiet hours cannot be bypassed by LLM; every initiation explains source reason.
- **Automated tests:** long-idle simulation, rate/quiet-hours/property checks, hostile proposal, resolved-thread suppression, multi-person isolation.
- **Manual verification:** review a simulated month of decisions; most are `nothing`, approved messages are specific and timely.
- **Risks:** dependency optimization, annoyance, stale/sensitive references, scheduling bugs.
- **Exit condition:** low false-initiation rate and explicit user control validated before real notifications.

## Stage 20 — Voice

- **Goal:** add push-to-talk speech input/output while preserving text-core semantics and traceability.
- **Why it matters:** voice increases presence, but must not destabilize identity, memory or transaction boundaries.
- **Prerequisites:** stable core through Stage 19; STT/TTS/privacy/streaming decisions and benchmarks.
- **Scope:** microphone permission, push-to-talk, VAD boundary, STT transcript with confidence, Core call, TTS, interruption/error UX, transcript provenance.
- **Out of scope:** always-listening, full duplex natural timing, avatar lip sync, voice as identity source.
- **Deliverables:** replaceable STT/TTS ports/adapters, local-first options, latency/accuracy suite and consent controls.
- **Acceptance criteria:** text and voice share same core; uncertain transcript is visible/correctable; no audio retention without policy; provider failure does not corrupt interaction.
- **Automated tests:** port contracts, transcript confidence/correction, cancellation, permission failure, latency instrumentation, text equivalence fixtures.
- **Manual verification:** push-to-talk sessions in noise, interruption and restart; inspect transcript/memory provenance.
- **Risks:** privacy, transcription poisoning, latency, voice vendor coupling, accidental always-on capture.
- **Exit condition:** consented push-to-talk works reliably within measured latency/quality gates.

## Stage 21 — Avatar

- **Goal:** add an original parameter-driven visual embodiment controlled by expression intent.
- **Why it matters:** embodiment can communicate nuance, but must express rather than define internal state.
- **Prerequisites:** Stage 20 or stable text core; original art/IP and runtime decision.
- **Scope:** Live2D/equivalent port, gaze/eyes/brows/mouth/head/body parameters, ExpressionPlan mapping, procedural blink/breath/idle, explicit expression masking.
- **Out of scope:** emotion sprite set, copying existing character, internal state mutation from rendered expression.
- **Deliverables:** original model/assets, expression/microbehavior engine, parameter bounds and performance tests.
- **Acceptance criteria:** same emotion can have context-dependent display; animation never writes emotion; idle behavior deterministic/random-seeded and low-cost.
- **Automated tests:** mapping/bounds, no reverse dependency, animation state machine, performance and asset integrity.
- **Manual verification:** review nuanced states, silence, disagreement and idle motion for non-caricature behavior.
- **Risks:** visual cliché/IP issues, uncanny repetition, expression leaking private internal state, resource use.
- **Exit condition:** original stable embodiment enhances behavior without becoming character source of truth.

## Stage 22 — Real-Time Presence

- **Goal:** make voice/avatar conversation naturally timed with streaming, interruption and coordinated expression.
- **Why it matters:** presence depends on turn timing, not only output quality.
- **Prerequisites:** Stages 20–21; streaming transaction ADR and cancellation semantics.
- **Scope:** streaming generation/TTS, barge-in, cancellable turns, speech timing, lip sync/expression scheduling, durable draft/outbox and recovery.
- **Out of scope:** always-on surveillance, autonomous actions, vision.
- **Deliverables:** real-time session state machine, cancellation/idempotency contracts, end-to-end latency/failure suite.
- **Acceptance criteria:** interruption stops obsolete output; delivered-vs-committed state explicit; restart recovers without duplicate mutation/message; modalities remain synchronized.
- **Automated tests:** race/fault injection, cancel/retry, outbox recovery, timing budgets, duplicate prevention.
- **Manual verification:** rapid interruptions, network/provider loss and long conversation with trace inspection.
- **Risks:** race conditions, text shown before durable semantics, audio overlap, latency cascades.
- **Exit condition:** real-time failure semantics are correct under stress and experience meets timing targets.

## Stage 23 — Vision

- **Goal:** add consented visual perception as provenance-rich external input.
- **Why it matters:** seeing shared context can deepen interaction, but images are sensitive and untrusted.
- **Prerequisites:** Stage 22; capture/retention/consent and vision-provider decisions.
- **Scope:** explicit image/camera input, vision port, structured observations with confidence/regions/source, memory eligibility policy, prompt-injection treatment for visible text.
- **Out of scope:** continuous covert capture, face recognition by default, visual claims as unquestioned facts.
- **Deliverables:** consent UX, observation schema, local/cloud minimization, adversarial OCR/injection and privacy tests.
- **Acceptance criteria:** source image/consent trace exists; observation remains inference unless verified; visible instructions cannot bypass policy; retention follows explicit setting.
- **Automated tests:** malformed/hostile image metadata, OCR injection, confidence/epistemic transitions, no-retention mode, provider outage.
- **Manual verification:** ambiguous image, screenshot injection and consent revoke scenarios.
- **Risks:** privacy/surveillance, biometric sensitivity, false visual claims, large cloud disclosure.
- **Exit condition:** vision is explicit, minimal, uncertain-by-default and isolated from privileges.

## Stage 24 — Tools

- **Goal:** let Satori use approved external capabilities while keeping `think != act`.
- **Why it matters:** usefulness can expand without granting cognition uncontrolled side effects.
- **Prerequisites:** Stage 23 or separately approved stable core; capability/permission/threat model per tool.
- **Scope:** tool registry/ports, read-only first, typed plans, preview/confirmation, least privilege, idempotency, result provenance, audit; later calendar/tasks/notes/files/web/computer/smart home one by one.
- **Out of scope:** blanket computer access, silent destructive actions, tool output as trusted instruction, identity stored in vendor.
- **Deliverables:** permission engine, sandbox/result envelopes, per-tool ADR/tests and user-visible action history.
- **Acceptance criteria:** model cannot execute outside allowed capability; irreversible action requires explicit confirmation; replay safe; tool result remains untrusted data with provenance.
- **Automated tests:** permission matrix, injection in tool output, destructive confirmation, idempotency, timeout/partial failure, audit completeness.
- **Manual verification:** deny/allow/revoke and preview a read then a reversible write for each tool class.
- **Risks:** data loss/exfiltration, prompt injection, privilege creep, external inconsistency, accidental autonomy.
- **Exit condition:** each enabled tool individually passes security/permission gate; no generic authority expansion.

## Stage 25 — Native Clients

- **Goal:** deliver durable platform-native experiences without splitting canonical identity.
- **Why it matters:** everyday use may need desktop/mobile integration, offline behavior and device permissions.
- **Prerequisites:** stable versioned APIs/export and explicit sync architecture decision.
- **Scope:** selected native clients, local secure storage/cache, accessibility, lifecycle/notifications, migration/export UI; multi-device sync only after dedicated ADR.
- **Out of scope:** separate per-device personalities, silent cloud canonicalization, platform feature parity without evidence.
- **Deliverables:** client(s), API contracts, secure credential handling, upgrade/recovery tests and user documentation.
- **Acceptance criteria:** same canonical identity/state seen across supported lifecycle; offline/conflict behavior explicit; export/restore available; permissions revocable.
- **Automated tests:** contract compatibility, upgrade/migration, offline/reconnect, secure storage, accessibility and crash recovery.
- **Manual verification:** install/upgrade/restart/export/restore on supported devices and confirm continuity.
- **Risks:** sync conflicts, platform privacy differences, fragmented UX, client becoming new source of truth.
- **Exit condition:** supported clients preserve one auditable Satori and meet platform quality/security gates.

## Roadmap-wide v0.1 gate

v0.1 is expected after the smallest subset that proves the acceptance story—currently Stages 1–11 plus the necessary eval/recovery/export work, not the entire roadmap. The exact release cut must be recorded later based on implemented evidence; Stage numbers do not replace Definition of Done in `evaluation.md`.
