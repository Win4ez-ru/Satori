# ADR-0072: Identity-scoped Checkpoint Reason Disposition

- Status: Accepted; macOS engineering gate passed
- Date: 2026-10-13
- Authority: explicit user continuation after the completed retained audit metadata vertical
- Related: ADR-0053/0057/0059/0060/0063/0066/0068/0070/0071
- Supersedes: only the assumption that every body action must have a canonical USER root and an
  erasure intent. Existing five source-action contracts and their serialized bytes remain exact.

## Context

Checkpoint approval and personality restore records contain operator-authored freeform reasons.
Their explicit owner is the identity and exact receipt, not a counterparty or canonical message.
Checkpoint ancestry cannot attribute those reasons to a USER source. Source exclusion therefore
cannot safely select these bodies. Ordinary budget and inspection queries currently load these
reasons incidentally even when they need only immutable numerical metadata.

## Decision

Add a sixth closed variant, `CheckpointReasonAction`, to the existing mixed body-action head.
Its scope is the trusted installation's identity and lineage, and its bounded sorted selectors
are exact `checkpoint_approval` or `personality_restore` record IDs. It has no canonical root,
counterparty, source-input or intent reference. Closed dispatch proves this variant's identity
scope explicitly; absence of a root never exempts an arbitrary action from validation.

Independent `policy_0008` permits NULL source/intent columns only for this exact policy version;
all five old policy versions still require all source fields. Main `0024_checkpoint_reasons`
likewise permits a NULL application root only for this variant. Existing identity/installation
binding, global request-ID collision handling, expected source/erasure/body revisions, reviewed
store/schema/approval digests, same body counter, whole-head preflight, floor-first commit barrier
and main acknowledgement transaction are reused. No second ledger or coordinator is introduced.
Old dataclasses, action bytes, hashes and historical material contracts remain unchanged.

The existing Personality owner is the sole writer. A terminal disposition sets only the selected
`reason` to actual SQL NULL, with explicit erased state and the approved action ID. This is neither
a new checkpoint approval nor a restore, trait mutation, budget refund or evolution revision.
Immutable proof binds the exact owner receipt and fixed referenced checkpoint/trait/revision/diff
closure; it does not bind unrelated future mutable Personality state. Existing audit events and
prospective binding bytes remain unchanged. Missing legacy audit bindings remain UNKNOWN.

Metadata-only headers preserve approval existence, idempotency, latest approved budget anchors,
inspection and restore behavior without loading or inventing reason text. Contentful owner reads
return only available reasons after current policy/material admission. Approval replay preserves
its existing checkpoint/hash semantics and returns terminal metadata without repopulating prose;
stale restore proposals retain the existing version-conflict behavior.

SQL guards cover UPDATE/DELETE/conflicting REPLACE through every PK and unique alias, including
recursive triggers disabled. Main material proves NULL and exact retained accountability before
ordinary admission. Restoring an older archive cannot re-enable reasons while the durable floor
retains their action; a pending whole head fences use until explicit reconciliation succeeds.
Faults before main commit roll back every owner write and ACK; uncertain completion is re-proved.

Public controls use explicit identity and exact selectors, return safe counts/digests/reason codes,
and do not disclose owner prose or foreign receipt data. Scope is proved before private census.
Controls are provider-neutral, bounded and local; no Character tuning or new Character contract
is needed. Review does not grant technical execution permissions or authenticated access.

## Engineering gates and limits

- Strict domain/codec/scope, reviewed freshness, global replay/collision and legacy-five parity.
- Real approval and restore records, retained numerical traits/checkpoint hashes/diffs/versions,
  budget anchors and audit/binding bytes; no regenerated reasons or second owner operation.
- SQL alias guards, tampered owner/material/identity, whole-head rollback and fault/restart proof.
- Actual private mixed-head/archive/restart scenario, relevant tests and rebuilt full Foundation.
- Bounded local control cost, zero real providers and no live-installation disposition.

This is logical body disposition. Numerical influence, sensitive identifiers/hashes, legacy audit
UNKNOWN/prose and unsupported scalar reason codes remain. SQLite free pages/WAL, old archives,
exports/OS/provider copies, whole-floor rollback, physical erasure, access/encryption/key recovery,
Linux and whole-14.4 acceptance are separate gates. Results retain `physical_erasure=false` and
`execution_authorized=false`. Stage 15 is not opened.
