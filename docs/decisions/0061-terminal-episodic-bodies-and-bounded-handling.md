# ADR-0061: Terminal episodic bodies and bounded handling

- Status: Accepted — implemented; macOS engineering gate verified; whole 14.4 remains open
- Date: 2026-10-06
- Authority: explicit continuation after 14.4.2d into terminal body representation and bounded
  owner handling; this slice performs real selected payload-body removal
- Related: ADR-0053, ADR-0055, ADR-0056, ADR-0057, ADR-0059, ADR-0060
- Supersedes: the physical-execution prohibition of ADR-0060 only for the fresh, bounded
  MemoryManager/RetrievalIndex logical-body operation defined here; existing intent approval does
  not authorize it. Other owner, exclusion, numerical-history and Character policies remain.

## Context

ADR-0060 records durable terminal root intents and metadata-only retention preparation. It
deliberately preserves content and provides no executable plan. Source exclusion protects
disclosure but does not remove existing episode summaries, quotation bodies or stored vectors.
Nullable owner bodies, trustworthy terminal representations and a fresh operation-specific
approval are required before those fields can be removed.

An intent-only floor is insufficient to distinguish an older main backup captured after intent
acknowledgment but before later body handling. That backup can carry the same intent head while
retaining the old bodies. A main-only application receipt cannot preserve the later handling fence.

## Decision

### 1. Finished product scope

Open **14.4.2e — Terminal Episodic Bodies & Bounded Owner Handling**. It adds a separately
reviewed executable operation that removes selected:

- Episodic summaries owned by MemoryManager, including affected ACTIVE and ARCHIVED records.
- All quotation bodies of each selected episode's evidence rows.
- Stored vectors owned by RetrievalIndex for those same episodes.

The operation keeps immutable episode/evidence references and required foreign keys. It preserves
historical numerical values, formation/lifecycle history, confidence, replay protection, caps and
unrelated owners. It uses existing owners' deterministic mutation methods and invokes zero providers.

This is actual logical body removal in the supported current main database. It is partial owner
coverage, not complete root erasure or a forensic storage-cleanup certificate. Public outcomes
must keep physical_erasure=false and distinguish selected bodies removed from retained surfaces.
Canonical USER/assistant bodies, semantic/model/position/inclination content, reflection and
audit/appraisal bodies remain explicitly retained or unresolved. InteractionLog body handling is
a proposed subsequent slice, not implemented here. Character is unchanged.

No generic cascade, automatic garbage collection, arbitrary SQL deletion program, substitution
text, new self evidence or numerical rollback is authorized.

### 2. Terminal body representation

MemoryManager owns the episode/evidence body state. A retained episode has its existing nonblank
summary and available evidence quotes. A terminal erased body has SQL NULL summary/quote fields
and a required reference to the committed body application receipt. State/body/receipt consistency
is enforced by exact schema constraints.

Normal domain memory/evidence types continue to require actual nonblank content. Erased rows
are represented by a separate metadata-only header/tombstone read, never by summary=None in a
normal aggregate or invented replacement wording. Their evidence IDs, root references, observation
timestamps, formation identity and structural relationships survive.

Historical attribution proof for a trusted terminal row is distinct from available quotation
proof. It can support scoped inspection and replay verification, but cannot certify live content,
retrieval, new semantic formation or fresh self/relationship evidence. Bodies cannot be reactivated,
reindexed, overwritten by formation retry or resurrected through an ordinary owner upsert.
ACTIVE/ARCHIVED lifecycle history retains its own meaning; archival is not erasure.

### 3. Exact bounded selection and fresh approval

The operator selects an existing proved canonical root in the configured scope. It must already
have a durable floor intent, terminal source exclusion, current source repair and current intent
acknowledgment. Scope proof precedes private selection, counts and public outcome lookup.

A fresh body-handling preview produces an operation-specific approval digest separate from
inventory, intent, archive and exclusion digests. It binds:

- Root, intent and trusted installation/floor/lineage/identity.
- Expected source generation, erasure revision and body-handling revision.
- Exact supported schema, owner policies and explicit partial retention effects.
- Exact private selected episode/evidence/vector references, current owner versions and complete
  before-state fingerprints, including required retained structural/numerical fields.
- Bounds, uncertainty, body dispositions and remaining owner/storage surfaces.

