# ADR-0057: Canonical source exclusion and a trusted policy floor

- Status: Accepted (runtime implemented; Foundation/macOS gates passed, Linux process gate open; limits in docs/progress.md)
- Date: 2026-10-03
- Authority: explicit production continuation of Foundations 14.4 after completed-slice gates
- Supersedes: none of the current implemented runtime policies; this defines the next vertical
- Related: ADR-0003/0004/0008, ADR-0012–0014, ADR-0020/0022–0027, ADR-0053/0055/0056

## Evidence and product boundary

Canonical scope and episode archival have passed their engineering gates. They do not exclude a
canonical user source: archive retains raw history, semantic support and historical self effects.
The same user message can support an episode, semantic/model claim, position, reflection,
inclination, relationship or personality decision. Reflection can cite earlier same-session roots
and receives current own positions/inclinations. Episode extraction also receives paired assistant
text. Filtering only an episode's current evidence cannot cover these actual inputs.

`InteractionProviderMetadata` stores selected aggregate IDs and some versions, not a complete
transitive context-root manifest. Formation evidence proves an owner's accepted grounds; it is
not exhaustive provenance for every word in a provider input/output. Surviving evidence therefore
does not prove that an old assistant reply, summary, proposition or topic label is independent of
an excluded root. No LLM can certify removal by rewriting those strings.

Open **14.4.2b.2 — Canonical Source Eligibility** within the already authorized workstream.
The eventual product operation excludes one proven canonical user root from ordinary disclosure,
new provider admission and new evidence/state proposals, with explicit effects and durable retry.
It survives selection of an older main-state backup in the same trusted installation. The runtime implements the logical exclusion contract described below; verification is tracked
separately. This ADR does not authorize erasure of real data, or open external
sources, network/multi-user access, Stage 15 or Character experiments.

## 1. Owner, authority and typed contracts

Introduce one small **SourcePolicyManager**. It owns canonical-root eligibility, not canonical
messages, episodes, knowledge, self, relationships or technical grants. InteractionLog remains
the message owner; MemoryManager still owns archive/reactivate. Immutable session
`scope_verified` remains attribution and is never used as a mutable retention flag.

The contracts below describe required information; their concrete Python APIs belong to the
implementation slice. Core/application ports remain independent of SQLite, OS locks and providers.

| Contract | Required data/behavior |
|---|---|
| `CanonicalRootRef` | Stable source-lineage/identity, counterparty, session/interaction/message IDs and canonical USER origin; full existing proof, never provider-supplied authority |
| `SourceEligibilitySnapshot` | Trusted floor ID, policy generation/version, root disposition and purpose-specific reason; no source text or permissions inferred from relationship |
| `ProtectedContentFootprint` | Bounded direct canonical roots and immutable owner/input-version dependency handles, with proved transitive eligibility and `COMPLETE`, `UNKNOWN` or `OVERFLOW`; separate from accepted evidence |
| `SourceAdmissionPort` | Bounded protected selection/dispatch admission and final commit lease; reprove scope, complete footprint or the explicit pre-policy no-exclusions condition, current floor/mirror and owner/source versions |
| `SourceExclusionProposal/Preview/Receipt` | Expected floor generation, exact target/scope/effects digest, operation ID, local operator approval, phase/repair status, reason and audit metadata |
| `SupportWithdrawalProposal` | Affected owner/aggregate/version and exact withdrawn roots; remaining historical support and owner disposition, not fresh counterevidence |
| `RecoveryPolicyAdmission` | Bind a candidate main DB to an independently trusted lineage/floor and reconcile before provider/context/jobs; offline byte transport stays separate |

Exclusion is terminal in this scope; there is no source-reactivate shortcut that refunds evidence
or bypasses a later erasure. A newly supplied independent user root has its own identity and
normal owner admission. Archive/reactivate remains reversible under ADR-0056 and cannot release
source exclusion or quarantine. Unknown/malformed/foreign targets reject before receipt lookup.

## 2. Independent floor and derived SQL projection

Use an installation-local, private **independent SQLite policy ledger**, outside the main database
and outside its whole-state bundle. It contains metadata-only bindings, monotonic exclusions and
durable operation receipts. Its fixed installation binding is independent of whichever database
path is selected. An old main DB cannot select, replace, create or downgrade its own policy floor.
The floor has an explicit independently versioned Alembic migration stream and strict schema
validation; it is not a second persistent self or a general event-store rewrite.

