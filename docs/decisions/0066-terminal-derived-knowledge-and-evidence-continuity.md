# ADR-0066: Terminal Derived Knowledge and Evidence Continuity

- Status: Accepted for the explicitly authorized Checkpoint 14.4.2g; implemented and macOS engineering-verified
- Date: 2026-10-06
- Authority: explicit continuation after the completed 14.4.2f engineering report
- Related: ADR-0053/0057/0059/0060/0061/0063
- Extends: ADR-0063 historical attribution through later approved owner body transitions. Its
  original witnesses and action hashes remain immutable. This does not change live eligibility,
  source exclusion, intent approval, numerical-state policy or Character behavior.

## Context

Canonical USER and selected assistant bodies can already be terminal SQL NULL. Semantic claims,
current User/World Model claims, positions and inclinations remain separate retained bodies.
Their accepted evidence and numerical histories cannot be removed through a generic cascade.

ADR-0063 witnesses bind exact physical evidence rows before canonical removal. Clearing a position
quote or its normalized copy changes that row's fingerprint. Ignoring these fields would weaken
the original proof; demanding the original fingerprint forever would prevent legitimate later
body removal. A terminal semantic claim also cannot be proved by normalizing its now-NULL value.
Historical attribution needs explicit proof continuity through the trusted approved action head.

## Decision

### 1. Bounded finished scope and existing owners

Open **14.4.2g — Terminal Derived Knowledge & Evidence Continuity**. Existing owners remove
the following bodies for exactly approved affected aggregates:

| Owner | Terminal content fields |
|---|---|
| SemanticMemoryManager | Semantic claim value and normalized value |
| User/WorldModelManager | Claim value/normalized value; World Model subject label and normalized label |
| PositionManager | Position proposition/normalized proposition and every selected position evidence quote/normalized signature |
| PositionManager's existing inclination policy | Topic/normalized topic and alternative topic/normalized alternative topic |

A number or boolean stored as a claim value is knowledge content and is removed with that value.
Confidence, inclination score/stability, historical deltas, timestamps, versions, statuses,
required evidence/FKs, idempotency and spent caps remain under the existing retention policy.
The operation makes no inference that the fact was disproved and performs no numerical rollback,
new learning, confidence recomputation, budget refund or Character-state mutation.

Terminal `body_state` and application-reference metadata are added only to these five aggregate
tables. Position evidence retains its existing physical column set: quote and normalized_signature
become SQL NULL together, with terminal authority derived from its approved parent position.
Other model/inclination evidence rows remain unchanged. Exact parent target proof binds the entire
evidence ID set and retained rows. SQL guards prohibit terminal resurrection, attribution changes
and deletion. Normal domain objects still require available content; terminal inspection uses
metadata-only representations, never substitute wording or an optional body in a normal claim.

Source withdrawal and owner/key repair must be current before approval. Semantic/model/position
active-key retirement retains its existing lifecycle meaning. The permanent inclination key and
aggregate identity remain reserved; NULL topics neither release the key nor permit duplicate
formation or a merge into the erased aggregate.

### 2. Exact causal selection and collateral scope

Selection combines canonical accepted physical evidence with COMPLETE actual-input dependency
closure, including immutable historical payload versions. Citations alone do not establish that
an aggregate is independent of a root. Matching words do not establish dependence. Cycles,
missing versions, unsupported shapes, UNKNOWN/OVERFLOW ancestry or unproved scope deny an
executable plan; the planner cannot omit an uncertain in-scope knowledge body to claim coverage.

All affected selected bodies are erased in full. A mixed-support aggregate is not reconstructed
by subtracting one fact, rephrasing its proposition or generating a replacement from remaining
roots. Its other same-counterparty evidence quotes are included in the fresh visible operation
scope, while those independent canonical USER bodies remain available. Every selected evidence
edge must have exact accepted historical proof or a freshly verified live-origin proof.

