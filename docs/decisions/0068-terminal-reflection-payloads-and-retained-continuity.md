# ADR-0068: Terminal Reflection Payloads and Retained Continuity

- Status: Accepted; first 14.4.2h vertical macOS engineering gate complete on 2026-10-10
- Date: 2026-10-09
- Authority: explicit continuation after Checkpoint 14.4.2g
- Related: ADR-0025/0053/0057/0059/0060/0061/0063/0066
- Extends: the existing mixed approved body stream and Reflection's retained history. It does
  not change Reflection triggering, target-owner numerical policies or Character behavior.

## Context

`reflection_proposals.payload` retains generated propositions, observations, candidate topics
and structured personality candidates. Removing canonical dialogue or derived knowledge bodies
does not remove this separate copy. Proposal IDs, runs, fixed source sets, attempts and outcomes
also anchor accepted downstream evidence, consumed roots, retry limits and idempotency.

Treating a NULL payload as an empty proposal would change routing and completion semantics.
Deleting a proposal would damage those retained references. Demanding live source quotations
before returning an already completed run also makes legitimate historical replay unavailable.

## Decision

### 1. Finished bounded scope and sole writer

Open **14.4.2h — Terminal Reflection Payloads & Retained Continuity**. The existing Reflection
owner clears the complete selected proposal payload to actual SQL NULL, including numeric JSON
candidate values. It sets `body_state='erased'` and a required `body_action_id` referencing the
existing main body application receipt. Available proposals require an object payload and no
body action. JSON `null`, `{}` and substitute prose are not terminal-body representations.

Keep proposal ID, run ID, ordinal, target owner, evidence-source IDs and creation time exact.
Keep the entire parent run, fixed sources, attempts and selected proposal outcomes exact:
status, aggregate version, source-set hash, purpose/schema/policy, counters, timestamps, reason
codes, downstream references and numerical histories are retained. Erasure creates no new
outcome, refund, rejected belief, rerouting, learning or numerical rollback. Proposal identity
and its immutable payload reference version remain reserved; payload version is exactly one.

An approved target binds the proposal/run IDs, current run version, exact original and terminal
material digests and exact reviewed metadata-reference count. Material includes the complete
raw proposal, whole parent run, all parent sources and attempts, and that proposal's exact
outcome set, including an empty set where the existing exhausted-run contract permits it.
The parent must already be COMPLETED or EXHAUSTED. Erasure never completes pending routing.

Reflection's `erase_payload_bodies(targets, *, action_id)` is the sole body writer. It validates
every target and typed receipt before mutation and does not commit. The existing main body
coordinator owns the atomic transaction and full-head acknowledgement.

### 2. Causal scope and historical continuity

Selection uses canonical owner support plus COMPLETE actual-input dependency closure. It includes
the entire fixed Reflection source window and projected upstream payload dependencies, rather
than only candidate citations. A mixed-support proposal is removed in full; no replacement is
generated from surviving sources. All affected support must prove the requested identity and
counterparty. UNKNOWN/OVERFLOW, missing versions, unsupported ancestry and affected
cross-counterparty support block an executable plan without collateral deletion.

Historical canonical roots and prior terminal knowledge evidence may establish retained
attribution through ADR-0063/0066's exact approved proofs. They do not authorize fresh learning
or substitute for live content. The five witnessed evidence table shapes remain unchanged;
Reflection body removal introduces no rewrite of an old witness, action or historical head hash.

SQL guards freeze retained parent run/source/attempt/outcome data and membership once any child
proposal becomes terminal. They also freeze proposal membership and retained headers. A later
approved available-to-erased transition of another child remains possible without changing the
already retained parent material. Terminal payloads cannot be reintroduced or deleted. The exact
schema pin includes these guards; an action FK or copied ACK alone grants no completion proof.

### 3. Available reads, headers and replay

Normal `ReflectionProposal` objects continue to require a contentful dictionary. Ordinary reads
filter available state, absent action and non-NULL payload before loading or mapping payloads.
Terminal inspection uses a separate immutable `ReflectionProposalHeader`, with no payload.
Inspection retains the existing contentful proposal interface and separately reports unavailable
headers. Metadata-only proposal IDs preserve complete proposal cardinality even when a body is
unavailable or source policy withholds a normal read.

Completed apply replay returns retained run metadata before reading proposal bodies or loading
source quotes. Exhausted runs remain non-routable through the existing terminal behavior.
Routing completion compares the complete retained proposal ID set with outcomes, never the
number of available bodies. A missing unrouted body is a closed failure, not an empty candidate.
Direct personality source resolution and commit adapters check proposal availability before
`dict(payload)`; target-owner evaluation and numerical policies remain unchanged.

