# ADR-0064: Previous Exchange Context for Affective Appraisal

Status: Accepted design for the explicitly continued Character iteration, 2026-10-06.
Behavioral quality and Character acceptance remain open.
Supersedes only ADR-0015's restricted appraisal-input clause for this opt-in context.

## Context

The context-boundary diagnosis
verified inherited numerical emotion/mood but no direct previous dialogue in appraisal. Four
authored context contrasts collapse to identical appraisal inputs while foreground history
differs. This establishes an information boundary, not correct categories or a recap root cause.
The earlier drive-line proposal was withdrawn. This iteration tests one narrower hypothesis:
one previous exchange may improve attribution and emotional consequences of the current event.

## Decision

`SATORI_AFFECTIVE_APPRAISAL_PREVIOUS_EXCHANGE_ENABLED` defaults to false. It changes only
appraisal context; foreground policy remains V35 by default, and V36's question cue is separate.
No provider/model, category mapping, native caps, owner formula, personality or relationship
state is changed. The existing appraisal remains before frozen agency and reply generation.

When enabled, Talk considers only the last slot of already loaded eligible recent history.
Through existing InteractionLog reads, it verifies the exact complete canonical pair, roles,
IDs, unmodified bodies, supported v1 message/interaction/session schemas, chronology and the
same verified identity/counterparty/session. At most that one pair is included. Missing,
unavailable, mismatched or oversized input is omitted atomically; no text truncation, older
replacement or global history walk. Upstream filtering may omit intervening turns, so the
projection is called the last available exchange, not a proved immediate predecessor.

The provider-neutral immutable `AppraisalPreviousExchange` has its own schema 1, explicit
user/assistant fields and exact source IDs. The sum of the two raw bodies is at most 2000
characters. Input request schema 2 is used only with this context; absent-context input schema
1 and actual provider payload remain unchanged. Output categorical wire 2 and semantic proposal
schema 1 remain separate from the input version. The adapter cannot copy input version into
the owner proposal.

Prior wording is untrusted interpretation context: it can identify what Satori actually said,
not establish the truth of those words, authoritative self facts or the user's intent. Only
the current user event is new evidence. History IDs are not added to provider output handles or
EmotionManager's allowed proposal refs; relationship appraisal/evidence remains unchanged.
No grievance, emotion label or utterance is mandated by a particular sentence.

## Source dependencies and ownership

The appraiser's complete actual input footprint includes the current USER root, selected
retrieval dependencies, the previous USER root and the existing version-1 `assistant_reply`
payload ref. Context dependencies remain separate from admissible mutation evidence. Existing
ADR-0057 dispatch and fresh finalization checks apply even to uncited history.

For context-bearing attempts, PreparedAffectiveContext carries the input receipt and complete
roots/refs on success, skip, rejection and provider failure after preparation. Talk unions
these dependencies into the canonical reply footprint; finalize rechecks the appraisal receipt
even without an affect transition. SourcePolicyError is propagated rather than interpreted as
an optional appraisal failure. No new admission port, repository, writer, migration or rollback
policy is introduced. Terminal bodies remain unavailable through ADR-0063's existing reads.

EmotionManager alone validates and applies the proposal. Its bounds, decay, numerical
inertia, mood coupling, CAS and reply-atomic commit/replay/failure semantics remain. Relationship
and personality keep their separate owners. The existing ten-reason max-one response retry
contract is unchanged, with no new judge, rewrite, catchphrase or extra normal-turn model call.

## Verification and decision gate

Verify exact legacy native payload, strict input/output version separation, source reachability,
pair validation, atomic oversize omission, two-counterparty isolation, uncited exclusion at
dispatch/finalize, skipped/rejected/failure dependencies, rollback/replay/restart and actual
context passage. With identical scripted proposals, context-on/off must yield equal owner,
cognition, agency and foreground requests. Scripted differing proposals can prove the path
through bounded state and selection, not the semantic correctness of choosing those proposals.

Run the rebuilt noneditable Foundation workflow and relevant historical Character, affect,
grounding, recent-history, source-policy and replay regressions. Native multi-turn sessions,
identity golden and semantic appraisal diagnostics are engineering evidence. Preserve actual
replies and timings privately; local-model prose cannot establish target-provider quality.

A separately frozen target-API appraisal/foreground experiment and small human blind review
are needed for behavioral judgment. Hold models, inherited state, retrieval, relationships and
caps equal, changing only this context; inspect subsequent neutral turns and repair as well as
the trigger. Preserve failures and context omission. Existing consumed grants and unused V36
plans do not authorize or describe this experiment. Character remains at 14.3 Phase A and
parallel Foundations proceeds independently. This ADR does not accept recap or Character.