Identity-global positions/inclinations may be selected only when their complete affected
support/input closure proves the requested identity and counterparty. Affected cross-counterparty
or unproved global ancestry blocks this slice. It does not authorize deletion of another person's
body. Completely proved unrelated aggregates remain outside the selection. Opaque audit,
appraisal and other out-of-scope bodies remain explicit residual surfaces rather than collateral
deletion targets.

### 3. Append-only historical evidence continuity

Add a closed, versioned KnowledgeBodyAction to the existing mixed body stream. Its private typed
targets bind owner kind/aggregate ID, version, complete original and terminal material digests,
exact evidence set and the applicable owner policy. Selectors are typed owner references, not
operator-supplied SQL or arbitrary table/column programs. The independent floor stores no bodies.

An evidence transition binds closed table/evidence ID, canonical source identity and scope,
predecessor approved witness/action, exact before-row fingerprint and exact terminal fingerprint.
For an already-NULL canonical source, its predecessor must be an exact currently authorized
historical witness. The transition preserves attribution and all retained fields; it only approves
the specified terminal content change. It cannot create an evidence edge or rewrite its origin.

A same-scope evidence row whose canonical USER is still available may instead use an explicit
live-origin witness captured in this action. Capture verifies the exact accepted source/owner
chain and, for quote-bearing evidence, nonblank literal membership before quote removal. This is
retained historical support, not fresh evidence or permission to learn. A later dialogue erasure
may consume that exact approved terminal-owner proof, as it already consumes terminal episode
proof, without claiming that NULL quotes were checked against live text.

Original dialogue witnesses, dataclasses, serialized actions, approval digests and historical
body-head hashes remain byte-compatible. The existing full-row digest is not weakened by ignoring
quotes or unfamiliar fields. Keeping witnessed evidence table column sets unchanged avoids an
unnecessary legacy projection. A future physical-shape change requires a separate exact-schema,
versioned representation contract; this ADR adds no generic legacy field filtering.

Build a purpose-limited effective historical index only after checking the complete trusted mixed
floor head: count, contiguous revisions, unique action/request IDs, installation/floor/lineage/
identity binding, excluded action root and exact head digest. Its three explicit seams are:

- Canonical root attribution from actual approved terminal dialogue state.
- Evidence attribution from the exact original witness or valid approved transition/live-origin
  chain and actual retained row, with exact approved terminal-parent proof when required.
- Terminal owner attribution from the exact approved NULL body and retained aggregate/evidence
  skeleton, instead of trying to normalize or map unavailable content.

Changed/new/forged rows do not inherit a witness. A changed quote after its canonical body is NULL
cannot be re-captured as literal evidence. These seams serve historical inventory, operator
review and actual completion proof only. Live memory scope, provider dispatch, new proposal
admission and owner formation continue to require available content and current source policy;
they never consume the historical index as learning authority.

### 4. Approval, floor-first execution and recovery

The excluded proved root, existing durable intent and current source/intent repair remain
prerequisites. A fresh operation-specific knowledge-body approval binds the exact schema/policy,
scope, source generation, erasure revision, body revision, complete selected preimages, terminal
material, continuity witnesses, collateral effects, bounds and retained surfaces.

Explicit independent-floor and main migrations add this action type and owner representations.
Their revisions are **policy_0005** and **0020_knowledge_bodies**, respectively.
Reads/runtime never implicitly upgrade the floor. Source generation and erasure revision keep
their meanings; the existing body revision/head is extended, without a new independent counter.
All prior episode/dialogue action bytes and head digests remain exact.

Acquire the exclusive bounded barrier before transactions. Commit the reviewed action to the
trusted floor before any body mutation. The main coordinator records typed receipts and invokes
only the existing owner writers in one bounded transaction, then verifies actual terminal
aggregate bodies, all selected quotes, exact retained skeletons and witness continuity before
acknowledging the complete effective head. It is not a new generic state owner.