Only deterministically proved selected same-scope episodes may be mutated. Complete managed
dependencies and canonical physical evidence are assessed separately. Missing/UNKNOWN ancestry
does not authorize deletion of an unproved summary or unrelated counterparty body. It is reported
as an unresolved remaining surface. All evidence quotes of an approved episode are part of the
visible operation scope, even when the episode has more than one same-scope root.

A fresh executable plan is blocked by any missing/UNKNOWN/unproved episode dependency in the
admitted store, including another counterparty's episode: incomplete actual-input ancestry cannot
prove independence from the selected root. Such bodies remain retained, and no collateral
permission is inferred. Completely proved unrelated episodes remain outside the selected operation.

Private selectors use fixed typed owner references, not supplied table/column SQL. Counts alone
cannot identify approved targets. Canonical inventory/capacity limits remain exact; over-capacity
selection denies rather than truncating, approximating or silently widening scope.

Bounds are 256 targets per action, 4096 floor actions, 1,000,000 cumulative private target
references and 100,000 distinct effective materialization targets, subject to the inherited
100,000-row/64-MiB census and 1,000,000 work quota (census rows × proposed effective targets).
Fresh review/commit enforce the proposed-head quota before floor append; resume enforces it
again. Indexed target material and chunked terminal admission bound processing without repeated
full-store scans per target. Later growth can leave a truthful pending action, not partial completion.

### 4. Floor-first approved action and recovery fence

SourcePolicyManager remains the sole independent-floor writer. Add a separately versioned
durable approved body action and monotonic body_revision. Source generation and existing intent
erasure revision retain their meanings. A new explicit floor migration preserves fixed inode,
installation binding and prior receipts; reads/runtime never implicitly upgrade it.

The explicit independent-floor revision is policy_0003. The main revision is
0018_erasure_bodies, with erasure_body_applications and erasure_body_bindings plus terminal
episode/evidence body fields. BodyErasureProposal contains the exact private BodyErasureTarget
selectors; BodyErasureAction embeds that immutable proposal. The floor body_head_digest proves
the complete approved action head without exposing per-body fingerprints.

The approved action records immutable metadata needed to verify or resume the exact operation:
request identity, fresh approval, selected owner references/preimages, scope, schema and policy.
It stores no quotations, summaries or vectors. Original intent approval is a prerequisite, not
permission to create this later action.

Acquire the exclusive bounded cross-process barrier before any floor/main transaction. Reprove
the reviewed state and durably commit the exact action in the floor before the first body mutation.
This advances the body head. A later main failure cannot cancel it, decrease the revision or replace
it with an allow state. Unknown commitment outcome is resolved by the original request binding.

The main database carries a derived body-head acknowledgment distinct from both intent preparation
and the five source-owner repair watermarks. Ordinary protected runtime rejects a stale body
acknowledgment, including a restored after-intent/before-handling backup. Offline byte transport
remains explicit and unmodified; restoration cannot auto-execute body handling.

Acknowledgment of the whole body head requires verified application of its current effective
targets, not just the existence of receipt rows. Public operations expose only the selected scoped
action. Private resume materializes all approved floor actions in one bounded atomic main
transaction. For a memory selected by multiple approved actions, the latest explicitly approved
target is authoritative; earlier actions remain immutable historical approvals. The acknowledgment
binds the complete action head and those effective targets, not a false claim that every superseded
original after-state is still current.

### 5. Atomic owner handling and replay

Within one bounded main transaction, existing MemoryManager and RetrievalIndex methods:

1. Revalidate exact selected references, versions and approved original preimages.
2. Record the typed main application receipt.
3. Mark selected episode/evidence bodies terminal and set summary/quote fields to SQL NULL.
4. Remove all approved stored vectors for those episodes.
5. Verify the required retained skeleton/numerical invariants and derive current body acknowledgment.

The receipt and both owners' effects commit atomically. A failure rolls back main changes while
retaining the authoritative pending floor action. There is no distributed transaction and no
provider-time lock.

Replay/resume of each current effective target requires either its exact approved original preimage
or exact expected terminal after-state. Terminal proof includes the correct action binding, selected
episode/evidence row set, retained structural/numerical fields, NULL body fields and vector absence.
A receipt alone or matching action_id alone is insufficient. Unexpected live/terminal mixtures,
changed versions, extra evidence/vectors or a changed skeleton reject rather than guessing.

