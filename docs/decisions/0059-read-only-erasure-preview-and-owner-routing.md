# ADR-0059: Read-only erasure preview and owner routing

- Status: Accepted — read-only runtime implemented; macOS engineering verification passed
- Date: 2026-10-03
- Authority: explicit continuation of the Foundations 14.4 production workstream
- Related: ADR-0053, ADR-0055, ADR-0056, ADR-0057
- Supersedes: no existing owner, exclusion, recovery or Character policy

## Context

ADR-0057 implements logical canonical-source exclusion through an independent installation
floor, complete actual-input receipts, disclosure admission and owner-routed support withdrawal.
It retains canonical text, evidence, revisions, historical numeric state and audit. Restoring an
older main database cannot revoke a surviving trusted floor's exclusions. Neither this guarantee
nor a completed exclusion receipt proves that stored content was physically erased.

The source dependency DAG describes inputs to managed payloads. It is not a complete retention
or foreign-key graph: exact quotes, historical revisions, vectors, appraisal/reflection JSON,
audit and metadata may still refer to a source without being a current disclosed payload. SQLite
free pages, journals, archives, provider caches and external copies are separate retention surfaces.
The current schemas also require retained canonical references through RESTRICT foreign keys.

A production erasure control needs an honest, scoped assessment before any destructive operation.
The next authorized slice delivers this assessment and owner routing. It does not implement a
physical executor, erase data, or create a new persistent erasure-intent writer.

## Decision

### Product boundary

Open a bounded **read-only erasure preview and fresh check** within Checkpoint 14.4. A local
operator selects one existing, proven, already excluded canonical USER root. The result describes
the relevant local stored records, their owners, current versions and future handling requirements.
It contains no conversation, quotation, summary, model value, proposition, prompt or appraisal text.

A fresh check recomputes the same assessment and compares its digest. A matching result means
only that the reviewed bounded assessment still matches the inspected logical state. A matching
blocked assessment remains blocked. It is not a
permission, reservation, authenticated approval, physical erasure certificate or execution permit.
There is no apply, delete, purge, redaction, compaction or erasure-intent commit in this slice.

Assessment coverage and physical readiness are independent. A complete bounded local assessment
can be reviewable while physical readiness remains **blocked**, always including
`physical_erasure_executor_not_implemented`. Attribution, policy and repair prerequisites deny
without a protected inventory. Missing provenance or owner coverage produces an explicit blocked
assessment. Partial enumeration never becomes a complete plan; capacity/schema failures cannot
return a truncated apparently complete inventory.

### Proof and consistency

1. Prove the selected canonical USER root and exact identity/counterparty/session/interaction
   ownership before exposing protected inventory, IDs, counts or policy details. A foreign,
   quarantined or unavailable target receives a content-free denial. Supported interaction
   lifecycle is COMPLETED; pending or failed interactions do not receive completed-source coverage.
2. Require the existing trusted installation/floor/lineage binding and terminal exclusion of the
   selected root. Do not implicitly enroll a floor or exclude a source to make preview succeed.
3. Acquire the existing bounded source-policy barrier before any SQLite transaction. Verify the
   current exclusion floor, exact main mirror and required owner-repair watermarks. Read the local
   inventory in one consistent SQLite read transaction. Repair lag blocks assessment; inspection
   does not reconcile the mirror or mutate an owner.
4. Release the barrier/transaction before operator review. Fresh check repeats every proof and
   inventory read. It never holds a lease between commands and never spans provider work.

The commands have zero provider calls and no canonical, ledger, receipt, audit, migration,
initialization, filesystem-publication or owner-repair writes. Read-only planning uses explicit
stored-state ports, not owner status calls that initialize or decay state. Operational settings
cannot provide an unmanaged bypass for a bound database.

The initial read-only opener supports existing regular rollback-mode SQLite files only. It checks
the main header and rejects WAL mode or any WAL/SHM/journal sidecar before opening SQLite.
`mode=ro` alone can create SHM; `immutable=1` would silently ignore committed WAL content. Neither
behavior is acceptable for a write-free current-store assessment. WAL support needs a later
explicit snapshot contract rather than an implicit file write or stale inventory.

### Typed inventory and owner routes

Use strict immutable application contracts and a deterministic domain plan:

- `ErasurePreviewRequest(message_id, counterparty_id)` selects one scoped root.
- `ErasureCheckRequest` adds `expected_generation` and `preview_digest`.
- `ErasureInventory` binds root, schema revision/digest, combined logical store digest, exact internal
  row-to-route digest, routes,
  scanned-row count, affected/uncertain payload-version counts and assessment blockers.