Resume privately materializes the complete approved effective head. Every effective target must
match its exact authorized original state or exact approved terminal state; a receipt/ACK alone
is insufficient. A pre-commit main fault rolls back all owner writes while the floor stays pending.
Uncertain completion reports pending without asserting that content is still available; fresh
status/resume proves actual state. No provider or provider-time lock is involved.

For mixed pending heads, operator review receives the complete trusted floor stream and checks
all actual terminal targets and their exact historical transitions. It may recognize an exact
authorized original row that is still pending, without granting ordinary admission or writing an
ACK. Changed/expanded executable selections require a new explicit approval; an unknown NULL-root
evidence mutation is not repaired by guessing a new origin. Supersession cannot erase old history
or expand the destructive operation beyond the current effective approved selection.

Effective knowledge witnesses require metadata-first whole-owner proof. Ordinary admission proves
terminal NULL bodies and NULL quote/signature predicates before materializing witness rows.
Privileged pending review may use the current transition's original fingerprint only when the
available owner has no action marker, its version and complete evidence/revision rowset match the
effective approved original material, and its predicted terminal digest also matches. This permits
fresh review of a changed separate owner while the pending position remains exact. Changed position
material after canonical NULL loses this original proof and safely blocks; restoring that proof
requires a future reviewed typed repair contract. A per-quote before/after alternative is insufficient.

Ordinary runtime continues to require complete main-head materialization and actual body/proof
validation. Source/intent repair cannot acknowledge knowledge body handling. Old main backups,
forged receipts, missing targets and wrong ACKs cannot restore disclosure through the same trusted
installation. Offline recovery retains exact supported predecessor snapshots and transports their
bytes; it does not sanitize them, implicitly execute an intent or roll back the independent floor.

Deterministic source-support withdrawal that increments an owner version must carry forward its
existing input closure through the existing `copy_payload` writer in the same owner transaction.
The successor receipt references that same aggregate's exact prior version, preserves its proof
status and legacy flag, and remains `eligible=false`; it does not create fresh input, evidence or
learning authority. A prior COMPLETE closure remains available for historical assessment only.
Missing or UNKNOWN prior receipts are never promoted. Historical stores that already lack such
successor receipts remain safely UNKNOWN; reconstructing them requires a separate reviewed typed
repair contract, rather than automatic inference or a fixture-created receipt after exclusion.

### 5. Resource bounds, honest outcomes and verification

Use the existing bounded main census and action stream: 100000 rows/64 MiB logical input,
4096 actions, 131072-byte target JSON, one million cumulative references and the one-million-work
quota. Each action has at most 256 owner targets and 512 combined evidence transitions/live-origin
witnesses. Overflow denies without truncation. Index owner/evidence/causal relationships once;
ordinary proof uses bounded targeted reads, not a repeated full census per witness.

Public results expose scoped counts, closed reason codes and explicit partial coverage, without
body text, evidence quotes or descendant identifiers. Actual selected SQL NULL removal is a real
body effect, but `physical_erasure=false` remains mandatory. Reflection, opaque audit/appraisal,
retained numerical history, identifiers/hashes, free pages, WAL/sidecars, archives/staging, OS and
external/provider copies remain explicit limits. Whole-installation/floor rollback, malicious
same-user writes, authenticated access, encryption/key recovery and Linux process verification
remain separate gates. This slice introduces no Character prompt/model/behavior dependency.

Acceptance requires exact old action/hash compatibility; owner NULL/SQL guards and immutable
evidence/FK/numerical/key retention; terminal semantic proof without normalize(NULL); original
and live-origin witness transitions; no post-NULL fresh-learning authority; future dialogue capture
from exact terminal evidence; mixed same-scope and blocked cross-counterparty/UNKNOWN cases;
missing/changed/forged transition and extra-edge rejection; floor-first interruption/atomic
rollback/replay/supersession; mixed pending-head reapproval; old-backup/false-ACK fences; strict
schema/codec and resource bounds; synthetic zero-provider CLI/restart/recovery verification,
latency and the rebuilt Foundation toolchain. Implementation and measured results belong in the
progress ledger after verification, not in this accepted scope contract.
