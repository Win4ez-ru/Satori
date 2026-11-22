# ADR-0056: Owner-controlled episodic archival

- Status: Accepted
- Date: 2026-10-03
- Authority: explicit production continuation of Foundations 14.4; completed 14.4.2a gate
- Supersedes: ADR-0012 ACTIVE-only lifecycle and ADR-0055's ACTIVE requirement for retained
  semantic support only; all canonical scope/exact-root proofs remain required
- Related: ADR-0013/0014, ADR-0025/0026, ADR-0053/0055

## Product scope

Open **14.4.2b.1 — Memory Archival** after the verified canonical-scope vertical. Explicit local
operator controls archive/reactivate one proven scoped episode. Archive excludes episode detail
from ordinary episodic recall, indexing, new semantic extraction and new episode-based reflection.
Reactivation is explicit. Both are deterministic MemoryManager transitions with no provider call.

Archive retains canonical dialogue, exact evidence, summary, importance/confidence, formation
decision, stored compatible vectors and already formed semantic/self/model/relationship state.
It is not canonical-source exclusion, support withdrawal or physical erasure. No automatic decay,
new summarization/consolidation model, independent semantic search or Character capability is added.

## Owner and mutation contract

The episode carries a positive monotonic `aggregate_version`, initially one, and
`ACTIVE | ARCHIVED`. MemoryManager evaluates a typed local proposal with memory ID, proven scope,
target status, expected version, request ID and preview digest. Metadata-only preview binds policy,
scope, target, current status/version and precise retained-data effects. Apply must match it.
Wrong scope, unknown/unproven target, stale version, changed request payload or mismatched preview
reject without partial mutation. Reactivation cannot repair attribution or release quarantine.

One existing MemoryManager UoW commits status/version CAS, append-only lifecycle decision and
content-free audit. Decisions record explicit local operator approval, reason, time, policy,
request ID and before/result versions/status. Scope proof precedes receipt replay. Same request
returns its original receipt without reapplying, even after a later inverse transition; result
also reports current status/version. Repeated target status is a recorded no-op, not a new version.
This local operator label is not an authenticated principal or technical grant.

## Admission versus retained proof

New formation sources/windows/backfill, episodic retrieval/indexing and reflection episode inputs
remain ACTIVE-only with complete verified scope proof. Existing semantic support and targets use
the same exact complete canonical proof independently of episode lifecycle. Archival does not
withdraw already admitted evidence or free semantic identity: keys, confidence, status and past
revisions remain unchanged. A fresh ACTIVE root for the same fact merges through the existing
manager rather than colliding with the still active key.

An archived evidence edge in a new merged plan must be an exact already owned edge of the current
admitted claim. Newly added edges require ACTIVE admission. A blanket proof-only source resolver
is forbidden. Foreground semantic recall retains its selected ACTIVE episode bridge: an
archive-only claim is inspectable but has no new relevance anchor; another ACTIVE support can
provide one. There is no global semantic dump or new provider retrieval policy.

Stored vectors remain derived and are ignored while archived, so reactivation can reuse an
unchanged compatible vector. Bulk/single index and semantic formation recheck their full source
snapshot/version before commit, including archive→reactivate ABA. Post-response replay of an
archived formation decision intentionally skips episode index/semantic work; it never resurrects
the episode or repeatedly reports an expected lifecycle exclusion as a provider failure.
Previously admitted/in-flight provider payload cannot be recalled by archival. Checks govern new
admissions and late commits; no retroactive disclosure guarantee is made.

## Persistence, recovery and interface

Migration 0015 adds episode version, lifecycle constraints and durable owner decision records.
Downgrade refuses while archived rows or lifecycle decisions would be lost. Recovery explicitly
admits exact 0013/0014/0015 references and preserves archived bytes/status/versions/history offline.
Restoring an older archive honestly restores its earlier lifecycle; archive is reversible snapshot
state. Anti-resurrection for future excluded/erased sources requires a separate trusted policy
floor/recovery contract and remains unimplemented. A checksum cannot prove future revocations.

Ordinary scoped episode reads stay ACTIVE-only. Explicit archived inspection is scoped and labeled
with status/version. CLI preview/apply exposes only operation metadata; local operator invokes
the controls explicitly. Character continues to consume the same already eligible bounded memory
projection. No renderer, prompt, model, affect/relationship policy or self owner is tuned.

## Acceptance

Archive/reactivate/no-op/replay/CAS and payload binding are tested through the real UoW, including
failures at every write point. Foreign/quarantined targets reject without calls/writes; version
and history survive restart and whole-state recovery. Archived episodes are absent from ordinary
reads/search/precheck/backfill/reflection, while other own memory remains useful. Original source/
proof/formation/fact state is preserved. Fresh same-fact evidence merges into the same semantic
claim, and malicious newly archived evidence is rejected. Late index/semantic commits fail closed.
Complete frozen-wheel Foundation checks, actual CLI scenario and documentation close this vertical.
