# Whole-store Retention & Readiness

Status: **14.4.2i isolated macOS engineering gate complete** under
[ADR-0074](decisions/0074-whole-store-retention-and-readiness.md), explicitly opened on 2026-10-14.
Full unfiltered Foundation: **4815 passed /
5 skipped /16 warnings**, rebuilt noneditable package, Ruff/mypy, fresh migration/
bootstrap and exact source/installed parity. This stable-Character compatibility snapshot does
not accept the moving Character checkout, Linux or complete14.4.

Actual final-snapshot scenario passes **21/21**, with
531 rows =460 supported-retained +71 owned content +0 UNKNOWN in the healthy managed case.
The initial19/21 result is preserved: a valid
unsupported backlog remains UNKNOWN in its new automated regression, and the corrected fixture
claims terminal knowledge facets only for actual semantic targets. No contract is broadened or
legacy binding backfilled. Local cost and
boundary audit retain proof details; current scope,
residuals and the next proposal are in [progress.md](progress.md).

The final documentation audit records local
link and whitespace checks. The completed-scope documents are updated after the immutable code
gate; these documentation-only changes do not replace its preserved snapshot manifest.

## Scope

`state retention` assesses the bounded logical contents of the existing current main SQLite
store for its installed identity. The admitted schema is `0024_checkpoint_reasons`: all **60
tables**, including empty tables, have an explicit entry in the existing **15-owner** registry.
This report is independent of a selected canonical USER root or counterparty; it does not invent
one to represent identity-owned state.

The owners are InteractionLog, MemoryManager, RetrievalIndex, SemanticMemory, CurrentModels,
SatoriPositions, Reflection, EmotionManager, RelationshipManager, PersonalityManager, Audit,
InitialSelf, InitialValues, SourcePolicyManager and SchemaRegistry. These are classification and
routing boundaries; this slice adds no owner, mutation path, floor stream, body action, binding,
migration or durable readiness state.

Older exact snapshots retain their existing [offline transport](recovery.md) and operator
contracts. This standalone control does not migrate an old main database to make it assessable.
Root-specific [erasure preview](erasure.md) and [owner preparation](erasure-intent.md) retain their
separate scope and approval rules.

## One row, one category

Each actual logical `(table, primary key)` receives exactly one category under
`whole_store_retention_v1`:

| Category | Meaning |
|---|---|
| `supported_retained` | The exact supported structural/numerical contract, or approved terminal body representation, is proved. |
| `owned_content` | The owner and representation are proved, and available declared content remains. |
| `unknown` | A required shape, version, scalar code, structured value, owner/reference/scope, provenance or terminal proof is unsupported or absent. |

For each table and for the entire census:

```text
supported_retained_rows + owned_content_rows + unknown_rows = total_rows
```

The categories are disjoint. `structural_metadata`, `numeric_history` and `terminal_body` are
additional facets and may overlap on the same row. They are never added to the category totals
as extra coverage. A `terminal_body` facet requires the complete approved head and actual terminal
owner material; a NULL column or copied marker alone is insufficient. A row can also carry more
than one closed UNKNOWN reason, so reason counts are not another disjoint partition.

`coverage=complete` means that every row has a supported classification, including any remaining
owned content. `coverage=partial` means at least one row is UNKNOWN. Readiness follows the same
partition:

| `logical_readiness` | Condition |
|---|---|
| `blocked_unknown` | `unknown_rows > 0` |
| `owned_content_remaining` | No UNKNOWN rows, but available owned content remains |
| `retained_only` | Neither UNKNOWN rows nor owned content remain |

Complete coverage is not a declaration that a root, installation or person has been erased.
Initial self names and value descriptions are declared content and remain `owned_content` when
their owner/shape is proved. Supported retention of IDs, hashes or numerical state does not make
those values anonymous or insensitive. Semantic/model number and boolean values remain knowledge
content; they are not reclassified as numerical history merely because they are scalar numbers.

## Admission before the census

