# ADR-0060: Durable erasure intent and owner preparation

- Status: Accepted — authorized scope contract; implementation in progress
- Date: 2026-10-04
- Authority: explicit continuation of the Foundations production workstream into the proposed
  durable floor-first erasure intent, owner retention and resumable handling direction
- Related: ADR-0053, ADR-0055, ADR-0056, ADR-0057, ADR-0059
- Supersedes: exact schema-admission limits only, extending ADR-0053 recovery to main
  0017_erasure_intent and ADR-0059 inventory to exact 0016/0017; explicit policy_0002 extends the
  ADR-0057 floor. Owner, exclusion, byte-preserving recovery and Character semantics remain.

## Context

Checkpoint 14.4.2c implements a bounded read-only erasure inventory and freshness check.
ADR-0057 implements terminal source exclusion outside the main database. Neither operation
records a durable request for future physical handling, prepares all owners' retention contracts,
or certifies removal of content. A normal stored inventory can remain blocked by opaque audit
and appraisal content or incomplete legacy provenance.

Current owner representations require canonical text, summaries, quotes, model values,
propositions and JSON bodies. RESTRICT foreign keys preserve references used by numerical
history, evidence, replay protection and audit. Generic deletion, empty strings or substitute
text would either violate the schema or fabricate a historical domain representation.

The next production capability must preserve a terminal privacy request across an old-main
restore without pretending that this request authorizes an unimplemented destructive executor.
The required owner/body representations are an explicit prerequisite for later physical work.

## Decision

### 1. Product boundary

Open **14.4.2d — Durable Erasure Intent & Owner Preparation**. The slice implements:

1. A reviewed, durable, metadata-only ErasureIntent in the existing independent policy floor.
2. A composite source-generation/erasure-revision admission fence for a restored main database.
3. Explicit reconciliation and resumable owner preparation with durable metadata outcomes.
4. Scoped preview, commit, resume and status controls that invoke zero providers.

There is no content deletion, redaction, vector removal, file cleanup, compaction, VACUUM,
physical executor or physical completion certificate. Physical execution remains denied.
Real-data erasure remains closed. No Character expression, prompts, behavior experiments,
personality evolution or numerical self/relationship policy changes are introduced.

An existing blocked inventory may be explicitly reviewed and bound to an intent. Its digest
records what was inspected, including UNKNOWN/opaque blockers and retained surfaces. It is
not an executable plan, and committing the intent does not turn assessment coverage into
complete coverage or erase its blockers.

### 2. Typed contracts and approval meaning

The application boundary uses immutable, bounded typed requests and outcomes.

| Contract | Required meaning |
|---|---|
| ErasureIntentProposal | Exact canonical root/scope, trusted installation binding, durable request ID, expected source generation and erasure revision, reviewed inventory digest and separate intent approval digest, schema/routing/retention policy versions |
| ErasureIntent | Immutable terminal request and original approval binding; metadata-only floor receipt; no deletion permit |
| OwnerRetentionAssessment | Explicit owner, bounded counts and representation constraints, retained metadata/numerical policy, content surfaces, uncertainty and readiness reasons |
| OwnerPreparationReceipt | Intent/owner/policy binding, assessed snapshot digest, prepared/blocked/failed outcome and audit metadata; no source text |
| ErasurePreparationResult | Separate floor commitment, main acknowledgment, owner outcomes and denied physical readiness |

The inventory's opaque routing digest binds inspected references, versions and dispositions,
not counts alone. This slice persists no executable plan or batch selectors. Preparation records
contain bounded owner counts, policy/representation constraints and digests. Their counts and
encoded size are bounded before publication. Future private action selectors must use allowlisted
owner references, not arbitrary SQL or operator-supplied table/column programs. Neither private
references nor source-derived text are rendered in CLI receipts.

The reviewed_preview_digest identifies the existing 14.4.2c inventory. A separate approval_digest
binds the intent envelope, including expected composite floor state, scope and retention policy.
The commit SHA-256 argument approves this envelope; it does not substitute an archive checksum,
source-exclusion approval or physical action-plan digest.

The operator label is local metadata approval under the current unauthenticated installation
model. It is not authentication, a scoped access grant or authority inferred from relationship.
Initial commit revalidates the exact reviewed digest and expected composite floor state at its
own mutation boundary. A prior fresh check is not a reservation.

A later physical executor requires a separately approved, append-only actionable plan revision
with supported owner/body representations and fresh policy/schema/closure proof. The original
blocked inventory cannot approve a changed plan, broader collateral scope or future destructive
operation. Its terminal root fence survives, while readiness remains independently evaluated.

### 3. One floor owner and two revisions

