- [ADR-0111: Actual Desktop OpenAI runtime](0111-explicit-desktop-openai-runtime.md) — explicit profile-bound runtime, existing all-role factory and budget owner, fixed reviewed prices and bounded memory adoption; no paid startup inference.
- [ADR-0110: Contextual source answers in chat](0110-conversational-cross-source-answers.md) — explicit conversation task, same-Core context/character, bounded mail/Telegram reads and source disclosure; calls unavailable.

# Architecture Decision Records

Accepted ADRs are immutable history. To change a decision, add a new ADR with
`Supersedes: ADR-XXXX`; do not rewrite the old rationale. Status values: Proposed, Accepted,
Superseded in part, Superseded, Rejected. A partial supersession identifies that only the scope
named by the newer ADR was replaced. The historical rationale remains unchanged; its status
marker may link the successor without rewriting the accepted decision's body.

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
| [0012](0012-conversation-history-and-episodic-formation.md) | Superseded in part | Durable raw history, derived episodic formation and Stage 4 retention |
| [0013](0013-episodic-retrieval-and-grounded-context.md) | Superseded in part | Exact derived episodic retrieval/rank retained; ADR-0055 adds canonical scope and full-lineage eligibility |
| [0014](0014-semantic-memory-evidence-and-consolidation.md) | Superseded in part | Evidence/confidence/consolidation retained; ADR-0055 partitions semantic identity and source admission |
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
| [0036](0036-owned-contribution-and-motivational-posture.md) | Superseded in part | Separate factual anchor, owned contribution and bounded motivational posture in request-local plan v3 |
| [0037](0037-non-echoing-flow-and-guarded-expression.md) | Superseded in part | Non-echoing acknowledgement, natural closure and request-local guarded expression in plan v4 |
| [0038](0038-response-act-and-evidence-envelope.md) | Superseded in part | One derived response act plus an explicit request-local evidence envelope without factual recap |
| [0039](0039-practical-care-and-lean-provider-projection.md) | Superseded in part | Practical care for ordinary depletion and one lean action/evidence/voice/stop provider projection |
| [0040](0040-direct-character-delivery-decision.md) | Superseded in part | Direct v24 character-delivery decision with one cohesive baseline and one late provider director |
| [0041](0041-v25-social-disclosure-and-failure-observability.md) | Superseded in part | Historical v25 social/self-disclosure delivery; ADR-0051 extends V32 mixed style/self scope; accepted privacy-safe provider failure reasons and explicit relationship recovery |
| [0042](0042-unified-causal-character-presence.md) | Superseded in part | Historical v26 causal presence and accepted owner bridge; its selection/rendering was replaced by ADR-0043 |
| [0043](0043-live-state-selected-character-movement.md) | Superseded in part | Historical V27 live-state-selected operational movement remains immutable; active selection/rendering is replaced by ADR-0045 |
| [0044](0044-v27-output-evidence-and-attempt-isolation.md) | Accepted | Conservative OpenAI output evidence, exact post-response cost accounting and isolated V27 attempts |
| [0045](0045-character-agency-kernel.md) | Superseded in part | Request-local Character Agency Kernel with unchanged persistent owners and historical V27 isolation |
| [0046](0046-north-star-agent-boundaries-and-roadmap.md) | Superseded in part | North Star source/world/permission/action boundaries retained; ADR-0053 replaces only the 14.3-before-recovery scheduling gate |
| [0047](0047-familiar-peer-character-delivery.md) | Superseded in part | V29 familiar expression and truthful state; ADR-0048 clarifies selective casual coverage, ADR-0049 versions realization as V30 |
| [0048](0048-selective-casual-response-coverage.md) | Accepted | Ordinary conversation may answer one of several questions; recognized topics constrain truth without requiring exhaustive casual coverage |
| [0049](0049-understated-contextual-expression.md) | Superseded in part | V30 realizes one contextual reply, preserves understated closure and explicit task completeness; historical V29 and typed owners unchanged |
| [0050](0050-selected-conversational-initiative.md) | Superseded in part | Historical V31 grounded conversational opening remains preserved; ADR-0051 replaces active expression as V32 |
| [0051](0051-compact-owned-reaction-and-closure-bounds.md) | Superseded in part | Historical V32 compact expression; ADR-0052 replaces active realization as V33 while retaining typed direct-stop bounds and mixed-request fixes |
| [0052](0052-owned-news-and-care-realization.md) | Superseded in part | Historical V33 realization; ADR-0054 preserves its news/care and correctness boundaries while replacing active affect disclosure |
| [0053](0053-parallel-foundations-and-whole-state-recovery.md) | Accepted | Parallel Foundations 14.4.1, pure consistent stored snapshot, private archives and digest-bound offline new-target recovery; Character acceptance unchanged |
| [0054](0054-bounded-mixed-affect-expression.md) | Superseded in part | V34 mixed-affect expression retained; ADR-0058 changes active V35 discretionary selection before reaction freeze |
| [0055](0055-canonical-memory-scope-and-legacy-quarantine.md) | Superseded in part | Production Foundations 14.4.2a: canonical scope, full-lineage admission, independent semantic namespace and preserved legacy quarantine |
| [0056](0056-owner-controlled-episodic-archival.md) | Accepted | MemoryManager archive/reactivate, preview-bound CAS/replay and retained semantic proof; source exclusion/erasure remain separate |
| [0057](0057-canonical-source-exclusion-and-trusted-policy-floor.md) | Accepted | Source exclusion with independent trusted policy floor, complete input dependencies and owner withdrawal implemented; Foundation/macOS gates passed, Linux process gate open |
| [0058](0058-mixed-affect-before-reaction-freeze.md) | Accepted | Candidate V35 pure mixed-affect readiness bounds discretionary initiative/voice before generation; owners, appraisal, schemas and protected cognition remain unchanged |
| [0059](0059-read-only-erasure-preview-and-owner-routing.md) | Accepted | Read-only canonical erasure preview and owner routing; exact local inventory/fresh check, explicit physical blockers and future floor-first tombstone policy; implemented, macOS engineering gate passed |
| [0060](0060-durable-erasure-intent-and-owner-preparation.md) | Accepted | Implemented 14.4.2d: terminal independent-floor intent, composite recovery fence and resumable owner preparation; policy_0002/main 0017, macOS engineering gate passed, physical execution denied |
| [0061](0061-terminal-episodic-bodies-and-bounded-handling.md) | Accepted | 14.4.2e: MemoryManager terminal NULL bodies, RetrievalIndex removal, fresh exact action and distinct trusted body-head fence; policy_0003/main 0018; full-root erasure remains open |
| [0062](0062-prior-conversational-cue-before-reaction-freeze.md) | Accepted design; behavior unaccepted | Opt-in V36 structural prior-question cue before reaction freeze; agency 2 / manifest 18; no semantic uptake or new persistent owner |
| [0063](0063-terminal-dialogue-bodies-and-historical-provenance.md) | Accepted | 14.4.2f: InteractionLog terminal canonical/dependent assistant bodies, historical witnesses and one mixed body head; policy_0004/main 0019; partial logical coverage |
| [0064](0064-previous-exchange-context-for-affective-appraisal.md) | Accepted design; verification in progress | Opt-in one-pair appraisal interpretation context; input 2 / proposal 1; existing source admission and owners |
| [0065](0065-context-use-in-generic-owned-replies.md) | Accepted design; behavior unaccepted | Opt-in V37 distinguishes shared premises from own contribution in generic replies; useful summaries and complete tasks retained |
| [0066](0066-terminal-derived-knowledge-and-evidence-continuity.md) | Accepted; implementation in progress | 14.4.2g: existing-owner terminal semantic/model/position/inclination bodies, append-only evidence continuity and one mixed recovery head; old hashes and live learning boundaries retained |

