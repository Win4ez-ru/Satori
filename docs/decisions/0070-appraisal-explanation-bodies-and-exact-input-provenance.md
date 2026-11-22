# ADR-0070: Appraisal Explanation Bodies and Exact Input Provenance

- Status: Accepted; explicitly continued 14.4.2h appraisal vertical implemented and macOS engineering gate passed
- Date: 2026-10-10
- Authority: explicit continuation after the completed terminal Reflection vertical
- Related: ADR-0015/0053/0057/0060/0063/0064/0066/0068
- Supersedes: only ADR-0015's contentful historical-transition representation after a separately
  approved terminal explanation action. EmotionManager ownership and numerical dynamics remain.

## Context

Appraisal preparation already records the actual current USER root, all selected episodic/semantic
payload dependencies and, when independently enabled, the previous exchange's exact USER and
assistant-reply dependencies. Finalization checks the prepared input receipt. The affective
repository does not persist that receipt's ID on the resulting transition. A primary source ID
and provider citations cannot establish which uncited retrieval or previous dialogue was read.

The eleven-score `appraisal_payload` is numerical history. Provider-neutral `reason_codes` can
contain arbitrary bounded text and have an exact second copy under the owned
`emotion.transition_applied` event's `details.appraisal_reason_codes`. Removing the entire numeric
envelope would damage retained affect trajectories and downstream signal hashes. Reconstructing
an empty appraisal proposal would violate its mandatory contentful contract and could corrupt replay.

## Decision

### Exact prospective links, without reconstructed legacy provenance

The existing EmotionManager persistence owner records `source_input_id` and the exact
`audit_event_id` with every new managed transition, in the existing canonical-reply transaction.
Both are explicit foreign keys. The same transaction retains the original actual input receipt,
transition, paired audit event, numerical state change and canonical reply. Source dispatch and
fresh final commit continue to validate the exact prepared receipt. No new content graph, generic
payload kind, owner, admission ledger or source-generation counter is added.

Migrated rows retain NULL links and remain UNKNOWN for destructive completeness. Do not infer an
input from source citations, another interaction's input, familiar JSON keys or chronological
adjacency. A failed transaction leaves no partial link, audit copy or state mutation. Transition
insertion occurs before completion of its canonical interaction inside the same transaction;
insertion guards must not require a prematurely completed parent.

### One owner and one paired logical disposition

Main `0022_appraisal_bodies` adds `explanation_state` and `explanation_action_id` to the existing
transition, and permits actual SQL NULL `reason_codes`. Available rows retain their ordinary
nonempty reason list and no body action. An erased explanation requires actual SQL NULL and a
valid main body-application FK. JSON null, an empty list and replacement prose are not terminal
representations.

The exact linked owned audit event loses only the `appraisal_reason_codes` key. The key is absent,
not replaced with JSON null or an empty list. All other event details and event headers remain
exact. This event-specific owner operation does not clear generic audit JSON or add a second
writer to the audit system. The sole `erase_explanation_bodies(targets, *, action_id)` method
validates the reviewed pair and does not commit. The existing main body coordinator commits both
copies and the complete mixed-head acknowledgement in one transaction.

Retain all eleven appraisal scores, confidence, source IDs, before/after snapshots, fast/mood
deltas, state/mood/policy/schema versions, provider metadata and committed time. Retain current
emotion/mood, personality, inclination and relationship numerical effects and consumed budgets.
This is explanation disposition, not numerical rollback, unlearning or Character tuning.

### Closed classification and complete causal scope

Every executable target requires an exact transition, COMPLETE nonlegacy actual input receipt
and one exact owned audit row. A strict raw classifier proves supported schemas; exact keys;
finite bounded numbers excluding booleans; typed snapshots, deltas and integer versions; and the
writer's closed event shape. Audit identity, transition/interaction/source IDs, trace, time,
versions, policies, source refs and original reason list must match the transition exactly.
Extra JSON, missing/duplicate/mismatched events or malformed numeric skeletons block approval.

Selection closes the actual receipt's roots and immutable payload dependencies through the
existing canonical owners and COMPLETE dependency DAG. Candidate citations are insufficient.
Mixed support removes the entire explanation pair. Affected cross-counterparty, uncertain,
missing-version, unsupported or overflowed ancestry blocks destructive readiness. Historical
terminal roots/upstream bodies use only exact prior approved attribution proofs, never invented
quotes or renewed eligibility. Unlinked legacy rows cannot prove independence and remain an
explicit completeness blocker.

