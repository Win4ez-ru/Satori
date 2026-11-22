# Owner-controlled episodic archival — Foundations 14.4.2b.1

Contract: [ADR-0056](decisions/0056-owner-controlled-episodic-archival.md). The active boundary and
verification record are in [progress](progress.md). This vertical adds explicit local owner
controls. Canonical source exclusion/support withdrawal are implemented separately under
[ADR-0057](decisions/0057-canonical-source-exclusion-and-trusted-policy-floor.md); physical erasure
and authenticated access remain later Checkpoint 14.4 work.

Engineering gate completed on 2026-10-03: **3460 passed / 5 skipped**, lint/typing, exact-schema
migrations/bootstrap, synthetic restart/restore/operator scenario and native runtime smoke.
The isolated canonical verification and shared-tree limitations are recorded in `progress.md`.

## Operator workflow

Inspect current scoped episodes with `satori memories`. The configured identity/counterparty
partition is proved from canonical sessions and every exact evidence edge, as in
[memory scope](memory-scope.md). A configured counterparty is attribution, not authentication.
Unverified, malformed and foreign targets remain unavailable through lifecycle controls.

```bash
uv run --no-sync satori memories archive MEMORY_ID --preview
uv run --no-sync satori memories archive MEMORY_ID --apply \
  --expected-version VERSION_FROM_PREVIEW --sha256 DIGEST_FROM_PREVIEW \
  --request-id UNIQUE_OPERATION_ID
uv run --no-sync satori memories --include-archived
uv run --no-sync satori memories reactivate MEMORY_ID --preview
uv run --no-sync satori memories reactivate MEMORY_ID --apply \
  --expected-version VERSION_FROM_PREVIEW --sha256 DIGEST_FROM_PREVIEW \
  --request-id ANOTHER_UNIQUE_OPERATION_ID
```

Preview exposes only operation metadata, current status/version, scope and retained effects.
It does not contain summary, evidence quote or raw dialogue. Applying requires that exact review
and expected version. A changed target or invalid digest rejects without mutation. Repeating an
operation ID with the same payload returns its original receipt and the current status/version;
it does not reapply an old archive after a later reactivation. A reused ID with changed payload
rejects. Request IDs are durable operation handles; keep the applied command for retry.

A request whose target status already holds is an accepted, audited no-op. It produces a receipt
without increasing the episode version. Actual transitions increase the monotonic version.
Archive/reactivate controls compose no providers and require no Character behavior or prompt.
Storage failure rolls back the transaction and produces a content-free CLI error.

## Product effects

| Surface | Archived episode |
|---|---|
| Ordinary episode list, retrieval candidates and embedding precheck | Excluded |
| New embedding/backfill, semantic extraction/window/backfill, episode reflection intake | Excluded |
| Explicit scoped `--include-archived` inspection | Retained; status/version labeled |
| Original raw conversation and recent raw continuity | Retained under existing scope rules |
| Summary, evidence, formation decision, importance/confidence | Preserved |
| Already formed semantic claims, confidence, keys and revisions | Preserved and scoped inspectable |
| Foreground semantic recall | Still requires a selected ACTIVE episodic relevance anchor |
| Compatible stored vectors | Preserved; reusable after explicit reactivation |
| Existing affect, relationship, User/World Model and own self effects | Preserved |

Another ACTIVE source can reinforce the same semantic fact and merge into its existing aggregate.
The new source must pass complete canonical proof. Archived evidence may survive a merge only
when it is an exact edge already owned by that admitted claim. Newly supplied archived evidence
is not admitted. This keeps knowledge identity stable without treating archive as evidence
withdrawal. An archive-only claim has no new episodic relevance anchor; no independent semantic
search or global dump is introduced.

Archive does not mean “forget this conversation.” Raw history remains readable and can participate
in recent continuity. Previously supplied or in-flight provider context cannot be recalled.
Guarantees cover new admission and late writes. Formation-version replay of the archived episode
skips its index/semantic phases deliberately; it does not resurrect the episode or report an
expected lifecycle exclusion as a provider outage. Other raw-root owners retain their policies.
If the lifecycle inspection itself fails, post-processing reports the deduplicated metadata-only
`memory_lifecycle_inspection` phase; index/semantic admission still fails closed, and the committed
foreground reply remains saved.

## Persistence and concurrency

MemoryManager evaluates a typed proposal. The existing memory UoW atomically commits a conditional
status/version update, append-only decision and content-free audit. Every write checks proven
scope and expected status/version. Scope proof also precedes receipt replay. Decisions include
the policy version, explicit `local_operator` label, time, reason and before/result versions;
that label does not grant authenticated access.

Provider work occurs outside transactions. Index and semantic processing reload complete source
snapshots before commit and bind writes to canonical proof and aggregate versions. An
archive→reactivate cycle advances the version twice, so the restored ACTIVE status cannot admit
a result sampled before those transitions. Bulk failure preserves the batch transaction and
reports failed counts; a foreground committed reply remains saved.

Migration 0015 preserves existing episode content and initializes version one. Downgrade refuses
while archived state or decision history would be lost. Recovery accepts exact 0013/0014/0015/0016
schema references and preserves archived status, version, evidence, vectors, receipts and audit
offline. A backup predating an archive restores its earlier lifecycle honestly. Implemented
source exclusion uses a separate trusted policy floor under ADR-0057; a database-only tombstone
cannot protect against an older database snapshot. Physical erasure remains separate.

## Acceptance and remaining dependencies

The gate covers real UoW transitions/no-op/replay, stale review and payload mismatch, scoped
denials, failure at each write boundary, delayed indexing/semantic ABA, retained semantic identity,
restart and exact whole-state restore. A synthetic fresh-process CLI scenario verifies the
operator workflow and useful own recall. Exact final counts and measured timings belong in
`progress.md`; no Character acceptance is inferred from them.

The source eligibility vertical implements canonical-root
eligibility outside episode archival, checks across every dependent owner, knowledge support
withdrawal and an explicit recovery policy floor. Foundation/macOS gates passed; actual Linux
process execution remains open. Physical erasure follows its own reviewed contract.
Disclosure minimization, encryption/key recovery and local principal/access
enforcement remain 14.4.3. No external source or action stage is opened here.

Source policy is checked independently of archive state. Excluded or unproved payloads cannot
be reactivated to bypass withholding. Archive/reactivate version changes also invalidate queued
top-level dispatch and late candidate commits; formed semantic historical parent receipts remain
independent of reversible archival. See [source policy](source-policy.md).