| [0067](0067-bounded-current-position-before-expression.md) | Accepted design; behavior unaccepted | Default-off bounded semantic current position after frozen agency/cognition, before expression; one extra eligible-turn call, no new persistent owner |
| [0068](0068-terminal-reflection-payloads-and-retained-continuity.md) | Accepted; first vertical macOS engineering gate complete | 14.4.2h: terminal Reflection payloads through the existing owner and mixed body head, frozen retained metadata, availability barriers and historical continuity |
| [0069](0069-prior-dialogue-relation-before-agency.md) | Accepted experimental design; behavior open | Isolated default-off pre-agency relation interpretation, exact latest-slot history, bounded ordinary lead modulation without persistent state |
| [0070](0070-appraisal-explanation-bodies-and-exact-input-provenance.md) | Accepted; macOS engineering gate passed | 14.4.2h: exact prospective appraisal input/audit links, paired explanation disposition, numerical history and mixed-head admission |

| [0071](0071-owner-bound-retained-audit-metadata.md) | Accepted; macOS engineering gate passed | 14.4.2h prospective owner-bound audit metadata, exact schema/receipt partition and safe read-only coverage; no legacy backfill or physical execution |

| [0072](0072-identity-scoped-checkpoint-reason-disposition.md) | Accepted; macOS engineering gate passed | 14.4.2h exact identity-owned approval/restore reason disposition in the same mixed head; preserved numerical budget/history and no inferred USER attribution |