The control acquires the installed policy runtime's shared barrier and proves the requested
identity, floor, installation and lineage. Inside one consistent SQLite read transaction it then
checks the exact physical schema, source mirror, owner reconciliation, erasure preparation
projection, complete mixed body head and foreign-key integrity before loading the protected
census. The inventory performs this verification itself; omitting a runtime callback does not
bypass it.

The complete body head binds the exact ordered action receipts to the trusted floor digest and
revision. It includes all six existing action families and checks actual owner state:

- episode summaries, evidence quotes and compatible index removal;
- canonical USER, paired and proved dependent assistant bodies;
- derived knowledge bodies and historical evidence transitions;
- Reflection payloads with their immutable parent/source/attempt/outcome material;
- both Appraisal explanation copies, numerical material and immutable actual-input ancestry;
- identity-owned checkpoint approval/restore reasons and their fixed numerical closure.

Historical attribution uses the existing approved action and witness contracts. It is never live
source eligibility. Fresh learning, provider dispatch and owner commits continue to use their
own available-content and source-policy checks.

A transaction-local `RetentionContext` shares the indexed material and classifications across
owners and selected-root routes. It is bound to the exact store objects and row material, schema,
logical store digest and snapshot. It is not reusable authority for another store, later body
head or changed row. No protected classification or admission decision is stored globally.

The public status/check path is strict: a pending or tampered body head, missing/replaced policy
floor, old-main restore, wrong acknowledgment or stale owner projection denies without protected
coverage output. It performs no enrollment, policy upgrade, migration, reconciliation, owner
preparation, automatic processing or provider construction.

The separate internal `for_owner_assessment` factory serves already privileged operator review
while an approved action is pending. It may use the fully verified approved floor head for
historical attribution, but cannot promote a partial main projection to complete-head authority.
Pending review receives no terminal-body facets; terminal rows without the required current
proof stay conservatively UNKNOWN. This factory does not acknowledge the head, approve execution
or weaken ordinary admission. It is not the public `state retention` path.

## Closed row contracts

Classification uses exact versioned field roles, strict native types, finite bounded numbers,
closed statuses and scalar-code matrices, bounded duplicate-free JSON, actual owner/reference
joins and scoped canonical or historical proof. Familiar table or event names are not sufficient.
The pinned physical field contracts cover all 60 tables; 45 metadata/numerical tables have explicit
owner contracts, and available/terminal body routes use their existing owner material proofs.

All 14 scalar `reason_code` carriers require their supported owner-specific code and state/kind
relationships. Provider, model, formation-method and policy labels are retained only in their
declared bounded opaque identifier roles. There is no provider-specific allowlist or dependency
on a Character prompt implementation. Unsupported codes, malformed structures, missing/foreign
references and unknown contract versions remain UNKNOWN; this operation never seals or backfills
legacy data to improve coverage.

Embedding vectors have a separate bounded field-role contract matching the existing provider-neutral
owner representation: 1–8192 finite native numeric components, excluding booleans, exact declared
dimension and nonzero norm. The serialized vector may occupy at most 262,144 bytes; ordinary
768/1536-dimensional vectors do not inherit the generic JSON collection limit. Generic document
limits remain unchanged, and vector classification adds no provider allowlist or arbitrary
numerical-magnitude threshold. Invalid or unsupported vectors remain UNKNOWN.

Audit classification reuses the existing [owner-bound audit contract](retained-audit-metadata.md).
A prospective binding is checked against its actual event, owner and scope; the binding marker
alone is not certification. Unbound legacy and prose-bearing unsupported events remain UNKNOWN.
The existing paired Appraisal audit proof is preserved without adding a replacement binding.

Source provenance status and retention category describe different facts. A strictly supported
`proof_status=unknown` or `overflow` row can be retained status metadata without becoming COMPLETE
or eligible evidence. A purported COMPLETE receipt still needs its actual nonempty canonical or
inherited dependency closure. Missing, cyclic, foreign or malformed ancestry is not inferred.
Generic footprints may contain identity-global references across counterparties; each referenced
owner and audit family continues to enforce its own scope. Retention classification grants no
new learning or disclosure authority to excluded, archived or historical data.