SourcePolicyManager remains the sole owner of the independent floor. Erasure intents extend its
existing installation binding and independently versioned Alembic policy migration stream.
They are not stored authoritatively in the main database, an export or a second privacy ledger.

Source generation keeps its ADR-0057 meaning: terminal source exclusions. Introduce a separate
monotonic **erasure_revision** for intent commitments. Admission compares both values, together
with installation/floor/lineage/identity binding. A new intent does not consume or rewrite source
generation, manufacture an exclusion, or alter historical exclusion receipts.

Support the existing policy_0001 floor for existing source-exclusion operations. Intent controls
require explicit reviewed upgrade to policy_0002. Preview/status/runtime cannot implicitly
upgrade, initialize or replace the floor. The upgrade preserves installation and lineage IDs,
existing exclusions/receipts and the fixed ledger/barrier inode binding. Unknown schema,
missing/replaced enrolled files and failed verification deny. Downgrade cannot discard intents
or their nonzero revision.

The main database stores only a derived intent acknowledgment/binding and owner preparation
metadata. They cannot weaken the independent floor. Erasure acknowledgment is distinct from
the five existing source-owner repair watermarks: semantic_memory, current_models, positions,
reflection_backlog and relationship_backlog. A current source watermark is neither intent
acknowledgment nor preparation evidence; preparation is not source-withdrawal completion.

Main 0017_erasure_intent has a pinned exact trusted schema and new metadata-only tables.
Read-only 14.4.2c inventory admits exact 0016_source_policy and 0017_erasure_intent. Intent
preparation requires 0017; unsupported revisions or schemas deny rather than auto-migrate.
Recovery admits 0017 alongside its separately validated supported predecessors and remains
byte-preserving. This schema extension is the only replacement of earlier admission limits.

### 4. Floor-first sequencing, replay and crash handling

Lock order remains the ADR-0057 barrier before every floor/main transaction. No distributed
transaction is added and no lease is held during inference.

1. Acquire the exclusive bounded cross-process barrier.
2. Reprove the target's canonical USER/completed/scope attribution, existing exclusion, current
   source mirror/required repair, supported inventory and exact review/composite revision.
3. Commit intent, revision and original request binding durably in one floor transaction.
4. Acknowledge the floor in the main derived projection.
5. Prepare each owner through its own bounded retention contract and commit metadata outcomes.

The terminal intent linearizes at step 3. A later main or owner failure never removes the intent,
decreases the revision or restores allow policy. Unknown floor commit outcome is resolved through
the original durable request ID before proceeding; a new guessed request is not a retry.
Reusing a request ID with a different root, scope, expected state, digest or policy is a conflict.

Resume first resolves and verifies the original intent binding. It explicitly reconciles a stale
main acknowledgment and privately reconstructs preparation for all floor intents needed to
acknowledge the entire current head. Public output remains restricted to the selected proven root.
Existing receipts are evidence to inspect, not permission to skip current owner/version checks.
Preparation retries are idempotent and do not mutate owner payloads, historical numerical state,
evidence or grants.

Owner preparation revisions are append-only per intent/request when schema, retention digest or
owner policy changes. They do not overwrite the original record or the floor approval. An identical
semantic assessment is a no-op. Preparation timestamps are audit metadata and do not change its
semantic digest. A blocked representation remains blocked in every matching result.

Private full-head assessment proves every approved canonical root before one consistent read
transaction/content census, then derives deterministic per-root routes from that shared snapshot.
It is not a public all-roots inspection API, and no request selectors are persisted.
The existing 100,000 logical-row and 64 MiB logical-input caps remain. An additional deterministic
work budget limits scanned_rows multiplied by proposed head size to 1,000,000. Preview and fresh
commit deny over-budget work before floor append. Resume applies the same budget to the current
head; later store growth can leave a durable intent pending without partial acknowledgment or
an approximate assessment. This bounds repeated per-root dependency/routing work, not wall-clock
latency or physical cleanup.

Main preparation records, and the original preview's whole-store fingerprint, necessarily change
as control metadata is appended. Resume therefore validates the immutable intent approval plus
a freshly assessed preparation snapshot; it does not require the old whole-store digest to remain
unchanged forever or silently reinterpret it as an executable plan.

### 5. Runtime and recovery admission

Ordinary protected provider/context/job operations deny when the main erasure acknowledgment
lags the trusted floor, including after restoration of an older main database. They do not
auto-prepare owners or repair privacy state. The operator must explicitly resume/reconcile.

After acknowledgment is current and existing source withholding/repair requirements hold,
unrelated protected runtime may proceed. An owner blocked on physical readiness does not
permanently block unrelated conversation: the root is already excluded by ADR-0057.
Blocked preparation still denies physical execution and must remain visible in status.