For bounded SQL joins, the main DB may carry a **derived** disposition mirror and floor watermark.
Only SourcePolicyManager's reconciliation path writes it. It is not a competing authority and
cannot grant eligibility when the trusted floor is missing, mismatched, unreadable or ahead.
Mirror verification is a short local read; no per-root provider/network call is added. Queries
must filter before limits and cover every exact edge, using indexed source/disposition joins.

An empty floor can be explicitly initialized for a genuinely new installation. An existing
activated/history-bearing DB needs explicit metadata-reviewed enrollment with its trusted
installation/lineage; missing floor never triggers an implicit empty replacement. Pre-policy
backups require reviewed binding to the existing trusted floor, rather than automatic inference
from path, checksum or an identity string alone. Floor/binding/lock files and sidecars are reserved
targets for ordinary artifact export/restore and cannot be overwritten by those commands.
An already enrolled lineage whose floor is missing cannot initialize a zero-generation
replacement. Initial pre-policy enrollment is a separate explicit trusted action; an old schema
or missing main-DB enrollment flag cannot prove that the lineage was never enrolled. Missing
trusted continuity/binding requires policy recovery, not relabeling an old DB as a fresh install.

The guarantee assumes trusted OS storage and cooperating Satori processes. Permissions and
fixed-inode validation reduce accidental replacement; they do not defeat a hostile same-UID
writer or rollback of the entire trusted installation. Encryption, MACs and signatures can prove
integrity/origin, but an older valid signed floor is still older. Stronger recovery requires a
trusted latest-floor recovery/monotonic anchor contract at the access/key/device gate. No such
anchor is claimed here. Cross-machine floor/key recovery remains a separate prerequisite.

## 3. Exclusion ordering, crashes and bounded synchronization

There is no distributed transaction between the independent floor and main DB. The exclusion
**linearizes at the durable floor commit**:

1. Validate canonical scope, metadata review, expected generation and durable request binding.
2. Commit the exclusion and receipt in the floor's own transaction.
3. Materialize the main eligibility projection, then route deterministic owner repair proposals.
4. Record each completed phase and return either complete or `exclusion_committed_repair_pending`.

A main failure after step 2 never rolls the floor back to allow. The source is excluded even if
repair is pending; retry resolves the original receipt and resumes idempotent work. An ambiguous
floor write is resolved from its request binding before any new operation. Unresolved/corrupt
policy fails closed. Floor/schema/binding/mirror lag blocks protected runtime until bounded
reconciliation; affected-owner repair lag continues to withhold that owner's affected content.
Unrelated content can resume once the authoritative eligibility mirror is current. No temporary
allow or fallback to an older mirror is permitted.
Each affected owner/aggregate/key also has an explicit repair-generation gate. Fresh same-key
formation cannot treat a hidden ACTIVE aggregate as absent or merge withdrawn support while
retirement is pending; reject with `owner_reconciliation_required` and resume the typed repair.
The floor/mirror watermark is not proof of completed owner repair.

Protect the check→commit race with an infrastructure-owned, fixed-inode **cross-process shared/
exclusive barrier**, not merely a sampled epoch. All cooperating paths acquire this barrier
**before** any floor/main SQLite transaction or writer:

- Short shared lease: verify floor/mirror, select a complete footprint, admit dispatch, or reprove
  the actual footprint/source/owner versions and commit while the lease remains held.
- Exclusive lease: advance the floor and materialize its eligibility mirror. Owner reconciliation
  uses the same established lock order and each owner's normal typed CAS/UoW.
- Release leases before provider/network waits. After inference, acquire a fresh lease and
  recheck; never hold a lock or transaction for generation time.

A commit ordered before exclusion is historical state. A late commit ordered after exclusion
must reject affected inputs, including uncited provider/context dependencies and ABA changes.
Dispatch linearizes at protected admission; already admitted/in-flight payloads cannot be
recalled. If exclusion affects foreground generation before finalize, discard the candidate,
commit no assistant text or tentative affect, and report a typed source-admission failure through
the interaction lifecycle. Do not use Character rewriting, its self-consistency reason enum,
another judging model or automatic generation loops. The accepted user input remains honest
pending/failed history under InteractionLog policy.