Fresh dispatch and final owner commit both recheck current payload availability at the same
immutable payload version. No barrier lock is held across a provider call. A result prepared
before erasure cannot route or persist a new effect after the terminal transition.

### 4. Floor-first action, replay and recovery

Add `ReflectionBodyAction` to the existing mixed floor stream through explicit **policy_0006**
upgrade, and add owner representation/guards through **0021_reflection_bodies**. All earlier
action JSON and head hashes remain exact. Operational reads never upgrade either database.
Known exact older main schemas use closed operator-only historical column projections; ordinary
runtime has no missing-column or damaged-current-schema fallback.

The excluded proved root, durable intent, current source/intent owner repair and fresh separate
Reflection-body approval are prerequisites. Approval binds scope, schema, source generation,
erasure revision, body revision/head, exact selected material, effects and bounds. Commit the
approved action to the independent floor under the exclusive barrier before any main body
mutation. Record the application and invoke Reflection in the bounded main transaction, verify
actual NULL bodies and exact retained material, then acknowledge the complete effective head.

Resume privately materializes all approved effective targets. Each target must match its exact
authorized original or terminal state. Pre-commit faults roll back main changes; the durable
floor action remains pending. An uncertain completion requires fresh actual-state proof.
Changed/expanded material requires a fresh approval and cannot be silently repaired by replay.
Old main backups, forged acknowledgements or terminal markers without exact approved targets
deny ordinary protected operation. Raw offline recovery/export remains explicit administrative
transport under ADR-0053, not a completion or forensic-erasure claim. Downgrade requires a
separate offline owner policy and cannot discard terminal state or floor proof.

### 5. Bounds and honest residuals

Keep the existing limits: 100,000 scanned rows, 64 MiB logical input, 4,096 mixed actions,
1,000,000 global action references/assessment work, 256 Reflection targets per action and
131,072 bytes per serialized action. Exact metadata-reference counts cover the whole retained
closure. Reflection's existing three-proposal, twelve-source and two-attempt caps remain exact.
Capacity checks occur before approval or mutation; no unbounded payload census is admitted.

This removes selected logical Reflection payload copies. `physical_erasure` remains false.
Remaining affect/appraisal/audit JSON, free-form checkpoint reasons, numerical trajectories,
digests and IDs, SQLite free pages/WAL, archives, caches, external copies and unauthenticated
operator/device/floor rollback are explicit residuals. Access control, encryption/key recovery,
forensic cleanup and the Linux process gate remain separate. Control commands invoke zero
providers. A fake foreground regression is engineering evidence, not a Character evaluation.

## Required engineering gates

- Exact SQL NULL payloads with preserved complete run/source/attempt/outcome rows and FKs;
  JSON null/empty-object confusion, resurrection and membership mutation are rejected.
- V1/V2/V3 and disabled-owner payloads, completed/exhausted parents and exact empty outcome sets;
  unavailable headers and complete proposal cardinality remain truthful.
- Completed/exhausted replay loads no proposal payload or source quote; normal routing and
  personality boundaries cannot map or commit unavailable content at the same payload version.
- Complete/transitive/mixed support, unrelated proved scope, cross-counterparty and UNKNOWN
  ancestry selection, with no guessed historical quotation or source promotion.
- Floor-first faults, atomic rollback, restart, uncertain completion, stale reviews, old-backup
  restore and forged ACK/body markers; metadata/NULL checks precede protected body reads.
- Exact old action/hash compatibility, supported old recovery schemas and explicit upgrades;
  malformed current schemas fail closed without a legacy runtime fallback.
- Deterministic owner/integration/CLI scenario, zero paid calls, relevant admission performance
  and the frozen Foundation toolchain. Record actual results only after that gate completes.

## Engineering record — 2026-10-10

The terminal Reflection vertical passes the rebuilt isolated
Foundation gate: **4448 passed / 5 skipped**,
Ruff format/lint, mypy, fresh migration and bootstrap. The actual final schema is revision 0021,
59 tables and 73 SQL guards, with exact pin admission, integrity `ok` and zero FK violations.
The actual synthetic CLI/restart/recovery scenario
passes 20/20 checks. All controls use zero real providers and USD 0.

Initial full-run failures are preserved separately; four stale test fixtures were corrected and
the entire gate rerun without production changes or test removal. Local cost has its own
postgate helper verification; the frozen full-run source remains immutable. Scope, residuals
and the proposed next owner contract are in [erasure Reflection](../erasure-reflection.md).
This record does not open another vertical or accept Character, physical erasure or whole 14.4.