An `AppraisalBodyTarget` binds transition ID, exact input/audit IDs, immutable explanation version
1, original/terminal material digests, an exact `input_closure_digest`, and a reviewed metadata-reference count. Material binds the
complete retained transition, exact input receipt and exact audit row. The original and terminal
transforms differ only in explanation availability/action normalization and the two content copies.
The closure fingerprint binds every reachable immutable payload receipt and canonical root header. It excludes mutable eligibility/policy-generation mirrors, lifecycle state, body text and erasure markers. Pair material hashes strict canonical audit JSON, while the fresh full-store approval still binds stored bytes. Selection separately proves the complete upstream closure. Ordinary admission compares bounded metadata against this fingerprint before protected content reads. Pending resume repeats the exact fingerprint and owner proof and
accepts only exact approved original or terminal material. Changed ancestry or material requires
fresh review, not a repair that guesses old inputs.

### Numerical reads, content availability and idempotency

Normal `AffectiveTransition` remains contentful and never maps NULL reasons into a substitute
proposal. Managed contentful reads additionally require the linked actual input's historical
eligibility; an uncited excluded dependency withholds the explanation. Missing managed links
remain unavailable as content. Numerical/header history is a distinct immutable interface that
includes available, withheld and terminal transition metadata and the strict numerical skeleton,
without explanation text. Status and duplicate-interaction detection use metadata first, so an
erased explanation never hides the latest transition or permits a second numerical application.

Existing Reflection/Inclination affective signals use numerical payloads and deltas, not reasons.
Their exact historical hashes and all five evidence witness shapes remain unchanged. No new
Character state, expression constraint, prompt, model, provider wire or appraisal formula is needed.

SQL guards freeze retained transition metadata and its paired event after terminal disposition.
They prevent resurrection, removal, membership substitution and conflicting `INSERT OR REPLACE`,
including with recursive triggers disabled. Ordinary admission proves SQL NULL/state/action and
audit key absence before loading retained material or historical protected Reflection/Dialogue
rows. Copied ACKs or an old main backup cannot substitute for the actual terminal pair.

### Shared floor, recovery and bounds

Explicit `policy_0007` introduces the fifth closed action to the existing mixed `body_revision`
stream. Earlier Episode/Dialogue/Knowledge/Reflection action bytes and head hashes remain exact.
Operational commands never migrate main or floor implicitly. The excluded proved root, durable
intent, reconciled owners and fresh digest-bound explanation approval remain prerequisites.
Commit the independent-floor action under the existing exclusive barrier before any main write.
Pre-commit faults roll back the whole main transaction while leaving the floor action pending.
Uncertain main completion remains fenced until fresh actual-state/head proof, with explicit
resume where reconciliation is pending.

Supported exact historical schemas remain administrative transport/reconciliation seams. A
malformed current schema has no legacy fallback. Old archive restore retains archive bytes and
requires explicit current-head reconciliation before operational admission. Downgrade requires a
separate offline owner policy.

Use existing bounded census/assessment material: 100,000 scanned rows, 64 MiB logical input,
4,096 mixed actions, one million references/assessment work, 256 targets per action and 131,072
serialized action bytes. Batched indexes/read budgets avoid target-by-whole-store work and do not
cache protected explanation text or skip fresh schema admission.

## Required engineering gates and limits

- Actual SQL NULL reasons and absent exact audit key, with unchanged numerical and other rows,
  exact signal hashes, FK integrity, empty reason/JSON-null confusion and guard bypass attempts.
- Atomic prospective links and existing dispatch/finalization exclusion barriers; uncited
  retrieval/prior-exchange closure, cross-counterparty denial, stale material and legacy UNKNOWN.
- Numerical/header history, latest status and metadata replay remain exact after disposition;
  no second mutation, empty appraisal proposal or reason reconstruction.
- Floor-first faults, paired-write rollback, restart/resume, copied ACKs and actual old-archive
  restore/resume; old body action/hash compatibility and strict schema admission.
- Actual synthetic CLI/restart/recovery and foreground/retrieval regression, bounded local cost,
  zero paid calls and the rebuilt frozen noneditable Foundation toolchain.

All public results retain `physical_erasure=false`. Numerical influence, hashes/IDs, other audit
events/free-form reasons, SQLite free pages/WAL/sidecars, archives/staging/OS/provider copies,
whole-installation rollback, authenticated access/encryption/key recovery and Linux verification
remain declared residuals. This ADR does not complete whole 14.4 or open Stage 15.