Dispatch admission is single-use for one immediate attempt. Queued/deferred dispatch, retry and
fallback need fresh admission; an old permit does not indefinitely authorize later transmission.
Disclosure/delivery likewise linearizes at a protected admission, rechecked for delayed delivery
and replay. A payload already admitted to delivery before exclusion is in flight; physical OS/
network consumption cannot be revoked. No candidate using a newly excluded footprint is admitted
to finalize or a subsequent disclosure after that linearization point.

Timeouts, contention and storage failures are bounded, content-free failures. No unsupported lock
adapter silently becomes a no-op. Implementation must verify the primary macOS and supported
Linux adapter semantics with actual process races before claiming this contract works.

## 4. Evidence, disclosure and conservative legacy behavior

Accepted evidence and protected content dependencies remain different concepts. Before dispatch,
record the bounded complete footprint of **all actual inputs**, including prior dialogue,
retrieved episodes/claims, selected User/World/position/inclination text and paired assistant text.
Record IDs/policy/version/proof status only; never prompts, raw text or chain-of-thought. Provider
citation selection is not the footprint. Later replies/extractions reference immutable earlier
input versions/receipts and their dependencies. Prefer bounded direct dependency receipts with
indexed reverse closure over flattening an ever-growing root union into every turn. Each existing
owner records its payload's receipt atomically; source policy materializes derived eligibility
from those receipts without writing that owner's content. Cycles, missing versions or incomplete
closure are UNKNOWN. Exclusion propagation/reconciliation is bounded and resumable; its mirror
watermark cannot advance until the protected dependency closure is materialized.
New payload/input versions can appear without advancing policy generation. A missing derived
eligibility record is UNKNOWN until complete admission proof/receipt at the current generation
or materialization exists; a current floor watermark alone does not certify new content.
Never truncate dependencies and mark them COMPLETE; omit optional projections or fail the required
operation with explicit OVERFLOW. No global unbounded history walk on every foreground request.

An affected paired reply is unavailable to recent history and idempotent text replay. Legacy
replies/derived text without complete reconstructible dependencies cannot be declared COMPLETE
merely because their current direct root is allowed. For explicitly recognized pre-policy text,
choose conditional compatibility admission only when its possible canonical source-lineage is
proved, the trusted current floor contains **no exclusions anywhere in that entire lineage**,
and all independent canonical/scope checks pass. Record UNKNOWN plus
`legacy_lineage_without_exclusions`; this is not evidence or a completeness certificate.
New descendants retain the UNKNOWN dependency and this condition, never upgrade it to COMPLETE.

The first exclusion in that lineage invalidates this conditional admission for every such
unknown payload/descendant, including across sessions/counterparties using global text. An unknown
lineage, missing new input-version receipt, incomplete new payload or OVERFLOW never receives this
legacy exception. At commit/delivery recheck the condition under the same admission lease.
Counterparty scope alone cannot narrow possible global dependencies. Preserve retained rows and
metadata explaining withheld projections; continuity may degrade after the first exclusion, but
enrollment alone must not falsely certify old provenance or silently discard all usable history.

Any affected text-bearing aggregate is withheld as a whole until its owner proves a clean current
text revision. A surviving support edge does not certify the old wording. SourcePolicyManager
never edits quotes, summaries, values or propositions. New clean formation uses only eligible
inputs through the existing managers; it cannot reuse hidden content as evidence or template.
Ordinary inspectors obey the same disclosure contract; raw offline state transport/forensic
retention are explicit separate purposes, not provider/context access.

Historical numeric personality/affect/mood/relationship and inclination trajectories, budgets and
their retained audit remain. The operation does not undo experience, rebase drift, refund caps,
reset relationship or partition global self by counterparty. Future mutations still require
eligible actual inputs. Human restatement in a new canonical message is new untrusted user input,
not proof that an old source was erased or independently verified.

## 5. Owner routing and active keys

Immediate disclosure withholding is enforced by source admission. Deterministic state repair
remains with each existing owner and carries policy generation/request ID/CAS/audit:

