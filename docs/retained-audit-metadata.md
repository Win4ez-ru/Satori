# Retained audit metadata

Status: Foundations 14.4.2h macOS engineering gate verified under
[ADR-0071](decisions/0071-owner-bound-retained-audit-metadata.md).

This layer establishes whether an audit record can be retained as supported structural/numerical
accountability metadata. It never infers that arbitrary JSON is safe from its event or field names.
It does not erase data, authorize an executor or supply new evidence to cognition or Character.

## Contract and ownership

The existing owner transaction atomically records its owner receipt, audit and prospective
`audit_metadata_bindings` row. A shared typed binding writer is the sole binding append path;
authors and state owners stay unchanged. Main `0023_audit_metadata` adds the companion table,
leaving `audit_events`, old Appraisal material hashes and the five-action floor untouched.

Supported non-Appraisal families are activation, episode formation/lifecycle, semantic formation,
user/world model formation, position formation, four Reflection outcome families, relationship
decisions, checkpoint approval/restore and source withdrawal. Each binds an exact declared owner
receipt and identity. Missing or unsupported binding remains UNKNOWN. No migration/backfill repairs
legacy trace/input links or assigns operator reasons to a canonical source.
The optional actual-input link is checked when explicitly supplied; its absence never certifies
complete causal attribution. Closed metadata can still be proved through its prospective binding
and exact owner/canonical scope, without granting fresh-learning or deletion authority.

The reader checks closed keys/versions/codes, bounded strict types and collections, exact owner
headers and receipt values, scope and cardinality. A canonical event digest is an additional check,
not a substitute for owner proof. Raw duplicate keys, extra prose, unknown codes, mismatched scope,
duplicate logical records and altered owner material cannot receive a metadata badge.

## Three-way partition

Every audit is classified exactly once as supported retained metadata, proved owned content or
UNKNOWN. Available Appraisal explanations remain owned content under EmotionManager's existing
pair contract. Terminal Appraisal metadata requires the approved mixed head, exact three-row
material and complete immutable input-closure proof. Its raw audit bytes/headers are not rewritten
by this layer.

Inventory removes the blanket audit blocker only for the proved subset. UNKNOWN rows remain
unchanged with a completeness blocker. The new schema/store/routing fingerprints invalidate stale
review while historical preparation receipts remain readable and immutable.

## Operator status

```bash
satori sources audit-metadata MESSAGE_ID --status
```

This branch proves the configured counterparty's canonical root before private census, reads one
consistent main-store snapshot and emits family counts, coverage and closed safe reason codes.
The root is the admission scope; the census is installation-wide current-main coverage, as the
`inventory_scope=current_main_database` field states. Counts are not a root-only audit subset.
It never emits raw unknown event names, selectors, JSON or explanation text, and constructs no
cognitive providers. It performs no implicit migration or enrollment. Coverage does not grant
conversation permissions; normal source and mixed-head barriers remain authoritative.
Coverage is complete only when UNKNOWN events and UNKNOWN binding rows are both zero.
Proved owned content may remain; complete classification does not imply complete erasure readiness.

Explicit migration and trusted-floor reconciliation are administrative prerequisites. An old
archive retains both its captured bytes and absent legacy bindings. It cannot operate across a
pending body head until existing explicit resume proves the actual state.

## Bounds and remaining work

Use the existing 100,000-row and 64 MiB inventory bounds, JSON byte/depth/node limits and one
indexed census. A conservative classifier-local one-million-work budget charges cached
per-identity owner proof and receipt/group traversal before work. Capacity exhaustion fails closed;
this does not prove linear time or large-store readiness. No per-event whole-store scan,
protected-content cache or LLM call is added. SQL guards protect immutable bindings and bound
non-Appraisal audits, including REPLACE with recursive triggers disabled.

Freeform checkpoint approval/restore reasons are separate retained bodies: their identity/checkpoint
references cannot attribute them to the erased USER root or counterparty. Unproved scalar reason
codes remain residuals. Numerical influence, sensitive identifiers/hashes, SQLite free pages/WAL,
archives/exported/OS/provider copies, full-floor rollback, local access/encryption/key recovery,
Linux and whole 14.4 acceptance remain open. Every result reports `physical_erasure=false` and
`execution_authorized=false`.

## Verification

The full isolated Foundation passes **4659 tests /
5 skips**, Ruff/mypy, fresh migration/bootstrap and installed/source parity. The
real-owner collector observes all fifteen
non-Appraisal families across 164 passing tests. The audit scenario
passes 12/12 and Appraisal downstream regression
passes 20/20 on the final snapshot. Foreign scope, inherited/uncited input roots, extra prose,
duplicate/forged material, strict JSON/work bounds and immutable SQL guards have relevant tests.

The initial full failure is preserved; only
obsolete fixtures and the uncollected scenario's missing leases were repaired. Production and
migrations are byte-identical, both full runs collect 4664. The current Character work is preserved
and excluded from behavioral acceptance. No live-installation erasure or full privacy readiness is
claimed. Local cost measures 50 fresh-proof warm status
reads: median/p95 27.932/28.380 ms on 203 synthetic rows, zero control cognitive calls and unchanged
main/floor/binding/lock material. Host load and scale behavior remain unproved.