| [0073](0073-contextual-repair-targeting-before-agency.md) | Accepted experimental design; behavioral gate open | Isolated default-off V35 repair targeting; one effective current cue in evidence/intake before agency, separate from emotional and relationship ownership |

| [0074](0074-whole-store-retention-and-readiness.md) | Accepted; isolated macOS engineering gate passed | 14.4.2i disjoint whole-main row coverage, exact six-action terminal proofs, closed owner metadata and separate read-only readiness; historical preparation-v1 unchanged |
| [0075](0075-repair-frame-and-addressee-before-admission.md) | Accepted experimental design; scoped verification complete, behavioral gate open | Same default-off repair operation; v2 focal frame/addressee and joint cue admission, with valid material uncertainty withheld; inherited/native failures and independent state owners retained |
| [0076](0076-conversational-agreement-before-repair-targeting.md) | Accepted experimental protocol; target sample 19/21, complete semantic/Character gates open | Same v2 operation interprets preceding activity/current uptake before targeting; no new state/call/owner. Both draft regressions pass but received-message detail/source-basis failures remain; one-shot grant consumed; ADR-0077 further clarifies represented acts |
| [0077](0077-represented-repair-within-shared-material.md) | Accepted narrow experimental protocol; scoped offline engineering verified, target semantics/Character open | Same default-off v2 operation retains interpersonal repair inside shared material and relative recipient; supplied Satori recipient stays non-live. 333 focused and 24 managed cells; full 5986 pass/13 inherited fail/5 skip. No DTO/state/owner/call change or API run |

| [0078](0078-character-closeout-api-evaluation.md) | Accepted bounded evaluation; one paid invocation consumed, Character acceptance open | Evaluation-only typed API ports; 20 independent scenarios completed, 108/132 total turns before missing-memory prerequisite stop, USD 0.857834320 usage; remaining contrasts NOT_RUN, no repeat/refill, production providers and state owners unchanged |

Open implementation choices are tracked in `../open-questions.md`; do not create a fictional ADR where evidence is not yet available.