| Owner | Required behavior after exclusion |
|---|---|
| InteractionLog | Retain canonical history; gate raw/paired/recent/replay disclosure and finalize using full dependencies |
| MemoryManager / derived index | Gate every actual formation dependency and exact root; withhold affected summaries/quotes/vectors; archive is not withdrawal |
| SemanticMemoryManager | Withdraw support through a typed operation; retire affected unsafe active payloads with an honest source-withdrawal reason and release active keys; preserve evidence/revisions, permit freshly grounded replacement |
| User/WorldModelManager and PositionManager | Owner-routed source withdrawal/retirement with existing lifecycle/revisions; no generic writer and no assertion that the fact was disproved |
| Reflection | Fixed eligible evidence plus eligible actual position/inclination/context inputs; retain old receipts, prevent replay of excluded proposal payloads into owners |
| PositionManager's existing inclination policy | Retain numeric trajectory and permanent aggregate identity; record withdrawn support/disclosure hold without score delta or refund; do not pretend a hidden key permits duplicate creation |
| Personality / Emotion / Relationship owners | Preserve historical state/caps; reject new proposals/finalization/backlog using excluded actual inputs; no arithmetic or Character tuning |

Excluded pending reflection runs and undecided relationship backlog roots receive honest typed
terminal exclusion outcomes through their owners. They cannot stay nonterminal forever and block
later unrelated useful work. A typed skipped source is not new relationship/self evidence.

Initial retirement is deliberately conservative for unsafe text. An owner-specific clean text
revision/re-admission requires proof independent of withdrawn sources; remaining evidence alone
does not satisfy it. Inclinations have no current retracted lifecycle and a permanent unique key:
do not mechanically copy knowledge retirement into them or add another personality store.
While clean re-admission is unavailable, the affected text/target stays unavailable with explicit
metadata rather than accepting a merge containing withdrawn historical support.
Fresh replacement knowledge starts from eligible evidence and the existing formation caps; it
does not inherit the withdrawn aggregate's confidence or merely copy its wording. Exact inverse
confidence reconstruction is not available from current historical edge metadata.

## 6. Character, latency and degradation

Character receives the existing bounded **eligible** memory/model/own-state projections and
unavailable/empty capability metadata. It cannot select the floor, authorize disclosure, inspect
withdrawn roots or mutate policy. No V31/V33/V34 renderer, prompt, tuning corpus or provider choice
is part of this contract. Any extra disclosure-status information is an application-owned typed
projection requirement, to be supplied independently; changing Character is not a workaround.

Control, admission and repair add zero LLM calls. The implementation benchmarks local floor/
mirror checks, barrier contention and affected-owner reconciliation separately from foreground
provider time. Bounded indexed queries and reused verified generations avoid per-message floor
scans. Quality gates include contention distributions and cold/warm native runtime decomposition;
fixed latency budgets are chosen from measured baseline, never guessed into the domain policy.
An excluded/unknown optional projection degrades to unavailable without a foreign fallback.
Untrusted/unavailable authoritative policy blocks protected operations, not a silent empty-memory
success. Existing committed replies remain stored; later delivery/replay also obeys eligibility.

## 7. Implementation slices and completion gate

Implement in reviewable parts under this one product vertical:

1. Authoritative floor/binding/migrations, strict metadata operator contracts, derived mirror and
   real cross-process barrier; provider construction remains closed when admission is unavailable.
2. Complete bounded input footprints and every read/dispatch/final-commit/replay consumer,
   including background backlog, paired assistant extraction and reflection's extra context.
3. Owner-routed support withdrawal/retirement and exact key/retry handling; independent same-
   installation recovery admission and explicit enrollment/reconciliation.
4. Integration, injected failures/races, realistic CLI/restart/old-backup drill, full Foundation,
   metadata-only native runtime smoke and updated docs/progress.

No public source-exclusion apply command may claim success while a consumer can bypass admission.
A ledger-only intermediate is not a finished privacy feature; protect runtime until integration
is complete. Design acceptance of this ADR does not close the runtime engineering gate.

Acceptance requires positive unaffected own recall and useful newly grounded knowledge, scoped/
quarantined denials, absence of excluded input at every captured provider surface, no uncited-
dependency late write, no stale-context assistant finalize, complete/unknown/overflow handling,
semantic/model/position retirement and active-key reuse, retained inclination/self numeric history,
legacy continuity under the verified no-exclusions condition and its complete invalidation on
the first lineage exclusion,
same-request replay and each write/crash fault, actual cross-process ordering, restart, current
backup and **pre-exclusion old-main-backup operational admission**. Missing/replaced/untrusted floor
must deny before providers/jobs; offline restoration remains byte-preserving. Full-installation
rollback protection is explicitly excluded until a trusted monotonic-anchor/key recovery gate.

