# Identity-owned checkpoint reason disposition

The explicitly continued 14.4.2h vertical follows
[ADR-0072](decisions/0072-identity-scoped-checkpoint-reason-disposition.md). Its macOS engineering
gate is complete; evidence and residual acceptance boundaries are recorded in [progress](progress.md).

## Scope and ownership

An operator reason belongs to an exact checkpoint approval or personality restore receipt and
its explicit identity. It has no canonical USER root or counterparty attribution. Checkpoint
lineage does not establish who authored its reason, and source exclusion cannot select it
indirectly. The closed selector is `(checkpoint_approval | personality_restore, record_id)`;
scope contains the trusted installation's identity and lineage.

`CheckpointReasonAction` is the sixth closed action in the existing mixed body stream. The old
five envelopes and hashes remain unchanged. It advances the same body revision, with the same
installation binding, source/erasure/body CAS, global request-ID collision policy, reviewed
store/schema/approval digest and whole-head floor-first barrier. It has no fake source intent.
Explicit independent `policy_0008` and main `0024_checkpoint_reasons` admit this exact variant;
old source variants still require complete canonical scope.

The existing Personality repository is the sole write path. It proves every selected owner
before any mutation, sets its reason to actual SQL NULL and records terminal availability and
the approved action ID in the same main transaction as every mixed-head owner write and ACK.
It does not approve or restore a checkpoint, create a revision or change numerical state.

## Immutable material and ordinary reads

Review binds the exact receipt and immutable closure. Approval retains its checkpoint and all
fifteen trait rows. Restore retains the owner event, exact revision, source/result checkpoints,
their trait rows and numerical diffs. Hashes include the original reason only in the private
available-body proof; terminal proof has NULL and retains accountability. Future mutable
Personality state is outside this closure and evolves independently through its existing owner.
Existing audits and prospective bindings remain byte-exact. Missing legacy bindings stay UNKNOWN;
this operation does not retroactively certify audit provenance.

Metadata headers preserve approvals/restores, terminal availability, latest approved budget
anchor, checkpoint hashes, diffs, versions and history. Budget and inspection readers do not load
reasons. Contentful owner lists admit the current mixed head and return only available reasons.
Approval replay preserves original checkpoint/hash behavior and returns terminal metadata without
repopulating text. A stale restore proposal keeps its version-conflict result and creates no
second restore. Historical numeric influence and path spend are retained.

SQL guards deny terminal owner mutation/removal and conflicting REPLACE through all primary and
unique aliases, including recursive triggers disabled. Referenced fixed checkpoint/trait/revision
material is protected too. Ordinary admission proves SQL NULL, exact retained material and the
approved whole head before protected reads. An ACK alone is insufficient.

## Explicit local controls

Migrate the main database explicitly, then review the independent policy upgrade:

```bash
satori personality reasons-policy --upgrade --reviewed
satori personality reason-bodies --identity-id ID --approval APPROVAL_ID --restore RESTORE_ID --preview
satori personality reason-bodies --identity-id ID --approval APPROVAL_ID --restore RESTORE_ID --apply \
  --request-id REQUEST_ID --expected-generation G --expected-erasure-revision E \
  --expected-body-revision B --sha256 REVIEW_DIGEST
satori personality reason-bodies --identity-id ID --request-id REQUEST_ID --status
satori personality reason-bodies --identity-id ID --request-id REQUEST_ID --resume
```

Use repeated selector flags for different records; duplicates are rejected. Preview/apply require
explicit nonempty selectors. Receipt status/resume uses identity and original request ID; supplied
selectors must match exactly. Changed selection, expected heads or digest cannot reuse approval.
Scope is proved before private owner census/counts. Output has safe counts/digests/status codes,
no reason prose or foreign receipt data. Read paths do not migrate, enroll, initialize owners or
construct providers.

Mixed selections retain every reviewed selector in the approved targets, including an already
terminal reason. The latter binds its current terminal material and is not written again;
new-body counts and `already_erased` remain separate. The approval digest binds both exact IDs,
and identical replay preserves the original terminal marker. An all-terminal selection has no
new body operation and is explicitly blocked; status is the read-only completion control.

Main failure after the durable floor commit leaves the operation pending. Explicit resume reuses
the approved whole head after proving actual material. Faults before main commit roll back every
owner write and ACK together. Restoring an older main archive while retaining the floor cannot
re-enable reasons: ordinary use is fenced until the complete terminal head is reconciled.
Archives preserve original bytes.

## Bounds and residuals

At most 256 sorted unique selectors and 131,072 action bytes are admitted. Existing 100,000-row,
64 MiB logical-store, one-million-work and mixed-head limits remain. Protected reasons are not
cached. Controls invoke no cognitive provider and add no Character implementation dependency.

Every result has `physical_erasure=false` and `execution_authorized=false`. Selected logical
bodies are removed; numerical influence and sensitive IDs/hashes remain. Legacy UNKNOWN audit
prose and unsupported scalar codes, SQLite free pages/WAL, older archives/exports, OS/provider
copies, whole-floor rollback, authenticated access, encryption/key recovery and Linux/full-14.4
acceptance are separate gates. Stage 15 is not opened.

## Verified engineering evidence and next proposal

Full isolated Foundation: **4726 passed,
5 skipped, 16 warnings**, Ruff format/check, mypy, fresh migration and bootstrap;
exact frozen-source/noneditable package parity. The isolated stable-Character compatibility gate
is qualified separately from moving Character behavior, Linux and complete 14.4 acceptance.

Actual mixed-head/archive/restart scenario
passes **20/20**; Appraisal regression
passes **20/20** and retained audit **12/12**.
Two actual owner reasons become NULL while 51 fixed references, numerical budget/traits and audit
bindings remain unchanged. The old main archive is fenced until explicit whole-head resume;
restarted foreground input and reply contain neither terminal reason nor excluded source secret.

Local warm control cost, 50 samples after two
warmups: shared admission median/p95 **12.488/13.008 ms**, status
**23.566/24.431 ms**, terminal preview **33.681/34.591 ms**.
All samples re-prove actual schema/material; controls use zero cognitive ports and preserve rows
and floor/binding/lock bytes. This is a small synthetic store with unknown host load.

The next proposed vertical is **14.4.2i — Whole-store Retention & Readiness Integration**: reuse
existing owner routing and completed terminal proofs for an honest bounded retained/content/UNKNOWN
partition, with closed scalar metadata shapes and explicit legacy blockers. It is not opened here;
physical erasure and access/encryption remain separate.
