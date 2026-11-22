# Terminal Episodic Bodies & Bounded Owner Handling

Status: accepted bounded scope under
[ADR-0061](decisions/0061-terminal-episodic-bodies-and-bounded-handling.md);
implemented with the macOS engineering gate verified. Evidence: Foundation,
CLI/restart/recovery scenario and
local cost. Whole 14.4 and the Linux gate remain open.

## What this slice removes

Checkpoint 14.4.2e performs actual selected logical body removal through two existing owners:
MemoryManager removes episodic summaries and all quotation bodies of selected episodes;
RetrievalIndex removes stored vectors for those same episodes. ACTIVE and ARCHIVED episodes
can be selected when their attribution and approved scope are proved.

Episode/evidence rows remain as terminal metadata-only reference skeletons. Historical numbers,
confidence, formation/lifecycle history, evidence/FKs, spent budgets and other owners retain their
meaning. The operation invokes zero providers and changes no Character behavior or prompts.

This is partial owner coverage. At completion of 2e, raw canonical dialogue,
semantic/model/position/inclination wording, reflection, audit/appraisal bodies and unknown
provenance remain retained or unresolved. Separately authorized [2f](erasure-dialogue.md) extends
selected logical handling to canonical and proved dependent assistant bodies.
Public physical_erasure stays false. SQLite free pages/journals, archives, logs/caches, OS snapshots
and external copies are not cleaned or certified.

## Representation and owner boundaries

Selected summary/quote fields become SQL NULL only together with terminal body_state and a
required committed application reference. Existing normal domain memory/evidence types still
require real content. Scoped terminal inspection uses a separate header/tombstone representation;
it cannot emit invented text or construct an ordinary aggregate with missing content.

Historical metadata proof supports identity/scope/replay inspection, not live quotation evidence.
Erased bodies are excluded before provider selection, limits and ordinary mapping. They cannot
reactivate, reindex, accept a formation overwrite or supply new semantic/self/relationship evidence.
Archival status/history remains distinct from terminal body state.

The handling coordinator calls existing owners' typed mutation methods. It does not become a
generic SQL writer or a second owner of memory, index, personality or privacy policy.

## Review and durable action

The root must already be proved in the configured scope, excluded and bound to a durable
[erasure intent](erasure-intent.md). Source repair and intent acknowledgment must be current.
A new operation-specific preview binds exact private episode/evidence/vector references, versions,
original bodies and retained skeleton/numerical fingerprints, schema, owner policies and partial
retention effects. Counts alone are not a plan.

The fresh approval digest is separate from read-only inventory, terminal intent, source exclusion
and archive checksums. A blocked old inventory/intent does not authorize this operation.
UNKNOWN/unproved episode bodies are residual blockers, not approval to remove another
counterparty's content. All quote bodies of each selected episode are explicit selected scope.

Readiness is deliberately conservative across the admitted store: any UNKNOWN, missing or
unproved episode dependency blocks a fresh executable plan, including an episode apparently
associated with another counterparty. Its incomplete input closure cannot prove that it is
unrelated to the selected root. Such bodies remain retained; the operator cannot approve them
implicitly or declare owner coverage complete. Unrelated episodes with complete proved
dependencies remain outside the selection and unchanged. A later uncertainty/retention policy
would require its own explicit contract rather than weakening this gate.

Before any selected body changes, SourcePolicyManager durably records the exact approved action
and advances an independent body_revision in its existing trusted floor. Its metadata preserves
the operation needed for exact replay without storing summaries, quotes or vectors.
The floor upgrade is explicit and preserves fixed inode/binding and prior receipts.
It adds the independent policy_0003 revision; the main 0018_erasure_bodies revision adds
erasure_body_applications and erasure_body_bindings plus terminal owner body fields.
BodyErasureAction embeds the immutable BodyErasureProposal with exact private
BodyErasureTarget references. The opaque body_head_digest binds the complete approved floor head.

The main body-head acknowledgment is distinct from intent-preparation acknowledgment and source
repair watermarks. An older after-intent/before-handling main backup cannot match the newer body
head. Ordinary protected operations deny until explicit handling resume; restore never auto-runs it.

## Execution and replay

The exclusive cross-process barrier precedes floor/main transactions. No lease spans provider work.
After floor commitment, a bounded main transaction revalidates approved preimages, writes the
application receipt, transitions summary/quote bodies to terminal NULL, removes the selected index
vectors and verifies retained invariants. Receipt and both owners' effects commit atomically.

Main failure leaves a durable pending floor action. Unknown floor outcome is resolved through
the original request identity. Reusing that identity for another scope, approval or target set is
a conflict.

Replay/resume of each effective target accepts only its exact approved original preimage or exact
expected terminal after-state. It verifies the selected row set, retained skeleton/numerical fields,
action binding, NULL bodies and vector absence. Finding an application receipt alone is not proof
of removal.
New evidence/vectors, changed body/version/skeleton or unexpected mixed state require rejection
and fresh review. An old approval cannot expand its selection.