ADR-0053 offline export/restore remains an explicit administrative byte-transport operation.
It retains supported canonical stored bytes, including text requested for later erasure. It does
not omit retained content, sanitize the archive, certify erasure, weaken the floor or auto-run
owner preparation. Operational admission of the restored store requires the surviving trusted
composite floor and explicit reconciliation. A raw offline archive is still sensitive.

### 6. Owner retention and physical prerequisites

Each persistent state keeps its existing owner and write path. The preparation coordinator
collects typed retention assessments; it does not become a generic SQL content writer.

Before a future physical step, the relevant owner must implement explicit retained/erased body
state and nullable or separate body storage. Immutable reference rows and historical metadata
proof must remain distinguishable from available quotation/content evidence. Erased data cannot
be reconstructed as a normal historical domain message or claim using substitute wording.

Numerical state, confidence, revisions, checkpoint lineage, consumed caps, replay/idempotency
records and required evidence/FK skeletons are retained under an explicit versioned policy.
Deletion is not new self evidence, trait evolution, relationship evidence or a budget refund.

Representation classification must consider actual semantics:

- A numerical model value can be source content; its numeric type does not make it safe history.
- The position normalized_signature is a normalized content copy and is a content surface.
- Permanent inclination keys require a reviewed retention/replay policy before body removal.
- Identifiers and signatures/hashes retained for provenance remain disclosed retention limits;
  this slice does not claim anonymity or total removal of personal information.

Opaque audit/appraisal JSON and free-form approval/restore reasons remain owner blockers.
Future preparation requires strict typed retained envelopes and content/provenance classification.
Arbitrary nested strings do not become metadata through field naming or a JSON shape check.
Erasing unproved content belonging to another counterparty, such as Bob's unrelated audit
details, is collateral scope that this intent does not approve. A future conservative broader
redaction policy needs its own visible scope and explicit actionable-plan approval.

### 7. Controls and public output

The command surface is:

~~~text
sources erasure-policy --upgrade --reviewed
sources erasure-intent ROOT --preview
sources erasure-intent ROOT --commit --expected-generation G --expected-erasure-revision R --sha256 DIGEST --request-id ID
sources erasure-intent ROOT --resume --request-id ID
sources erasure-intent ROOT --status --request-id ID
~~~

Preview and status are read-only and do not create directories/databases, change permissions,
migrate, enroll, repair owners or initialize domain state. Commit, policy upgrade and resume have
only their explicit metadata mutation scope. All controls invoke zero providers.

All target/receipt operations prove the configured canonical scope before request lookup or
returning a digest/outcome. Wrong identity, lineage or counterparty never exposes another
intent's existence, private selectors, descendant IDs, counts or text. Public error reasons and
receipts contain bounded metadata without prompts, quotations, raw audit JSON or exception text.

### 8. Limits and acceptance gate

The guarantee is limited to the same surviving trusted installation floor. Restoring the whole
floor/installation, a malicious same-user writer, cross-machine freshness or authenticated
operator authority requires later access/key/device work. A signature or encryption key alone
does not prove that a floor is the latest one.

WAL/journals/free pages, exports, staging, OS snapshots, provider caches/logs, external copies and
previous disclosure remain separate retention surfaces. No physical bytes or external copies
are changed or certified in this slice. Linux process-lock verification remains open until
actually exercised; a macOS gate does not close it.

Required verification:

- Exact policy_0001 compatibility and explicit policy_0002 upgrade preserving fixed bindings,
  exclusions, request receipts and revisions; unsafe downgrade/missing-floor refusal.
- Fresh preview/commit scope, request, digest, schema, policy and composite revision conflicts.
- Floor failure before commitment leaves main/content unchanged; unknown outcome and crashes
  after floor commitment preserve the intent and support explicit replay/resume.
- Full-head work bounds deny new commitments before floor append; later over-budget resume
  preserves the intent and returns truthful pending metadata. Unsafe port error text is replaced
  by a safe generic reason without losing the durable receipt.
- Old-main restore denies ordinary protected runtime until current acknowledgment; explicit
  resume restores unrelated admission without claiming physical readiness.
- Complete and UNKNOWN/opaque inventories remain honest; preparation is bounded, owner-routed
  and idempotent, with append-only drift revisions and timestamp-independent semantic digests;
  failure/blocked outcomes cannot become execution permission.
- Content, vectors, numerical history, caps, evidence/FKs and Character files remain unchanged.
- Actual CLI/restart/recovery scenarios invoke zero providers and render no protected text or
  descendant references; Foundation toolchain passes on a frozen rebuilt package.

Implementation and engineering evidence belong in progress.md and erasure-intent.md.
Acceptance of this bounded capability does not accept physical erasure or all of Checkpoint 14.4.
