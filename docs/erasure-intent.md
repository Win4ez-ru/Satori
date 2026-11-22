# Durable Erasure Intent & Owner Preparation

Status: implemented bounded contract; macOS engineering verification passed under
[ADR-0060](decisions/0060-durable-erasure-intent-and-owner-preparation.md).
This is Checkpoint 14.4.2d within the Foundations workstream. The final frozen Foundation passed
3991 tests with 5 expected skips; [progress](progress.md) records exact evidence and limits.
Physical execution and whole 14.4 acceptance remain open.

## Capability and boundary

The slice records a terminal metadata-only privacy request in the independent installation
policy floor and explicitly resumes existing owners' preparation. The request survives an
older main-database restore while that trusted floor survives. Protected runtime requires a
current composite acknowledgment before provider/context/job use.

It does not physically erase, redact, remove vectors, compact SQLite, clean files or produce a
deletion certificate. Physical execution is always denied and real-data erasure remains closed.
Character expression, prompts, personality evolution and numerical relationship/self policy
remain outside this workstream.

The existing [erasure preview](erasure.md) is a reviewed inventory, including its uncertainty.
A blocked inventory can be deliberately committed as an intent, but cannot become complete or
actionable through that commitment. Existing [source exclusion](source-policy.md) already
withholds the selected root. Neither exclusion nor intent proves that retained text was removed.

## Controls

The command contract is:

~~~shell
uv run --no-sync satori sources erasure-policy --upgrade --reviewed
uv run --no-sync satori sources erasure-intent ROOT --preview
uv run --no-sync satori sources erasure-intent ROOT --commit --expected-generation G --expected-erasure-revision R --sha256 DIGEST --request-id ID
uv run --no-sync satori sources erasure-intent ROOT --resume --request-id ID
uv run --no-sync satori sources erasure-intent ROOT --status --request-id ID
~~~

ROOT is an existing canonical USER root in the configured identity/counterparty scope. The
interaction is completed, attribution is proved and the root is already excluded. Intent controls
do not implicitly enroll a policy floor or exclude a source.

Preview/status are read-only. They create no database/directory, change no file modes and perform
no migration, enrollment, owner repair, domain initialization or provider request. Policy upgrade,
intent commit and resume are explicit metadata mutations. None authorizes a content mutation.

Preview and successful policy upgrade return exit 0. Intent results return exit 2 when
main_projection_current is false, and exit 0 when the main head acknowledgment is current.
The latter may still report preparation_current=false or blocked preparation/physical readiness;
exit 0 is acknowledgment status, not a deletion permit or a certificate of preparation freshness.

Status proves target scope before looking up a request. A wrong identity, lineage or counterparty
cannot inspect another receipt or infer its existence through returned counts/digests. Public
results do not contain quotations, summaries, model values, propositions, audit JSON, private
batch selectors, descendant IDs or raw exception messages.

## Floor and schema

SourcePolicyManager is the only floor writer. The intent belongs to the existing independent
trusted installation store, not the main DB or a backup bundle.

Policy_0001 remains supported for existing source-exclusion operations. Erasure intents require
an explicit reviewed upgrade to policy_0002. Read-only controls and ordinary runtime cannot
implicitly upgrade or replace the floor. Upgrade preserves the existing installation/lineage
binding, exclusions, receipts and fixed ledger/barrier inodes. A missing or replaced enrolled
floor is denied; it cannot be recreated as an empty installation.

Source generation keeps its exclusion-only meaning. A separate monotonic erasure_revision
advances when new intent commitments are recorded. Main acknowledgment binds both revisions
and the installation/floor/lineage/identity. It is a derived projection rather than authority.

Owner preparation receipts are distinct from the five existing source-owner repair watermarks.
Source repair confirms logical support withdrawal; preparation records assessed retention and
readiness. Neither is a physical deletion receipt.

Main 0017_erasure_intent adds only derived acknowledgment and preparation metadata. Intent
preparation requires its pinned exact schema; preview/status do not migrate a predecessor.
The 14.4.2c inventory supports exact 0016_source_policy, 0017_erasure_intent and
0018_erasure_bodies. Offline recovery preserves separately validated predecessor compatibility
through exact 0018. New body-action acknowledgment is separate from this metadata-only intent
preparation; no intent control executes it automatically.

## Review, commitment and resume

The review binds exact root/scope and trusted installation state, supported schema, ordered
owner routes, uncertainty, retention policy and inventory digest. The opaque inventory routing
digest binds inspected references/versions; counts alone cannot bind reviewed state.
This slice persists no executable plans or batch selectors.

The reviewed_preview_digest records the existing read-only inventory. A separate approval_digest
binds the intent envelope and expected composite floor state. The commit SHA-256 argument approves
that envelope. It is distinct from an archive checksum, source-exclusion approval and future
action-plan digest.

Commit acquires the exclusive bounded barrier before any SQLite transaction, reproves the target
and current source/mirror/repair state, then compares the approved digest and both expected
revisions. A prior fresh check neither reserves this state nor authorizes later deletion.

The intent and request binding commit durably in the floor before main acknowledgment or owner
preparation. This is the operation's linearization point. Main/owner failure leaves a durable
pending request and cannot roll the floor back to allow. No distributed transaction is used.
Unknown commit outcome is resolved by the original request ID. Reusing that ID with a different
scope, target, digest, revision or policy is a conflict.

Explicit resume verifies the original floor receipt and privately reassesses every floor intent
needed to acknowledge the entire current head. Public results remain restricted to the selected
proven root. Its metadata writes are idempotent; it does not rewrite content or numerical state.
Persisted preparation outcomes must be checked against current owner policy, schema and retention
digest. They are not authority to skip a failed/missing owner or execute a destructive operation.