Explicit resume applies the whole approved floor head in one bounded atomic main transaction.
The latest explicitly approved target for each memory is effective; old actions remain immutable
history. Acknowledgment proves the complete head and its effective targets, rather than pretending
that superseded original after-states remain current. Public output stays restricted to the selected
proved scope and contains no descendant references, bodies or private fingerprints.

The current bounded contract admits at most 256 targets per fresh action, 4096 approved floor
actions and 100,000 distinct effective targets for main materialization. The inherited exact
census admits at most 100,000 logical store rows and 64 MiB of encoded logical inventory. The
independent floor also caps cumulative private target references at 1,000,000. Capacity failures
deny without truncating selectors; a committed action remains pending if later store growth exceeds
admitted materialization bounds.

Fresh approval also checks a work quota of 1,000,000: admitted logical census rows multiplied
by distinct targets in the proposed effective head. Main resume checks that quota again; later
store growth cannot justify silently skipping an approved target. Indexed target material and
chunked metadata-only terminal admission avoid a full-store scan for every memory.

The opaque whole-head fingerprint is computed from all immutable approved actions and verified
against the trusted floor body_head_digest. Matching a local revision count, receipt row or
body_action_id alone cannot prove that the main application represents the approved head.

A changed/expanded pending target requires a fresh same-root preview and approval. That new
action can supersede the affected target; it cannot rewrite the previous action or cause resume
to infer a larger plan. Operator preview may inspect this pending state under current source/intent
admission while ordinary protected runtime remains blocked by body-head lag.

## Fresh schema admission

Normal body admission validates a fresh exact physical schema pin; inventory assessment retains
the additional full owner-route column assessment. The shared canonical-DDL helper may memoize
only its deterministic metadata transformation. Its cache uses the exact raw SQL string as a key,
admits strings of at most 16,384 characters and holds at most 128 entries. Larger strings are
transformed without caching.

Each admission continues to read fresh sqlite_schema metadata, recompute the schema digest and
verify the full trusted body head and terminal proof. This memo stores no eligibility result,
protected body or admission decision and provides no authority to reuse an older allow state.

Private main action projections use strict typed JSON decoding and canonical comparison.
Boolean or floating-point values cannot impersonate integer revisions or aggregate versions.
Historical enrollment uses narrow predecessor proof columns; missing current body fields cannot
silently turn a damaged current schema into a legacy store.

## Operator commands

Upgrade the independent floor explicitly with
`sources erasure-body-policy --upgrade --reviewed`. This preserves binding and existing receipts;
it does not authorize body handling.

Use `sources erasure-bodies ROOT --intent-request-id INTENT` with:

- `--preview` for a fresh read-only approval digest and partial effects.
- `--apply --expected-generation G --expected-erasure-revision E --expected-body-revision B
  --sha256 APPROVAL --request-id ACTION` to record the fresh approved action before owner handling.
- `--resume --request-id ACTION` for explicit replay of the existing approved effective head.
- `--status --request-id ACTION` for scoped read-only state.

If an action is pending due to changed bodies, use status → fresh preview/approval → new action
→ apply of the effective head. Do not retry an old digest as permission to widen its target set.
Preview/status never create stores, enroll, migrate, repair or invoke providers. Apply/resume use
existing admitted stores and the previously approved bounded owner operation.

## Result and remaining gates

Results distinguish floor approval, pending/current body application, exact selected logical
removal and residual coverage. A successfully applied operation still reports partial coverage
and physical_erasure=false; it is not a full-root deletion certificate.

In this contract, logical body removal means the actual selected summary/quotation columns
are SQL NULL and selected index vectors are absent in the admitted current main store.
physical_erasure=false means that this operation does not prove full-root, whole-installation or
device/media erasure. It does not mean that the selected bodies were only hidden or left unchanged.

This slice performs no VACUUM, free-page cleaning, WAL/journal conversion, artifact sweeping or
cryptographic/SSD erasure. Raw offline export/restore remains byte-preserving administrative
transport. Full floor/installation rollback, malicious same-user writes, authenticated local
approval, access/encryption/key/device freshness and the inherited Linux process gate remain open.

The later explicit continuation opened [14.4.2f](erasure-dialogue.md) under
[ADR-0063](decisions/0063-terminal-dialogue-bodies-and-historical-provenance.md). It defines
InteractionLog terminal bodies and exact historical evidence witnesses while extending this same
body head through policy_0004. Existing episode action JSON and digests remain unchanged.
Derived knowledge owners and forensic cleanup remain separate later contracts.

## Required verification

Tests and an actual synthetic CLI/restart/recovery scenario must show NULL selected summary/quote
bodies and absent selected vectors; intact skeleton/numerical/FK/history state; denied reactivation/
indexing/formation resurrection; strict target/version/preimage conflicts; atomic main failures;
durable pending floor replay; after-intent/before-handling backup denial; receipt-only/modified
terminal-state rejection and explicit residual coverage. All body control commands invoke zero
providers. A separate foreground/restart/recovery scenario uses only fake providers to verify
that protected disclosure and processing cannot revive erased bodies; no real provider or
Character evaluation is required for this gate.

The frozen rebuilt Foundation gate and relevant manual checks are required before any engineering
verification result is recorded. Physical cleanup and whole Checkpoint 14.4 acceptance are separate.