Existing row/byte/head limits remain in force: at most 100,000 logical rows and 64 MiB of logical
input, together with the existing approved-action/reference bounds. A separate shared classifier
budget caps deterministic indexing, classification and reference-walk work at 1,000,000 units.
Existing owner validators retain their own admission bounds. Capacity denial never returns a
truncated apparently complete report. One SQL census does not imply one index build, a universal
linear-time guarantee or support for every database below the row cap.

## Controls and fresh review

Use the existing configured main database and its trusted policy directory:

```bash
satori state retention --identity-id ID --status

satori state retention --identity-id ID --check \
  --expected-generation GENERATION \
  --expected-erasure-revision ERASURE_REVISION \
  --expected-body-revision BODY_REVISION \
  --sha256 REVIEW_DIGEST
```

`--status` rejects review flags. `--check` requires all four review values, taken from the reviewed
status result. Both commands read an existing file-backed rollback-mode SQLite database. The
write-free factory refuses missing/nonregular/replaced files, WAL mode and existing WAL/SHM/journal
sidecars rather than ignoring committed data or creating missing sidecars. Neither command opens
cognitive services or calls a provider.

Status returns the explicit scope identifiers and opaque digests, current revisions, policy/schema
versions, safe table/owner counts, facets and closed reason counts. It exposes no descendant IDs,
row JSON, stored body prose, unsupported raw scalar codes or exception text. Identifiers and
digests in the fixed contract remain sensitive metadata.

The `review_digest` is separate from historical preparation-v1 digests. It binds the retention
policy, source-policy version, identity/installation/floor/lineage, source generation,
erasure/body revisions and head digest, exact schema, complete logical store, private per-row
classification, counts and unassessed surfaces. A body-head-only change, same-generation row edit
or same-count row replacement can invalidate review.

`--check` repeats the current assessment. A head mismatch returns `retention_head_conflict`; a
changed review digest returns `retention_review_stale`. A current check preserves the report's
complete/partial coverage and logical readiness. It is not a reservation, action permit or approval
to delete anything. CLI exit 0 means complete classification, even when owned content remains;
exit 2 covers partial classification or a rejected review/admission, and storage failure returns
exit 1. Automation must inspect `logical_readiness` and the explicit execution flags rather than
interpret exit 0 as erasure completion.

Root routing may consume the same proved row decisions. A later explicit owner-preparation
refresh uses its established append/idempotency path. Reading this report does not rewrite old
receipts or change preparation-v1 fields, decoder, hashes, six action formats, owner state,
numerical trajectories, budgets or Character behavior.

## Limits of the result

Every successful status keeps:

```text
inventory_scope=current_main_database
whole_head_current=true
external_retention=not_assessed
physical_readiness=blocked
physical_erasure=false
execution_authorized=false
automatic_processing=false
historical_numeric_state_retained=true
```

Actual SQL NULL body removal by a previous approved owner action is stronger than logical source
exclusion, but still does not prove physical device erasure. The report assesses neither SQLite
free pages/WAL/SHM/journals, exported archives/partial exports, application/OS memory caches,
logs/private evaluation artifacts nor already disclosed provider/external copies. Supported
numerical influence and sensitive identifiers/hashes remain; UNKNOWN legacy is explicit.

Offline export/restore remains raw byte-preserving administrative transport under
[ADR-0053](decisions/0053-parallel-foundations-and-whole-state-recovery.md). It does not omit retained prose or prove
erasure. An old-main restore remains fenced by the independently retained source/erasure/body
floor until the relevant explicit control reconciles the approved head. Rolling back the whole
trusted installation/floor, a malicious OS writer, authenticated access, encryption/key recovery,
physical cleanup, Linux execution and complete Stage 14.4 acceptance remain separate gates.
Character tuning, paid experiments, 14.4.3 and Stage 15 are not opened by this read-only control.