- `ErasureRoute` exposes only owner, table, disposition, row count, content-column names and
  uncertain-row count. No descendant row IDs or protected values are returned.
- `ErasurePreview` separates `assessment_status=complete|blocked`,
  `physical_readiness=blocked`, `external_retention=not_assessed` and explicit blockers.
- `ErasureCheckResult` reports a current matching assessment with its complete/blocked status;
  `physical_erasure=False` and `execution_authorized=False` are invariant.

`ErasurePlanner.preview(request)` and `.check(request)` consume an immutable inventory port and
the existing source-policy admission port. A plan binds the
canonical root, installation/floor/lineage, current source-policy generation, supported schema,
plan/routing policy versions, complete bounded local inventory and current assessment reasons.
Internal records/owner versions and non-versioned values are covered by the combined store digest.
Public routes group records by owner/table and future disposition. Counters identify uncertainty;
truncated lower bounds cannot stand in for an exact plan. The initial supported schema is exact
`0016_source_policy`, with limits of 100,000 total logical rows and 64 MiB logical input.

CLI is `sources erasure ROOT --preview`, or
`sources erasure ROOT --check --expected-generation GEN --sha256 DIGEST`, in the configured
counterparty scope. These commands have no apply option.

| Retention family | Required route and honest current effect |
|---|---|
| Canonical USER/paired assistant and interaction/session references | InteractionLog owns future text handling; retain all text/references now; include known downstream assistant dependencies |
| Episode summaries, exact evidence and derived vectors | MemoryManager and its existing index adapter; include archived records and historical evidence, without lifecycle changes |
| Semantic/User/World knowledge, positions and their evidence/revisions | Existing knowledge owner; include retired/superseded historical records, immutable support and active-key history; no claim that exclusion disproves a fact |
| Reflection sources/proposals/outcomes and formation decisions | Existing reflection/formation owners; include stored JSON and upstream links; no replay or new proposal |
| Affect, relationship, inclination and personality history | Existing owner; retain numeric trajectory, aggregate identity, caps and historical references; separately identify text-bearing/structured context that needs later owner policy |
| Audit, input/payload receipts, withdrawal/lifecycle metadata and signatures | Retain provenance metadata now; explicitly include linked records and residual identifiers/hashes; these are not an erasure certificate |

The planner coordinates immutable read assessments. It does not become a generic aggregate writer
or delete through cascading foreign keys. A future owner executor must resolve text-versus-reference
retention, schema constraints and dependencies through that owner's typed policy before mutation.
No personality/affect/relationship arithmetic, score refund, cap reset, Character renderer or
provider-specific prompt is part of planning.

Unknown dependencies remain unknown. Legacy accepted citations do not reconstruct uncited actual
inputs; no missing edge, unrecognized payload/owner, cycle or overflow is promoted to COMPLETE.
Conservative potentially affected records may be reported as such, without pretending they are
exact descendants or exposing unrelated counterparties' content. Owner routing distinguishes
known retained records from unresolved scope/lineage and unsupported future actions.

### Digest and freshness

The digest is separate from ADR-0057 exclusion preview and from an archive checksum. It binds:

- Exact target/scope, installation/floor/lineage and source-policy generation/version.
- Schema and plan/owner-routing adapter versions.
- Deterministically ordered complete logical inventory, classification, and one combined
  fingerprint of the entire logical store, including owner versions, non-versioned values and
  dependencies.
- A separate opaque routing fingerprint binds exact internal primary keys and their owner/
  disposition/uncertainty assignments, so equal public counts cannot hide a changed selected set.
- Coverage, retained effects, physical blockers and the explicitly unassessed retention surfaces.

Counts or current aggregate versions alone cannot bind old quotes, append-only evidence, revisions,
non-versioned JSON or added dependent rows. Canonical fingerprint encoding preserves exact stored
values; it does not trim or normalize source text into equivalent wording. Fingerprints are not
sender authentication or proof of completeness. Protected values and row-level content hashes
are not rendered as operator content.

The whole-store binding is conservative: even an unrelated logical write invalidates the prior
review, rather than guessing that it has no retention dependency. A capacity error never degrades
to an approximate digest.

Any changed stored row, target proof, generation, schema, routing version, repair state or coverage
invalidates the reviewed digest. A fresh match applies only at that read snapshot; another write
after the check can make it stale. A future executor must reprove and compare under its own final
mutation lease rather than accepting a previous check result as authority.

### Recovery floor and future tombstone prerequisite