Changed or expanded selection needs a fresh review and new approval. The executor cannot recompute
a larger destructive plan from an old intent, inventory digest or application receipt. A terminal
body is never copied back from an archive or re-created by learning/indexing retry.

If an approved action is pending because its original bodies or selection have changed, explicit
operator preview may review a fresh same-root plan under current source/intent admission while
ordinary runtime remains blocked by body-head lag. A new approval can supersede the affected
target without rewriting the old action. The operator sequence is pending status → fresh preview
and approval → new action → atomic application of the whole effective head. No later target is
invented during resume, and an old intent never becomes an executable plan automatically.

Normal body admission reads fresh sqlite_schema metadata and validates the exact physical schema
pin. Inventory assessment additionally retains the complete owner-route column assessment.
Recovery/inventory/body admission may share a bounded pure canonical-DDL memo: the exact raw SQL
string is its key, strings of at most 16,384 characters may use at most 128 entries, and larger
strings are transformed without caching. Only the deterministic metadata transformation is
memoized. Every admission still reads fresh schema metadata, recomputes its digest and verifies
the full trusted body head and terminal proof. Eligibility, protected bodies and admission
decisions are never cached by this mechanism.

### 6. Operator interface

The explicit floor upgrade is `sources erasure-body-policy --upgrade --reviewed`. The bounded
operation is `sources erasure-bodies ROOT --intent-request-id INTENT` with one of:

- `--preview`: read-only fresh owner plan and operation-specific approval digest.
- `--apply --expected-generation G --expected-erasure-revision E --expected-body-revision B
  --sha256 APPROVAL --request-id ACTION`: record the reviewed floor action before applying the
  current effective head.
- `--resume --request-id ACTION`: explicitly resume already approved actions; no new selection.
- `--status --request-id ACTION`: scoped metadata inspection without mutation.

Public outputs retain no private descendant selectors or body fingerprints. Preview/status do
not enroll, repair, migrate or invoke providers. Apply/resume operate only on the existing admitted
stores; the separate upgrade command does not grant a body action.

### 7. Public result and limits

Results separate durable floor approval, current application/body acknowledgment, selected logical
body removal and unresolved/residual coverage. They disclose bounded owner/count/status metadata
without private selectors, descendant IDs, source text, raw exceptions or per-secret fingerprints.
A pending floor action remains visible after storage failure without being labeled completed.

SQL NULL/vector removal does not scrub SQLite free pages, WAL/journals, exports, caches, OS
snapshots or external copies. No VACUUM, journal conversion, secure_delete claim, artifact sweep,
SSD erase or cryptographic-erasure guarantee is introduced. Full installation rollback, malicious
same-user writes, latest-floor/key/device recovery and authenticated approval remain separate gates.
Linux process verification remains open until actual execution.

### 8. Acceptance

- Selected summaries and all selected evidence quotation bodies are NULL; corresponding vectors
  are absent after restart, with valid receipt bindings and integrity/FK checks.
- Retained skeleton, numerical values, confidence, formation/lifecycle history, caps and unrelated
  counterparties/owners remain unchanged; no fabricated domain content is returned.
- Erased bodies cannot reactivate, reindex or be revived by ordinary owner/processing retries.
- Exact scope, schema, request, approval, version, preimage and full target-set conflicts fail closed.
- Failure before floor commitment changes nothing; failures after it preserve a pending action.
  Main receipt/body/index effects are atomic across fault and unknown-outcome tests.
- Replay verifies actual original/terminal states. Forged receipt-only completion, extra vectors,
  changed evidence sets and modified skeletons are rejected.
- An after-intent/before-handling backup is denied by the surviving body head until explicit
  handling resume; changed restored preimages need fresh review, not automatic execution.
- Public coverage remains partial and physical_erasure=false; retained raw/derived/opaque/external
  surfaces stay explicit. Body control commands invoke zero providers. A separate synthetic
  foreground/restart/recovery scenario uses fake providers to verify denied disclosure and
  processing; no real provider or Character evaluation is part of this gate.
- Relevant automated/manual gates and the frozen rebuilt Foundation toolchain pass before
  recording an engineering result.

This ADR opens only the specified owner operation. Canonical-body handling, full-root erasure,
forensic cleanup, production access/key work and whole Checkpoint 14.4 acceptance remain open.
