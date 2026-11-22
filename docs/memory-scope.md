# Counterparty-bounded Memory — Checkpoint 14.4.2a

Status: implemented and engineering/integration/native-smoke verified. Accepted design: [ADR-0055](
decisions/0055-canonical-memory-scope-and-legacy-quarantine.md). This is the first production
vertical of 14.4.2, after the verified 14.4.1 recovery slice. The current workstream boundary and
completed verification records are in `progress.md`.

Subsequent archival extension: [ADR-0056](decisions/0056-owner-controlled-episodic-archival.md)
and [memory lifecycle](memory-lifecycle.md) preserve already admitted semantic support through the
same full canonical proof independently of episode status. NEW episode intake remains ACTIVE-only.
The historical 2a gate below remains its verification record; lifecycle does not infer scope,
release quarantine or add a new Character contract.

## Product behavior and contracts

Two people can use the same canonical Satori identity without their raw history, episodes or
user-subject semantic facts entering one another's memory context. Own recall remains useful;
unknown or malformed provenance is excluded. This layer establishes attribution, not authenticated
multi-user access. Counterparty selection is still trusted local configuration.

`MemoryScope(identity_id, counterparty_id)` is an immutable typed domain value. Composition binds
the configured counterparty to the canonical identity; required query/index/semantic contracts
carry that scope. Compatibility history/recent/close/episode reads may omit the argument only
because composition injects a mandatory scope provider. Omission never means a global query.
Internal post-response processing derives scope from the committed source, then rechecks it at
commit. A caller-supplied expected scope can only restrict that admission.

```text
verified canonical session + completed user interaction
  → exact complete episode evidence → eligible derived index
  → same-scope semantic source window and aggregate
  → all-evidence scoped recall → bounded untrusted provider projection
```

Every source edge must reach the same canonical user interaction/session and contain a literal
quote. Every semantic edge must reference that complete episode/evidence/root chain in the same
partition. A single selected eligible edge cannot admit a mixed aggregate. SQL checks run before
candidate selection/provider input; pure Python verifies aggregate key namespace before mapping
into downstream context. No custom database function or provider-specific policy is introduced.

Semantic keys namespace the existing normalized claim identity with SHA-256 of compact ASCII JSON
`[identity_id, counterparty_id, original_key]`. Equal facts for two people remain independent.
Existing confidence caps, exact merge, correction/conflict and revision policy apply within a
partition. Foreign correction references and generated-ID collisions cannot mutate another owner
record. This reuses the existing manager and write path.

Foreign/unverified explicit sessions and foreign idempotency replays reject before stored reply
return, canonical mutation or provider calls. Empty scoped recall retains normal no-memory
behavior; an infrastructure failure retains unavailable degradation. No fallback searches another
person. A post-response extraction/index failure leaves the committed conversation intact and
retains the existing retry behavior.

## Legacy migration, operator inspection and recovery

Migration `0014_memory_scope` adds `conversation_sessions.scope_verified`. Newly owner-created
sessions explicitly set it true. Migration 0007 assigned old sessions `local-default`; that
default alone cannot prove attribution, so existing default-only sessions remain false.
Existing explicit nondefault assignments can be verified. Claims with complete homogeneous
verified roots are rekeyed; mixed/unknown/malformed aggregates are preserved in quarantine.
No automatic reassignment, split, confidence recalculation or historical self reset occurs.
Migration records only counts and reasons in audit.

```bash
uv run --no-sync satori memories scope-status
uv run --no-sync satori history
uv run --no-sync satori memories list
uv run --no-sync satori memories search "a current question"
```

`scope-status` returns typed scope metadata and counts of eligible current-partition episodes,
verified current-partition sessions, unverified identity sessions and quarantined identity
episodes. It contains no user text, quote, memory payload or foreign counterparty ID. Quarantine
means preserved storage with inadmissible lineage, not erased or archived content. Explicit
legacy attribution/review is future work; ordinary commands do not reveal quarantined payload.

Whole-state recovery admits exact predecessor 0013 and current 0014 schemas through separate
clean migration references. Offline restore preserves archived database bytes and audit, does
not migrate or dispatch work, and keeps 0014 verification flags/claim namespaces. Explicitly
selecting an old restored DB for normal application commands invokes ordinary migration and the
same quarantine policy. Unknown revisions and weakened constraints fail closed. Downgrade cannot
collapse two active scoped facts into one global namespace; it rejects before changing keys.

## Ownership, Character boundary and remaining dependencies

InteractionLog owns session attribution; MemoryManager owns episodes; SemanticMemoryManager owns
claims; the index remains derived. Positions, inclinations and personality remain global Satori
self, with new source processing rejecting quarantined roots. Existing state is not recreated
per person or retroactively erased. Their quotation-free propositions/topics can still contain
sensitive derived wording: disclosure policy requires the later principal/privacy slice.

Reflection retains ADR-0025/0026's earlier-evidence-root contract: formation T2 may refer to a
canonical exact user quote from T1 in the same verified session. Its separate deterministic
proof admits every root in that session; mixed/unknown evidence is excluded. This does not
broaden current episode creation or ordinary retrieval/semantic lineage. Canonical position
edge equality is also checked before reflection selection and on fixed-source reload.

Character receives its existing bounded memory projection after admission. The slice changes
no renderer, prompt, model, appraisal policy or expression tuning and requests no new Character
capability. Future lifecycle code must share an immutable source-eligibility port rather than
alter Character or write another owner's state.

The next vertical adds explicit source lifecycle/exclusion and owner-routed downstream invalidation.
Physical erasure needs dependency preview, minimal tombstones/audit and a recovery policy that
prevents resurrection. Local principal, grants, sensitive projection/export and encryption remain
14.4.3. This vertical alone does not establish production privacy readiness or open Stage 15.

Subsequent source exclusion and selected body handling preserve this scope contract.
[ADR-0063 / 14.4.2f](erasure-dialogue.md) introduces actual terminal canonical/assistant bodies
and separate metadata-only history headers. Independent available USER sources remain usable
when their dependent assistant is erased; unavailable pairs never enter recent/provider history.
Exact pre-erasure retained-row witnesses preserve historical attribution only. They cannot create
new literal quotation proof, fresh learning or cross-counterparty disclosure after canonical NULL.

## Verification

New tests cover forced-similarity foreign recall denial, positive own context, same-fact independent
aggregates, isolated correction, malformed/mixed evidence, scope changes before commit, target-ID
collision, foreign correction references, pre-provider quarantine rejection and no-write replay/
session denial. Migration tests preserve canonical history/state while proving default quarantine,
exact lineage, rekeying, reverse migration and collision refusal. Recovery tests admit 0013/0014,
reject schema/version spoofing and compare restored bytes to the archived snapshot.

`tests/memory_scope_scenario.py` exercises actual composition and fresh-process CLI history,
scope-status, export, preview and restore against disposable synthetic data. Captured provider
requests prove no foreign text/IDs on memory paths; restart/restore retain both positive recall
and exclusion. Private output contains only counts, timings, call counts, versions, hashes and
assertion results. Injected fakes avoid network/credentials; they establish isolation/integration,
not sampled model quality or real-inference performance. Final results are recorded in `progress.md`.
