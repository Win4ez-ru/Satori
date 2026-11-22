# Erasure preview and owner routing

Status: accepted bounded read-only Foundations contract under
[ADR-0059](decisions/0059-read-only-erasure-preview-and-owner-routing.md); runtime implementation,
focused tests, actual CLI/recovery drill and frozen macOS Foundation passed. Whole 14.4 privacy
acceptance and physical erasure remain open. Implemented logical source exclusion is
described separately
in [source policy](source-policy.md); current verification remains in [progress](progress.md).
ADR-0060 adds a separate [durable intent and owner preparation](erasure-intent.md) control.
Preview/check remain read-only and never commit that intent. Their schema registry now admits
exact 0016, 0017 and 0018, including 59 tables and eight one-way guards at 0018; full fingerprints
include intent and body-action bookkeeping. Preview/check do not execute either control.
The current exact registry also admits 0019–0023. ADR-0071 partitions audit retention through
prospective bindings and exact owner proof; legacy/arbitrary audit JSON remains UNKNOWN.

## Product result

Preview answers a concrete question: which supported local stored records and owners would need
review if one already excluded canonical USER source were later physically erased? It reports
metadata, retained effects, unresolved dependencies and readiness blockers. It does not expose
source/assistant text, quotes, summaries, model values, propositions, prompts or appraisal text.

A fresh check recomputes the scoped assessment and compares its digest. A match says
the bounded plan still describes the inspected logical state at that moment. It grants no
permission, holds no reservation and authorizes no deletion. Physical readiness is always blocked
in this slice, including `physical_erasure_executor_not_implemented`; a complete useful preview
remains possible.
If the matched assessment is blocked, it remains blocked; freshness does not upgrade coverage.

The commands are:

```bash
uv run --no-sync satori sources erasure ROOT --preview
uv run --no-sync satori sources erasure ROOT --check --expected-generation GEN --sha256 DIGEST
```

`ROOT` is a canonical USER message ID in the configured counterparty scope. There is no apply option.
Exit status is `0` for a complete local assessment, `2` for a blocked assessment (still returned as
JSON) or safe prerequisite/validation denial, and `1` for a storage failure. A matching blocked
check returns `2` with `review_status=current`; this never grants execution authority.
There is no physical deletion, redaction, compaction, file cleanup, erasure-intent commit or
automatic source exclusion. Preview/check invoke zero providers and write no main/floor state,
owner decisions, audit, receipts or artifacts. They do not enroll, migrate, initialize, decay or
repair state to make a result available.

Audit routes distinguish certified structural/numerical metadata (`RETAIN_METADATA`), proved
available Appraisal content (its existing affect owner route) and UNKNOWN (`REVIEW_UNCERTAIN`).
Every audit and binding is partitioned once; missing/orphan/unsupported binding proof remains
an explicit completeness blocker. New schema/store/routing fingerprints make old review stale
without rewriting historical version-one preparation receipts. Scoped installation-wide
`sources audit-metadata ROOT --status` reports this classification separately; complete coverage
does not certify all records as metadata or authorize physical execution.
See [retained audit metadata](retained-audit-metadata.md).

## Scope and prerequisites

The root must be independently proved as canonical USER sequence one in a COMPLETED interaction,
with exact selected identity/counterparty/session ownership. Foreign,
quarantined, unsupported-lifecycle or unavailable targets fail without revealing protected
inventory, IDs or counts. The target must already be excluded by the existing trusted floor.
Installation/lineage binding, main mirror and required owner-repair generations must be current.
Failure instructs the operator to resolve that prerequisite separately; preview does not repair it.

Inspection acquires the existing bounded source-policy barrier before a consistent SQLite read
transaction and releases both before review. Fresh check repeats all proofs and the inventory.
A missing/replaced enrolled floor cannot become a new empty installation through these commands.
The read-only opener accepts existing regular rollback-mode SQLite files only. WAL-mode headers
or present WAL/SHM/journal sidecars are rejected before opening SQLite, because `mode=ro` alone
can create SHM. It does not use `immutable=1`, which could ignore committed WAL content.

## What the local plan covers

| Family | Route | Current retained data |
|---|---|---|
| Canonical source, paired assistant and related stored interactions | InteractionLog | Exact text, attribution and canonical references |
| Episodes, including archived episodes | MemoryManager | Summary, evidence quotes, lifecycle/formation decisions and stored vectors |
| Semantic/User/World claims and positions | Existing knowledge owner | Retired/current payloads, evidence, revisions, correction/competition links and key history |
| Reflection/formation records | Existing reflection/formation owner | Sources, proposal JSON, outcomes and upstream references |
| Affect, relationship, inclinations and personality | Existing state owner | Numeric trajectory, caps, identity, decisions/checkpoints and contextual/evidence references |
| Audit and provenance | Existing audit/payload/decision owners | Input/payload receipts, withdrawal metadata, identifiers, signatures and linked historical audit |

`ErasurePlanner` consumes `ErasurePreviewRequest(message_id, counterparty_id)`;
`ErasureCheckRequest` additionally binds `expected_generation` and `preview_digest`. Immutable
routes expose owner/table names, future disposition, row counts, content-column names and uncertain
counts. Descendant IDs and row values are not exposed. Internal owner versions and all non-versioned
values are bound by one combined logical store fingerprint.
An opaque routing fingerprint separately binds actual internal row keys and their owner/
disposition/uncertainty assignments. Equal public counts therefore cannot conceal a changed
selection; no descendant IDs or protected values are revealed by either fingerprint.