Physical erasure is a later reviewed operation covering raw pairs, summaries/quotes/vectors,
knowledge/revisions, reflection payloads, receipts/FKs and existing archives. Access/principal,
disclosure grants, encryption/key recovery and truthful erasure limits remain 14.4 production
acceptance prerequisites. There is no automatic transition to another Stage.

## Alternatives

| Alternative | Reason rejected |
|---|---|
| Episode archive or session scope flag as exclusion | Cannot govern all canonical/uncited roots; confuses retention with attribution |
| Tombstone only inside the main DB/backup | Restoring an older snapshot removes the later revocation |
| Independent floor query followed by an unlocked commit | Exclusion can linearize between check and write |
| Hold a SQLite/OS lock through inference | Adds provider-time serialization and outage coupling |
| One transaction spanning both stores | Crash/atomicity guarantees depend on storage modes; unnecessary distributed coordination |
| Encryption/signature alone proves latest policy | Authentic older state still allows resurrection without trusted freshness |
| Delete edges or rewrite old generated wording centrally | Violates owner authority, loses provenance and cannot prove semantic sanitization |
| Global personality/relationship reset | Erases experience and refunds bounded history instead of enforcing disclosure |

## Runtime realization — 2026-10-03

The application source-policy ports are implemented in the modular monolith. Main migration
`0016_source_policy` stores the derived binding/excluded-root mirror, immutable input/payload
receipts, standalone owner-withdrawal decisions and separate owner-repair generations. An
independent `policy_0001` migration stream owns the installation ledger; neither the main backup
nor the selected main DB can replace it. Binding/ledger/lock and sidecars are reserved artifact
targets. Newly activated installations enroll explicitly through activation; populated pre-policy
stores require `sources enroll --reviewed`. Operational composition always binds the trusted floor
before constructing providers. Unmanaged pure-core test fixtures are explicit TEST-only composition;
they do not bypass the gate for an already-bound DB.

Each payload records the entire actual input window and immutable owner-version handles, including
paired assistant text. Exclusion materializes a bounded topological dependency DAG (at most 100,000
payloads); missing parent/version, cycles and overflow fail closed. Direct input footprints are
bounded to 512 roots and 128 payload handles. Filtering occurs before SQL limits. Dispatch rechecks
the current top-level selected aggregate versions and episode ACTIVE status after scheduler/pool
waits, for every retry/transport attempt. Final commits reprove the same top-level versions;
immutable historical parents retain their historical semantics, including archived semantic support.
No lease spans inference. There is no Character-specific policy or output editing.

Reviewed legacy enrollment independently proves every accepted canonical evidence edge and owner
scope. Such payloads retain known roots but remain UNKNOWN because uncited old inputs cannot be
reconstructed. Unproven legacy text is withheld even at generation zero; the normal owners retire
unsafe active keys and terminalize invalid backlog. Proved UNKNOWN compatibility is available only
while the entire lineage has zero exclusions. The first exclusion withholds all such payloads and
their descendants; no citation subset upgrades UNKNOWN to COMPLETE.

Semantic/User/World/position owners retire unsafe text through their own typed operations;
confidence/evidence/revisions remain historical. Inclinations keep their permanent keys and numeric
trajectory with a disclosure hold. Affect/personality/relationship numeric history and spent caps
remain: exclusion governs text/evidence use and new proposals, without undoing past experience.
Mirror repair commits before independently resumable owner repair. A failed owner loses its repair
watermark; hidden ACTIVE knowledge cannot accept a fresh same-key merge while repair is pending.

Native macOS process-lock checks are required and recorded separately from Linux: the Linux adapter
is implemented but actual Linux process execution remains unverified on this host. Physical erasure,
authenticated access, encryption/key recovery, cross-machine policy recovery and a trusted freshness
anchor remain later gates. Detailed operator usage and residual data are in
[source policy](../source-policy.md); verification is in [progress](../progress.md).