Preparation is append-only per request. A changed semantic retention assessment creates a new
revision without overwriting the original record. A repeated identical assessment is a no-op;
prepared_at is audit metadata and is excluded from its semantic digest. Representation constraints
keep physical readiness blocked even when preparation has been reported successfully.

The original whole-store inventory digest naturally becomes stale as control metadata is
appended. Resume therefore preserves the original immutable review binding while producing a
fresh preparation assessment. It does not reuse the old digest as a permanent execution token.

If a future executor becomes available, it requires a new explicitly approved actionable plan
revision. Changed schema/body representation, additional dependencies or broader collateral
redaction are fresh review scope. The terminal root fence remains; future approval is not forged.

## Assessment resource limits

Private resume proves all approved canonical root bindings before one consistent read
transaction/content census and computes deterministic routes for each root from that snapshot.
No public all-roots API or persisted executable selectors are introduced.

Existing limits remain 100,000 logical rows and 64 MiB logical input. The full-head assessment
budget is 1,000,000 units, measured as scanned_rows multiplied by proposed head size.
Preview/fresh commit refuse an over-budget proposed head before appending to the floor.
Resume checks the current head against current store size; later growth may leave preparation
pending. It cannot shrink the reviewed scope, approximate the result or acknowledge only a subset
of the floor head to pass the budget.

A post-commit resource/storage failure preserves the terminal intent and returns pending metadata.
Unexpected port reason text is replaced with a safe generic failure code, retaining the truthful
durable receipt without rendering private content. These are deterministic work/memory bounds,
not a measured latency guarantee, physical erasure result or storage-cleanup budget.

## Owner outcomes and protected runtime

The preparation coordinator obtains explicit assessments from existing owners. Each owner reports
bounded counts, content surfaces, retained metadata/numerical history, uncertainty and representation
constraints. No executable selectors or arbitrary SQL are stored. No coordinator becomes a second
aggregate writer.

Results separate:

- Durable policy commitment and exact request binding.
- Current or stale main acknowledgment.
- Each owner's preparation, blocked or failed outcome.
- Physical readiness: blocked.
- Physical execution/erasure: false.
- External retention surfaces: not assessed.

Ordinary protected runtime denies a stale erasure acknowledgment. Startup, restore and provider
dispatch cannot silently run preparation to make it current. After explicit resume acknowledges
the surviving floor, unrelated runtime may proceed when existing source-admission/repair
requirements hold. Blocked physical readiness does not mean permanent global runtime shutdown:
the source is already withheld, and its unresolved retained bytes remain visible in the report.

## Required owner/body representations

Current nonblank bodies and provenance FKs prevent honest generic deletion. A future physical
step requires owner-controlled retained/erased body state, nullable or separate body storage,
retained reference skeletons and a historical metadata proof distinct from live quotation proof.
Substitute text cannot masquerade as the original canonical message, evidence or claim.

Historical numeric state, confidence/revisions, checkpoints, consumed budgets, replay protection
and required evidence/FKs retain their existing meaning. Erasure is not new personality or
relationship evidence, numerical rollback, reactivation or a cap refund.

Classification follows content semantics. A numeric model value can encode private source
content. The position normalized_signature is a normalized textual copy. Permanent inclination
keys and retained source-derived hashes need explicit replay/retention decisions; their retention
cannot support a claim of anonymity or complete removal of personal information.

Opaque audit/appraisal JSON and free-form approval/restore reasons remain blocked. A future
owner contract needs strict retained envelopes and provenance-aware content classification.
Unrelated counterparty content is not approved collateral scope: Bob's unproved audit text
cannot be silently removed as part of Alice's intent. Any conservative broader redaction needs
its own visible scope and actionable-plan approval.

## Recovery and limits

[Whole-state recovery](recovery.md) remains explicit offline administrative byte transport under
ADR-0053. Export/restore retains the supported stored data, including pending-intent content.
It does not filter text, sanitize archives, certify erasure, select a weaker floor or auto-prepare
owners. Operational use of an old restored main requires current trusted composite acknowledgment
through explicit reconciliation/resume.

The guarantee requires the same surviving trusted installation. Full floor/installation rollback,
malicious same-user writes, cross-machine latest-floor recovery and authenticated operator
authority remain separate access/key/device prerequisites. Encryption or a valid signature does
not by itself prove that a restored floor is current.

WAL/SHM/journals, free pages, staging, logs/provider caches, OS snapshots, exports, external copies
and previously disclosed content remain unassessed retention surfaces. This slice changes none
of their content and does not issue a physical certificate. The Linux process-lock gate remains
open until actual Linux execution; macOS verification cannot close it.

## Verification

Required engineering evidence includes migration compatibility and fixed-inode preservation;
strict scope/digest/request/composite-revision conflicts; floor-first failure and unknown-outcome
replay; old-main restore admission and explicit resume; bounded honest UNKNOWN/opaque owner
outcomes; unchanged content/vectors/numerical history/evidence/FKs; and an actual provider-free
CLI/restart/recovery scenario. The frozen rebuilt Foundation toolchain must pass.

The 2d macOS engineering gate is complete. Exact runtime status, gate outcomes and
remaining blockers are recorded in [progress](progress.md), the
CLI/recovery drill, and the
local cost probe.


## Subsequent bounded body handling

[ADR-0061 / 14.4.2e](erasure-bodies.md) adds a separately reviewed action for episodic summary/
quotation bodies and all selected index spaces. Existing intent controls remain metadata-only.
The new independent body revision closes restoration of an after-intent/before-body archive;
intent acknowledgment and source repair cannot acknowledge physical owner materialization.
An intent or a current blocked preparation still grants no body execution authority.