An owner route is a read assessment, not a mutation proposal or generic deletion instruction.
No score, personality, emotion, relationship history or spent cap is refunded. Text-bearing
context is distinguished from retained numbers and FK/provenance metadata for later owner policy.

Actual-input provenance and physical retention are different graphs. The plan includes known
historical/withheld records and structural references; current eligible payloads are insufficient.
UNKNOWN legacy ancestry, missing/new-version receipts, unrecognized record owners, cycles or
overflow remain explicit blockers. Accepted citations do not establish complete uncited inputs.
Conservative potentially affected data is not presented as an exact descendant set.

The first supported schema is exact `0016_source_policy`; limits are 100,000 total logical rows
and 64 MiB logical input. Capacity failures cannot produce a truncated plan or digest. Unsupported
schema or routing coverage cannot silently omit rows. A returned blocked assessment can be checked
for freshness but cannot become complete by matching its digest.

## Digest and freshness

The review digest binds exact root/scope, installation/floor/lineage, source-policy generation,
schema and plan/routing versions, ordered complete inventory, owner versions, retained effects and
readiness blockers. A combined fingerprint binds the entire logical store, including non-versioned
rows, owner versions and dependencies, so changes to evidence, quotes, JSON or append-only history
are detected without rendering them. An unrelated logical write also conservatively makes a prior
review stale. No per-secret-column hashes are returned.
Counts alone do not identify a reviewed state. No content hash is evidence of authentication or
completeness, and a preview digest is distinct from source-exclusion approval and archive checksum.

Fresh check matches only an unchanged assessment and preserves its complete/blocked status.
Any changed relevant root proof,
inventory row/version, dependency, policy, schema or repair state makes the old plan stale.
The result holds at one read snapshot. A future executor must revalidate under its own final
mutation boundary rather than reuse the check as a permit.

## Recovery policy and future tombstones

An old main archive still restores its original sensitive bytes offline. Existing source exclusion
survives only because the independent trusted installation floor remains current; operational
provider/context/job use requires reconciliation before access. Preview/check neither sanitizes
the archive nor changes [whole-state recovery](recovery.md).

Before any later physical executor's first content mutation, the SourcePolicyManager must durably
commit a terminal metadata-only ErasureIntent/tombstone to that same independent floor. It must
bind exact reviewed scope/plan, policy/retention versions, expected floor state and request identity.
The future floor-first receipt reports policy commitment and resumable owner outcomes; physical
completion needs its own verified evidence. Main/owner failure cannot restore allow policy, and
an old main backup cannot replace or downgrade the independent tombstone.

This slice creates no tombstone storage or intent API. Existing exclusion receipts prove logical
withholding, never completed erasure. Full installation/floor rollback, cross-machine floor/key
recovery and authenticated operator authority remain separate prerequisites.

## Physical limits

Only the supported live logical database is assessed. SQLite WAL/SHM/journal files, free pages,
rollback/OS snapshots, private staging, full/partial exports and receipts, logs/provider caches,
external copies and previously admitted/in-flight disclosure are explicitly unassessed. The plan
does not scan or authorize arbitrary filesystem cleanup and does not claim these surfaces absent.
Identifiers, signatures/hashes and retained provenance can remain sensitive themselves.

Future readiness requires reviewed owner text/reference retention, FK-safe representations,
physical database cleanup, controlled artifact inventory, access/key protection and truthful
external-copy limits. Private file modes and a local approval label are not authentication or
encryption. Character changes cannot make any of these proofs pass.

## Verification

Required coverage includes positive scoped preview/fresh match; exact raw-pair, archived, evidence,
vector, revision, JSON, numeric/audit routes; wrong-scope/prerequisite denial; UNKNOWN/unsupported/
overflow handling; stale versioned and non-versioned data; restart and old-main-backup fencing;
failure/repetition with zero state writes/providers; and a synthetic real-CLI workflow. The final
frozen Foundation and measured local planning results are recorded in `progress.md`.
Passing this slice does not accept physical erasure, access/encryption, Linux process-lock
verification, whole Checkpoint 14.4 or Character behavior.

Current results: **68 focused cases** and **3836 passed / 5 skipped** in the frozen Foundation;
format/lint/types/migration/bootstrap passed. The actual CLI drill
assessed 107 rows, 60 routes and 8 affected payload versions through fresh processes, stale checks,
old-main restore/explicit repair and missing-floor denial. Opaque audit/appraisal carriers honestly
keep that production fixture's assessment blocked. A current blocked check is useful freshness
evidence, never a complete retention certificate or execution permit.

The 50-sample local probe measured median/p95
20.480/21.142 ms including the floor/mirror/inventory, without CLI startup. The CLI drill measured
727.393/758.962 ms for preview/check including process startup. Zero control provider calls and
USD 0; no large-store latency budget is established.

The subsequent durable floor-first ErasureIntent and existing-owner preparation under ADR-0060
is separately implemented and verified; its controls are described in
[erasure intent](erasure-intent.md). The current bounded body slice is ADR-0061 below; the
preview command itself remains non-executing.


## Subsequent terminal representations

[ADR-0061](erasure-bodies.md) adds separately approved bounded episode body handling. The
read-only inventory now also admits exact 0018 (59 tables and eight one-way SQL guards), while
preserving exact 0016/0017 support. Terminal episode/evidence rows route as retained metadata;
they no longer claim available summary/quotation content. Historical attribution proof remains
distinct from new quotation eligibility. Preview/check themselves remain non-executing, with
UNKNOWN/opaque readiness blockers and full-root physical erasure still open.
