# ADR-0055: Canonical memory scope and legacy quarantine

- Status: Superseded in part
- Date: 2026-10-03
- Authority: explicit user instruction to continue Checkpoint 14.4 as a production workstream
  through architecture/implementation/integration/tests/realistic-scenario/documentation slices
- Supersedes: ADR-0013's unpartitioned eligibility and ADR-0014's installation-global semantic
  namespace only; ADR-0053 scheduling is extended to the explicitly authorized 14.4 workstream
- Related: ADR-0008, ADR-0012–0014, ADR-0020/0022, ADR-0046/0053
- Superseded by ADR-0056 only for ACTIVE admission of already retained semantic support.
  Canonical scope, exact complete proof, namespace and quarantine remain required; NEW sources
  remain ACTIVE-only. Historical decision below records the 2a contract.

## Scope and ownership

Open 14.4.2a, **Counterparty-bounded Memory**, after the passing 14.4.1 engineering/manual gate.
Only this vertical slice is implemented first; lifecycle/erasure and local principal/encryption
follow their own completed-slice records within the authorized 14.4 workstream. No Stage 15,
external source, Character tuning, A/B experiment, model change or real-data erasure is opened.

Immutable `MemoryScope(identity_id, counterparty_id)` identifies canonical attribution. It is
not an authenticated principal or grant. Resolve it from canonical sessions and complete user
roots, never from Character, relationship trust, retrieved text or an LLM proposal. Episodes
derive their partition from their existing canonical source; no duplicate memory/self owner is
created. InteractionLog continues to own sessions, MemoryManager episodes, SemanticMemoryManager
claims, and the index only derived vectors. Positions/inclinations/personality remain global self.

## Admission and integration

Ordinary history, episodic/semantic reads, retrieval and explicit indexing/backfill require the
current scope. Foreign explicit sessions and foreign idempotency replays reject before returning
stored replies or writing a new interaction. A legacy unverified session cannot supply dialogue
context. Public APIs have no wildcard fallback.

Episode/semantic/index work validates canonical source eligibility before any provider call and
again at commit. Retrieval filters before embedding precheck, candidate loading and ranking.
Semantic source windows, conflict/correction working sets and recall use the same proven scope.
Every semantic evidence edge must have complete, valid, same-scope lineage; selecting one eligible
episode does not make a mixed claim eligible. Missing or contradictory lineage fails closed.
No out-of-scope quote, summary, value or root handle enters downstream appraisal/cognition or
foreground projection. Ranking, confidence, merge/conflict and grounding policies otherwise stay.

Semantic structured identity is namespaced by SHA-256 of compact ASCII JSON
`[identity_id, counterparty_id, existing_normalized_claim_key]`. This preserves normalized value
semantics and permits equal facts for two people without merging confidence or superseding one
another. SQL filters admit complete roots first; key checks are pure Python, with no custom SQLite
function, hidden global policy or provider dependency.

## Legacy migration and recovery

Migration 0007 assigned historical sessions `local-default`; the value alone proves no person
attribution. Migration 0014 adds persisted `scope_verified` to sessions. Newly created sessions
are explicitly verified by InteractionLog. Existing non-default IDs are provably post-0007
explicit assignments; they are admitted. Existing default-only sessions remain unverified.
Never assign legacy records to today's configured counterparty.

Rekey only existing claims whose **all** canonical roots prove one verified partition. Retain
claim IDs, values, confidence, status, evidence and revisions. Mixed/unverified claims stay intact
and excluded as whole aggregates: no automatic split, reformation or confidence recalculation.
Record content-free migration counts/reasons in audit. Quarantine is an eligibility outcome;
it does not mean physical deletion, expiry or a change to relationship/personality history.
Explicit attribution/review and lifecycle transitions remain subsequent slice work.

Recovery explicitly admits the frozen predecessor schema 0013 and current 0014 through separate
exact-schema references. It continues to restore validated bytes offline without running jobs or
changing the archived audit. Selecting an old restored DB for normal application use explicitly
runs its ordinary migration; the same legacy quarantine policy applies. Unknown revisions/schema
are rejected, never guessed. Current-schema export/restart/restore preserves scope and exclusion.

## Failure, cost and Character boundary

Scope checks are deterministic local reads/joins. They add no LLM calls, and prechecks can avoid
embedding or extraction calls. Ineligible source processing fails with metadata-only reasons;
retrieval failure retains its existing unavailable/no-memory degradation, without foreign fallback.
Index/semantic failures do not erase canonical dialogue or block an already committed reply.

Global reflection retains the accepted ADR-0025/0026 evidence-root contract: an active important
episode may cite an earlier canonical user interaction in the same verified session as its
formation trigger. Its separate eligibility proof requires every exact root in that verified
session; mixed/unverified roots are excluded. Ordinary episode recall/semantic lineage retain
their stricter current-source contract. This preserves an existing self-owner input rule without
broadening episode creation, semantic evidence or Character policy.

Character receives existing bounded, already eligible memory projections. This slice changes no
renderer, prompts, affect/relationship policy, self owner or expression contract. No new Character
capability is required. Future lifecycle consumers use an immutable source-eligibility port;
Character implementation remains independently replaceable.

Opaque scope is not auth. Global own propositions/topics may contain sensitive derived wording;
this slice does not establish complete disclosure policy or safe shared-account access. Local
principal, source/use/disclosure grants, export policy and encryption remain 14.4.3 gates.

## Acceptance

Two counterparties in one real composition/database retain positive own recall while foreign
history, summaries, claims, quotes and IDs are absent from captured embedding/semantic/appraisal/
foreground requests. Equal facts remain separate aggregates; one person's correction cannot
change the other's values, confidence, evidence or revisions. Foreign session/replay attempts
produce no provider call or canonical write. Malformed/mixed/unknown roots fail closed.

Migration fixtures cover provable/nondefault, historical-default and mixed/invalid evidence;
quarantined history is preserved. Restart and whole-state export/restore retain both positive
recall and exclusion. Existing global own-self tests stay valid. Complete Foundation gate and
metadata-only realistic scenario evidence are recorded before closing this vertical slice.