- [ADR-0079: Desktop-first companion delivery](0079-desktop-first-companion-delivery.md) — accepted delivery scheduling and D1 adapter boundary; Character paused, privacy/action gates preserved.
- [ADR-0080: Local push-to-talk and committed reply speech](0080-local-push-to-talk-and-committed-speech.md) — accepted bounded D2.1; local Small CPU int8, editable draft/separate Send and opt-in committed-only Milena speech. Engineering verification is in progress; physical/human/public release gates remain open, full D2/Stage 20/15 and D3 remain separate.
- [ADR-0081: Voice quality and expression delivery](0081-local-neural-voice-and-expression-delivery.md) — GigaAM, deterministic speech text/expression projection and explicit session-key cloud speech; human acoustic acceptance remains separate.
- [ADR-0082: Session calendar read and assistant foundation](0082-session-calendar-read-and-assistant-foundation.md) — accepted bounded D3.1/D4.1 local calendar view, selected-resource grants and no source ingestion into the Core.
- [ADR-0083: Transient calendar cards in the conversation view](0083-transient-calendar-dialogue-cards.md) — accepted bounded local-display permit and deterministic ephemeral Calendar card; implemented, bounded engineering delivery complete with installed UI PASS_SCOPED, no canonical Send/provider/persistence/TTS, real Calendar acceptance separate.
- [ADR-0084: Cloud speech admission and explicit public emotion auditions](0084-cloud-speech-admission-and-public-auditions.md) — accepted pre-network outgoing-text guard and four manual authored public audition cases; implemented, bounded engineering delivery complete with installed UI PASS_SCOPED, canonical reply unchanged, voice/acoustic acceptance open.
- [ADR-0085: Offline selected-mail policy and projection foundation](0085-offline-selected-mail-foundation.md) — offline foundation; superseded in part by [ADR-0087](0087-bounded-mail-alternative-selection-and-diagnostics.md) only for MIME-alternative decoding/projection version; historical implementation evidence retained.
- [ADR-0086: Foreground Gmail connection and selected reading](0086-foreground-gmail-connection-and-selected-reading.md) — bundle 6 installed, Connect/list20 scoped PASS, original selected previews rejected; superseded in part by [ADR-0087](0087-bounded-mail-alternative-selection-and-diagnostics.md) only for MIME alternatives/projection version; session-only/source-isolation bounds retained.
- [ADR-0087: Bounded mail alternative selection and closed diagnostics](0087-bounded-mail-alternative-selection-and-diagnostics.md) — superseded in part by [ADR-0088](0088-mail-header-horizontal-whitespace.md) only for header-value horizontal whitespace/projection version; bounded alternative selection, partial omissions and closed reasons retained.
- [ADR-0088: Mail header horizontal whitespace](0088-mail-header-horizontal-whitespace.md) — accepted literal HTAB-only header-value/typed-claim compatibility, lossless v3 projection with all other strict bounds retained; source implementation/575 focused checks and private account flow PASS_SCOPED, final Foundation/build/full-gate/install pending.
- [ADR-0090: Reviewed schedule import and iPhone calendar client](0090-reviewed-schedule-import-and-iphone-calendar-client.md) — accepted bounded local photograph/OCR review, exact-calendar create-only action, reconciliation and native iPhone calendar client; implementation and real device/synchronization acceptance remain separate.
- [ADR-0089: Format controls in deterministically discarded HTML data](0089-discarded-html-format-controls.md) — accepted bounded v4 rule: six literal format characters only in already locally discarded data, explicit entity asymmetry, partial typed omission; installed 7 unchanged, master engineering/live acceptance pending.
- [ADR-0091: Reviewed adoption after device-only installation-binding drift](0091-reviewed-device-binding-recovery.md) — accepted explicit owner-reviewed generation-zero recovery; ordinary full-pair validation unchanged. The owner-approved actual adoption preserves canonical/floor/lock bytes and restores normal Core startup; original-volume provenance remains unproved.

- [ADR-0092: Unified personal-source desktop workspace](0092-unified-personal-sources-desktop.md) — accepted bounded common workspace and owned packaged Telegram adapter; runtime qualification and account acceptance remain separate. Calendar/Gmail grants and Core isolation remain unchanged; foreground context/maps are prepared separately.
- [ADR-0096](0096-daily-assistant-actions-and-paired-mobile-client.md) — accepted typed daily actions, durable reminders, paired native mobile client and separate source result cards.
- [ADR-0097](0097-exact-zero-opacity-mail-projection.md) — accepted exact supported zero-opacity predicate and versioned local mail projection.
- [ADR-0098](0098-explicit-public-places-tool.md) — accepted explicit public MapKit search, confirmed origin, owned map and separate route handoff.

- [ADR-0108](0108-conversational-calendar-command-authority.md) — accepted direct simple Calendar command authority, natural clarification and verified-only success; supersedes repeated form/read/write UI confirmation only.

- [ADR-0109](0109-explicit-gmail-mailbox-search.md) — accepted explicit foreground search across connected Gmail without prior message selection; body-read grants, session-only custody and source exclusion remain.
