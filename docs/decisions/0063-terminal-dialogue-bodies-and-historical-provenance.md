# ADR-0063: Terminal Dialogue Bodies and Historical Provenance

Status: Accepted for explicitly authorized Checkpoint 14.4.2f, 2026-10-06.
Extends ADR-0057/0059/0060/0061; does not supersede their source, intent or episode contracts.

## Context

Source exclusion already prevents use of canonical roots and their known dependencies. ADR-0061
removes selected episodic summaries, quotes and vectors. Canonical USER text and assistant replies
remain separate owned bodies. Removing a USER body also removes the literal source against which
retained quotation evidence was checked. A message ID or body hash alone cannot prove that a
particular retained quotation was ever present in that source.

## Decision

InteractionLog owns a terminal `conversation_messages.content = NULL` representation. Normal
HistoricalMessage and ConversationInteraction remain contentful domain objects. Erased messages
have a separate metadata-only header. IDs, role, sequence, schema version, timestamps, scope,
interaction status, provider receipts and numerical history remain. SQL guards prohibit terminal
body resurrection and changes/deletion of terminal attribution. Downgrade is explicitly refused.

A fresh dialogue plan selects the excluded canonical USER, its paired assistant and all assistant
replies with COMPLETE proved actual-input dependence on that root. Independent later USER text
is retained. UNKNOWN assistant ancestry anywhere in the admitted identity blocks a fresh plan;
affected cross-counterparty replies require a later explicit policy and also block. Selection
does not infer dependencies from matching words. Selected episode dependencies must already be
terminal under an approved episode action. No generic SQL cascade or deletion is introduced.

The single existing body stream is extended with a closed `dialogue_body_erasure_v1` action.
Explicit floor policy migration 0004 admits the new type. Existing episode action dataclasses,
JSON, approval hashes and episode-only head digests remain unchanged. Source generation, erasure
revision and body revision remain separate counters. Main receipts and whole-head acknowledgment
are reused; effective target keys include owner kind. A fresh operation-specific dialogue review
is mandatory: an intent or episode approval does not authorize canonical text destruction.

Before canonical removal, the exact approved dialogue action captures content-free historical
evidence witnesses: closed table name, evidence ID and fingerprint of the exact retained row.
The fingerprint includes the exact attribution skeleton and a quote digest where the table stores
a quote. Memory and position literal nonblank quote membership is proved while USER text exists;
quote-free model/inclination edges retain their exact verified canonical/source chain without a
new quotation claim. Witnesses are limited to memory, user-model, world-model, position and
inclination evidence for this exact root. Already-erased episode
edges use their existing trusted terminal episode proof. Changed/new evidence does not inherit
the witness. A witness proves retained historical attribution only, never eligibility for new
learning, provider context, a permission or a grant. Live evidence still requires available text
and the source admission contract. Witness values and body fingerprints are private.

The trusted floor decision precedes main writes. Under one main transaction the coordinator
checks all original/approved-terminal preimages, records exact mixed actions, invokes only the
existing owner writers, verifies actual terminal bodies and retained witness rows, and acknowledges
the complete body head. A failure before main commit rolls back all main writes atomically while
the floor action remains pending for explicit resume. An uncertain commit outcome or subsequent
verification failure reports pending without certifying that bodies remain available; fresh
status/resume proves actual completion. Resume applies the current effective approved whole head,
including later actions or supersession, and never expands beyond that approved selection.
Old backups and forged receipts/ACKs cannot bypass actual body proof.
Source/intent repair never acknowledges a body head. Ordinary reads/providers remain fenced until
the exact approved mixed head is materialized. Restart/replay cannot generate replacement text
for a terminal interaction. Metadata-only operator root resolution remains possible after NULL
through trusted terminal proof; legacy enrollment cannot manufacture new quote proofs from NULL.

Operator root resolution and fresh preview receive the complete approved floor action stream under
the exclusive control lease. Its count, contiguous revisions, bindings and exact digest must match
the trusted snapshot before deriving historical attribution from actual terminal rows. This permits
review/resume when the latest floor action is pending and an earlier canonical root is already
NULL. Main receipts or an incomplete prefix never become independent authority. Generic inventory
and ordinary runtime keep strict complete main-head proof; operator preview checks current source
and intent mirrors without treating a body ACK as a prerequisite for re-reviewing pending work.
If no unavailable or action-attached canonical USER needs historical proof, the default historical
context can be empty without requiring a body receipt. That absence grants no authority. This
preserves 2e changed-target reapproval and prevents a mixed-head recovery deadlock.

Bounds are 256 message targets and 512 witnesses per action; the existing 131072-byte target JSON,
4096-action, one-million-reference and 64-MiB floor bounds apply (references include witnesses).
Main census, byte and work bounds are inherited. Overflow blocks a plan, without truncation.

## Consequences and scope

This is partial logical body removal, always `physical_erasure = false`. Retained claims,
positions, appraisal/reflection/audit bodies, identifiers/numerical effects, SQLite free pages,
sidecars, archives, staging, OS snapshots and external/provider copies remain explicit residual
surfaces. No numerical rollback, budget refund, forensic erasure, access/encryption, new Character
state, prompt tuning or provider call is added. Character receives only already-admitted available
history through the existing typed interface. No implementation/version-specific dependency exists.

The vertical must verify old episode byte/hash compatibility; mixed revision and owner-key
isolation; literal witness capture and mutation rejection; actual NULL and SQL guards; paired and
derived replies versus retained independent USER/other counterparty; atomic interruption/resume;
restart, old-client replay, source repair and pre-dialogue backup fences; legacy/corrupt schema;
whole-state recovery; realistic synthetic CLI and fake-provider scenario; latency and full toolchain.
Linux process verification and complete 14.4 acceptance remain separate open gates.
