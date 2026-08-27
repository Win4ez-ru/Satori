# Architecture Decision Records

Accepted ADRs are immutable history. To change a decision, add a new ADR with `Supersedes: ADR-XXXX`; do not rewrite the old rationale. Status values: Proposed, Accepted, Superseded, Rejected.

| ADR | Status | Decision |
|---|---|---|
| [0001](0001-modular-monolith.md) | Accepted | Modular monolith with dependency rule |
| [0002](0002-persistent-self-outside-llm.md) | Accepted | Persistent self lives outside LLM/prompts |
| [0003](0003-single-state-owner.md) | Accepted | One writer-owner per state family |
| [0004](0004-proposal-based-mutations.md) | Accepted | Proposal → policy → atomic audit mutation |
| [0005](0005-layered-memory-with-provenance.md) | Accepted | Layered memory distinct from raw chat |
| [0006](0006-structured-cognition-no-raw-cot.md) | Accepted | Structured cognition without raw chain-of-thought |
| [0007](0007-capability-provider-ports.md) | Accepted | Capability-based, vendor-neutral provider ports |
| [0008](0008-local-first-transactional-state.md) | Accepted | Local-first canonical transactional state |
| [0009](0009-stage-1-toolchain-and-layout.md) | Accepted | Stage 1 Python toolchain, portability and package layout |
| [0010](0010-explicit-activation-and-initial-self.md) | Accepted | Explicit activation, versioned seed and normalized initial self |
| [0011](0011-stage-3-conversation-context-and-provider.md) | Accepted | Stateless conversation context, trusted roles and local Ollama baseline |
| [0012](0012-conversation-history-and-episodic-formation.md) | Accepted | Durable raw history, derived episodic formation and Stage 4 retention |
| [0013](0013-episodic-retrieval-and-grounded-context.md) | Accepted | Exact derived episodic retrieval, semantic-first rank and untrusted memory context |
| [0014](0014-semantic-memory-evidence-and-consolidation.md) | Accepted | Evidence-grounded semantic claims, deterministic confidence and correction history |
| [0015](0015-affective-state-appraisal-decay-and-mood.md) | Accepted | Bounded affect, structured appraisal, lazy decay and slower mood |
| [0016](0016-interactive-runtime-context-and-delivery.md) | Accepted | Long-lived chat, bounded recent context, canonical delivery and retryable post-response work |
| [0017](0017-runtime-self-model-and-character-expression.md) | Superseded | Derived runtime self-model, character expression and provider/identity distinction |
| [0018](0018-contextual-self-expression-and-disclosure.md) | Superseded | Contextual self projection, natural disclosure and semantic behavior evaluation |
| [0019](0019-local-inference-priority-and-categorical-appraisal-wire.md) | Accepted | Foreground inference priority and compact categorical appraisal transport |
| [0020](0020-persistent-counterparty-relationship-model.md) | Accepted | Counterparty-specific bounded relationship state and post-response lifecycle |
| [0021](0021-dialogue-coherence-and-compositional-disclosure.md) | Accepted | Transient dialogue coherence, compositional disclosure and max-one typed response regeneration |
| [0022](0022-evidence-typed-user-and-world-models.md) | Accepted | Evidence-typed counterparty user/world models with deterministic validity and expiry |
| [0023](0023-transient-structured-cognition-pipeline.md) | Accepted | Transient typed cognition pipeline with explicit fallback, strategy invariants and no extra foreground model call |
| [0024](0024-evidence-linked-satori-positions.md) | Accepted | Identity-global evidence-linked Satori positions with anti-mirroring and explicit revision |
| [0025](0025-bounded-reflection-runs-and-owner-routing.md) | Accepted | Rare fixed-source reflection with deterministic cost bounds and per-proposal owner routing |
| [0026](0026-evidence-backed-satori-inclinations.md) | Accepted | Evidence-backed identity-global preferences/interests with bounded change, decay and context influence |
| [0027](0027-bounded-personality-evolution-and-checkpoint-restore.md) | Accepted | Multi-month bounded trait evolution with cumulative drift budgets, checkpoints and append-only restore |
| [0028](0028-yandex-ai-studio-foreground-provider.md) | Superseded in part | Optional credential-pinned Yandex AI Studio foreground provider with local owner boundaries |
| [0029](0029-transient-character-expression-plan.md) | Superseded in part | Typed request-local selection of Satori's original character expression |
| [0030](0030-relationship-modulated-character-expression.md) | Superseded in part | V2 semantic and positive relationship modulation inside transient character expression |
| [0031](0031-openai-foreground-provider.md) | Accepted | Optional credential-pinned OpenAI Responses foreground provider with unchanged local owners |
| [0032](0032-openai-visible-and-reasoning-output-budgets.md) | Accepted | Separate application-visible and OpenAI reasoning output budgets with fail-closed enforcement |
| [0033](0033-late-compact-character-delivery.md) | Superseded in part | Late compact realization of the typed character-expression plan without enum labels or scripts |
| [0034](0034-relevance-scoped-memory-and-literal-delivery.md) | Superseded in part | Relevance-scoped no-recall wording and shorter literal realization of the same typed character plan |
| [0035](0035-single-late-character-realization.md) | Superseded in part | Single late realization of the complete typed character plan with grounded practical initiative |
| [0036](0036-owned-contribution-and-motivational-posture.md) | Accepted | Separate factual anchor, owned contribution and bounded motivational posture in request-local plan v3 |

Open implementation choices are tracked in `../open-questions.md`; do not create a fictional ADR where evidence is not yet available.