This slice keeps ADR-0053 offline restore byte-preserving and ADR-0057 operational admission
unchanged. An old main backup can restore retained text offline. Before ordinary provider/context/
job use, the surviving independent floor still excludes the root and requires current mirror/owner
repair. Preview/check neither redacts an archive nor rewrites restored rows. Missing or replaced
enrolled floor cannot create an empty replacement. Full installation/floor rollback and
cross-machine freshness remain unproved without a separate trusted anchor/key recovery contract.

Before a later physical executor's **first content mutation**, the SourcePolicyManager must commit
a terminal, metadata-only **ErasureIntent/tombstone** to the existing independent trusted floor.
It must bind the root/scope, approved exact plan digest, policy versions, durable request identity,
expected floor state and explicit retained-data policy. Its future receipt records policy commitment
and independently resumable owner outcomes, never physical completion by implication.

That future change needs an independently versioned policy migration, reviewed recovery admission
and fault/replay tests. The floor remains authoritative after an owner/main failure and cannot
roll back to allow. Lock ordering remains barrier before floor/main transactions; no distributed
transaction or inference-time lease is introduced. Unknown commit outcome is resolved by the
original durable request binding, rather than a new guessed request. An older restored main cannot
select or downgrade tombstone policy, auto-run deletion, or certify that earlier content was erased.

No new tombstone table, erasure generation, intent receipt or commit operation is implemented by
this read-only slice. Existing source exclusions are the current logical recovery fence; they
must never be labeled erasure completion.

### Retention limits and blocked readiness

The exact inventory is limited to supported live logical records and proved dependencies in the
selected database. It does not scan the filesystem or claim a whole-installation inventory.
SQLite WAL/SHM/journal files, free pages, rollback snapshots, private staging, existing full/partial
exports and receipts, application/provider logs or caches, external copies and in-flight disclosures
are explicitly unassessed retention surfaces. They are never reported absent merely because no
record is present in the planner's registry.

Future physical readiness additionally requires owner-specific retention/redaction policies,
FK-safe representation, a physical storage cleanup protocol, controlled artifact inventory,
access/key prerequisites and truthful external-copy limits. IDs, signatures and content hashes
remain potentially sensitive residual metadata. Private 0600 files and the `local_operator` label
are not authentication, encryption or authority over arbitrary files.

## Alternatives

| Alternative | Reason rejected |
|---|---|
| Rename source exclusion to erasure | Logical withholding retains bytes and cannot prove deletion |
| Inventory only eligible current payloads/citations | Omits withheld/archive/history/evidence and uncited inputs |
| Generic FK cascade or scrubber | Violates owner policy, destroys provenance/numeric history and may miss structured content |
| Persist plans or intents before an executor contract | Adds an unused authority and ambiguous completion/recovery states |
| Treat fresh check as a signed deletion permit | No access authority or reservation exists; relevant state can change after the read |
| Put future tombstone only in main DB/export | Older recovery removes the later privacy floor |
| Scan/delete arbitrary backups or provider caches now | Surface ownership and reliable inventory are unproved and outside this authorized slice |

## Acceptance

Required tests prove positive scoped preview and unchanged freshness check, exact route coverage,
raw paired/history/archived/revision/quote/vector/JSON inclusion, retained numeric state and audit,
foreign/unproven denials before inventory disclosure, exclusion/current-mirror/owner-repair gates,
UNKNOWN/missing/cycle/overflow/unknown owner/schema blocked results or safe denials, and bounded
inventory behavior.

Digest regressions mutate versioned and non-versioned records, add dependencies/history and change
floor/repair/target state; each makes the old review stale without accepting a partial match.
Failures and repeated calls leave canonical/floor logical state unchanged and invoke
zero providers. Synthetic CLI/restart/pre-exclusion backup checks verify operational recovery
fencing while offline raw bytes remain retained. Complete frozen Foundation checks and current
documentation are required; exact results belong in `progress.md`.

This gate accepts read-only planning/checking only. It does not close Linux process-lock
verification, physical erasure, local authenticated access, encryption/key recovery, whole
Checkpoint 14.4, Character acceptance or a later Stage.

Implementation and verification evidence: 68 focused cases and the synthetic actual CLI/recovery
scenario passed; frozen Foundation reports 3836 passed / 5 skipped, with format/lint/types,
migration and bootstrap clean. Exact snapshot, timings, skips and remaining gates are recorded in
[progress](../progress.md) and [erasure](../erasure.md). A normal stored fixture remains honestly
blocked on opaque owner content, while its metadata inventory/freshness checks remain usable.
