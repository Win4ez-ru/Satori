# ADR-0071: Owner-bound Retained Audit Metadata

- Status: Accepted; implemented and macOS engineering gate verified in the explicitly continued 14.4.2h vertical
- Date: 2026-10-13
- Authority: explicit user continuation after the completed Appraisal explanation vertical
- Related: ADR-0053/0057/0059/0060/0063/0066/0068/0070
- Supersedes: only the blanket audit-JSON retention classification in ADR-0059, where a supported
  exact owner contract is now proved. Existing source permissions and body-action contracts remain.

## Context

The retained-data inventory currently marks every audit `details` value both opaque content and
retained metadata. This cannot establish which values are safe to retain. A familiar event name,
an ID-shaped field, a deterministic current writer or a successful JSON parse cannot certify
historical arbitrary JSON. There are fifteen writer sites and sixteen event families, including
the separately controlled Appraisal explanation pair.

Adding contract columns to `audit_events` would change the raw audit row material bound by existing
Appraisal actions. Rewriting old audits, guessing their writer or inferring trace/input links from
timestamps would violate approved hashes and the no-backfill rule.

## Decision

### Prospective bindings through existing owners

Main `0023_audit_metadata` adds only a companion `audit_metadata_bindings` table with an event FK,
supported contract version, closed family discriminator, explicit identity scope, exact owner
receipt FK slot, canonical event fingerprint and an optional explicitly supplied source-input FK.
It does not add columns to `audit_events`, change its stored rows, rewrite old actions or introduce
a new floor revision, content graph, source-generation counter or erasure action.

Existing domain owners continue to author their own audit events. One shared infrastructure binding
writer is the sole binding append path. It records the event and binding within the same existing
owner transaction as its receipt/state change. An unsupported shape/code retains the original
audit and owner behavior but receives no metadata certification. No event is dropped to improve
coverage, and binding failure cannot commit a partial receipt. No new personality, affect,
relationship or Character owner is created.

The prospective contract covers the fifteen existing non-Appraisal families: activation, episode
formation/lifecycle, semantic formation, user/world model formation, position formation, Reflection
position/inclination/personality/disabled-owner outcomes, relationship decisions, checkpoint
approval/restore and source-support withdrawal. Bindings reference the exact declared owner record;
the reader never reconstructs a binding from event names, time adjacency or matching JSON keys.
Scope inferred through an explicit canonical owner FK is a structural join, not guessed provenance.

Legacy audits have no binding and remain UNKNOWN. Migration performs no content update or backfill.
Absent actual-input links remain absent: metadata certification does not claim complete source
ancestry, and it never turns audit output into fresh learning evidence.
An explicitly prospective binding may certify closed metadata with no source-input link after
exact owner/canonical scope proof. If a link is supplied it is checked, never inferred. This
certification does not establish source-complete attribution or authorize causal data deletion.

### Closed classification and bounded proof

The reader proves supported header/details versions, exact keys, closed code/enumeration
vocabularies, finite bounded numbers without bool coercion, bounded ID collections with uniqueness
where required by the owner contract (episode quotations may share a canonical source root)
and exact owner/header/receipt/cardinality/scope agreement. Raw JSON duplicate keys, unsupported
versions, extra prose, malformed receipts, forged scope and duplicated logical event slots remain
UNKNOWN. Event/binding hashes alone are insufficient; retained owner material is revalidated.

Bindings are immutable. SQL guards deny mutation, removal and conflicting REPLACE, including with
recursive triggers disabled; bound non-Appraisal audits are immutable as well. The Appraisal
writer and its exclusive two-copy disposition remain governed by ADR-0070, with no companion
binding inserted for its old or new pair.

Classification partitions each event exactly once: proved retained metadata, proved owned content,
or UNKNOWN. Available Appraisal reasons remain owned content. Its terminal audit is metadata only
after existing complete mixed-head, three-row and immutable input-closure proof; neither an ACK nor
key absence alone is sufficient. All old action bytes, material digests and numerical signal hashes
remain exact.

Use one indexed census within a consistent read transaction and the existing 100,000-row and
64 MiB inventory bounds. A classifier-local conservative one-million-work budget charges indexes,
groups, receipt walks and cached per-identity owner census before proof. This is bounded admission,
not a universal linear-time/scalability guarantee. Strict JSON has byte/depth/node limits.
Do not introduce per-event whole-store scans, protected-body caches, provider calls or a second
source-policy graph.

### Integration, freshness and recovery

Inventory routes retain only certified audit rows as metadata, route proved Appraisal content to
its existing owner, and retain UNKNOWN rows unchanged with an explicit completeness blocker. The
new table participates in store/schema/routing fingerprints. A changed partition invalidates fresh
review; historical preparation receipts and their version-one decoder remain unchanged.

A scoped read-only `sources audit-metadata MESSAGE_ID --status` reports closed family counts,
coverage and safe reason codes. Root/counterparty proof precedes private receipt/body census. It
does not expose unknown event names, IDs, JSON or prose, migrate/enroll an installation or create
cognitive providers. Coverage is a retention proof, not an ordinary conversation permission.
Complete coverage means zero UNKNOWN events and zero UNKNOWN binding rows. Proved owned content
may remain: complete classification does not mean all events are metadata or erasure-ready.

Explicit schema migration and exact physical/registry/recovery pins support the new main schema.
Old archives retain their bytes and missing legacy bindings. Existing floor-first body fencing and
explicit reconciliation apply before restored Appraisal content can receive a terminal metadata
classification. A full-installation/floor rollback is not solved by this table.

## Engineering gates and limits

- Exact prospective owner binding and rollback of owner receipt, audit and binding together.
- All supported families, UNKNOWN legacy, extra prose/version/code/type/duplicate/scope attacks;
  strict JSON and bounded indexed census, with no raw unknown data in public results.
- Normal and recursive-triggers-off REPLACE/UPDATE/DELETE guard tests; actual schema pin and FK integrity.
- Known-content/metadata/UNKNOWN route partition, freshness changes and preserved old preparation ABI.
- Actual synthetic CLI/restart/archive/mixed-head scenario, old Appraisal hashes and numerical history
  continuity, byte-preserving read controls, relevant tests and rebuilt isolated Foundation gate.
- Local control cost and zero real cognitive-provider calls; no Character or paid-model experiment.

Every public result retains `physical_erasure=false` and `execution_authorized=false`. Identifiers,
hashes, numerical influence, freeform checkpoint reasons and unsupported scalar reason codes,
SQLite free pages/WAL, archives/exported/OS/provider copies, authenticated access/encryption/key
recovery and Linux/full-14.4 acceptance remain open. This contract is not confidentiality, unlearning,
forensic cleanup, whole-root erasure or Stage 15 authorization.
