# Personality evolution specification

Status: accepted 2026-08-23. Implementation and measured acceptance evidence are recorded in
[`performance/stage-14.md`](performance/stage-14.md).

Stage 14 implements [ADR-0027](decisions/0027-bounded-personality-evolution-and-checkpoint-restore.md).
This document is the executable contract for proposal formation, owner decisions, persistence,
restore, expression and acceptance. `personality.md` remains the product-level character source.

## Boundary

```text
canonical long-period roots
→ personality-purpose Reflection V3 fixed set
→ strict trait + direction proposal
→ PersonalityManager evidence/drift/budget policy
→ atomic evidence + revision + checkpoint + outcome + audit
→ live personality read on future turns
```

`PersonalityManager` is the only post-activation writer. Reflection owns run lifecycle, not traits.
The provider chooses neither magnitude nor state. Values, relationship, affect, inclinations and
generated text have no personality write path.

## Versioned contracts

V1 implementation versions:

- personality evolution schema: `1`;
- personality policy: `1`;
- personality evidence normalization: `1`;
- checkpoint hash schema: `1`;
- Reflection personality schema/policy: `3`;
- Personality Expression Projection: `2`;
- runtime character/context manifest: `16`.

Existing Reflection V1/V2 runs retain their original wire, hash and resume behavior. Only a V3
run with purpose `personality_evolution` can contain a `PersonalityChangeProposal`.

## Provider-neutral proposal

```text
PersonalityStateReference
  identity_id
  aggregate_version
  canonical_trait_keys[15]

PersonalityChangeProposal
  trait_key
  direction=increase|decrease
  confidence
  citations[source_id, role=support|counterevidence]
  expected_personality_version
```

The state reference intentionally omits current and baseline values. A proposal has no delta, new
value, score, patch, budget or checkpoint field. One personality-purpose run has zero or one
proposal; zero is preferred to weak synthesis.

## Source eligibility and selection

The repository resolves only exact Stage 12 canonical leaves: position evidence or evidence of an
active important episode. Every V3 source includes edge, root and upstream lineage identity. V3
stores no affect attachment.

Hard exclusions before inference and at owner application:

- any non-user, incomplete, foreign-identity or hash-invalid root;
- assistant/provider/reflection/current-state lineage;
- a root already accepted as inclination evidence;
- direct or modal personality assignment;
- user desire that Satori become a trait;
- a user's stable-character evaluation of Satori;
- first-person user trait self-ascription used as an invitation to mirror;
- explicit relationship/love/trust/closeness/exclusivity/obedience material.

Selection is stable and time-diverse. Policy V1 passes at most the first 256 canonically ordered
source candidates (at most about 128 KiB of bounded 512-character quotes) into the owner
selector reduces that reservoir to the immutable 12-source / 4,800-character fixed-set cap.
The repository order is deterministic and the selector result is independent of input order.

Selection then:

1. normalize NFKC/case/whitespace, fold Russian `ё` to `е` and calculate exact signatures;
2. cluster exact and deterministic near-duplicates by token and character 3-gram similarity;
3. group by UTC calendar month and upstream lineage;
4. choose oldest-to-newest month round-robin, at most two counted roots per lineage;
5. stop at twelve sources or 4,800 quote characters;
6. require the full structural gate before a provider call.

The gate is eight roots/messages/interactions/signatures/clusters, six sessions, six ISO weeks,
four calendar months, four lineages and at least ninety days of observation. The fixed source set
belongs to the personality consumption namespace; completed general runs do not consume it.

## Owner decision

The owner validates, in stable order:

1. schema, identity, canonical trait key and expected aggregate version;
2. finite provider confidence of at least `0.80`;
3. exact fixed-run membership and at least 80% source coverage;
4. source eligibility, root/hash/lineage validity and global root non-reuse;
5. all structural diversity gates;
6. at least eight supporting sources and support share at least `0.80`;
7. per-trait ninety-day and global thirty-day cooldowns;
8. exact step against value, rolling, lifetime, activation and approved-checkpoint budgets.

One accepted delta is exactly `+0.005` or `-0.005`. It is never clamped or scaled. Rejection reason
codes are closed and include invalid/stale/source/diversity/support/cooldown/value/budget families.
Every rejection is terminal for the proposal and leaves personality evidence and state unchanged.

Decision confidence is:

```text
min(
  provider_confidence,
  0.80
    + 0.015*min(4, supporting_roots-8)
    + 0.010*min(2, sessions-6)
    + 0.020*I(span>=180d),
  0.90
)
```

It is stored for explanation and never changes the delta.

## Drift ledger

Every comparison uses the complete sorted canonical fifteen-trait vector:

```text
linf = max(abs(left[key] - right[key]))
l1   = sum(abs(left[key] - right[key]))
path = sum(abs(applied evolution deltas))
```

V1 limits:

| Metric | Per trait | Global |
|---|---:|---:|
| One event | `0.005` | `0.005` |
| Rolling 365-day evolution path | `0.015` | `0.060` |
| Lifetime evolution path | `0.080` | `0.300` |
| Activation endpoint distance | `linf <= 0.080` | `l1 <= 0.300` |
| Last approved-checkpoint distance | `linf <= 0.020` | `l1 <= 0.050` |

Reversal reduces endpoint distance but does not refund path. Restore is not evolution spend and
also does not refund prior spend. All arithmetic is deterministic and provider-free.

## Persistent records

```text
PersonalityEvidence
  evidence/personality/trait/direction IDs
  reflection run/proposal/source IDs
  edge/root/session/counterparty/lineage IDs
  content hash + normalized signature
  observed/accepted times

PersonalityRevision
  revision ID, kind=evolution|restore
  before/after aggregate versions and trait vector diff
  exact applied delta for evolution
  decision confidence, policy/reason
  checkpoint/evidence/outcome lineage
  drift and path metrics

PersonalityCheckpoint
  checkpoint ID/hash/schema
  identity + source aggregate version
  kind=activation|evolution|restore|manual
  complete immutable trait vector

PersonalityCheckpointApproval
  approval ID, checkpoint ID/hash
  expected aggregate version
  explicit local reason + approved time

PersonalityRestoreEvent
  restore ID, source checkpoint/hash
  before/after aggregate versions
  changed trait diffs, explicit local reason/time
```

Raw source quotes remain canonical in their owning tables and are not copied into personality
evidence, checkpoints, revisions, audit or export.

## Atomicity and replay

For an accepted V3 proposal, one Personality Unit of Work writes current trait value/version,
evidence, evolution revision, resulting checkpoint, reflection outcome and audit. Rejection writes
only outcome and audit. Unique proposal/policy, evidence-root and checkpoint-version keys make
replay a no-op. An optimistic expected aggregate version prevents concurrent lost updates.

Restore is a separate local owner transaction. It verifies checkpoint identity/hash/completeness
and expected current version, writes the restored current vector at a new aggregate version, an
append-only restore revision/event, a resulting checkpoint and audit. It never deletes history or
changes baseline values.

## Checkpoint approval and downgrade

The activation checkpoint is approved by definition. Evolution/restore checkpoints are
restorable but do not reset the checkpoint-distance budget. A new budget origin requires an
explicit local approval record after inspection of state, drift and anchor behavior.

Migration `0012_personality_evolution` backfills only the activation checkpoint and nullable v16
manifest metadata; it changes no current trait, baseline or value. Downgrade stops after any V3
run or Stage 14 owner record so provenance cannot be discarded beneath evolved values.

## Expression Projection V2

The current and baseline vectors deterministically produce the original five soft guidance
composites plus grounded optimism. Existing baseline wording is unchanged. When the absolute
relative composite change reaches `0.005`, at most the two strongest stable cues enter generation
as `slightly_stronger` or `slightly_softer` closed wording. No number, evidence, budget or history
enters provider context.

Context manifest v16 stores personality aggregate version and cue codes/directions. This proves
which personality projection shaped a response and lets checkpoint comparisons replay the same
deterministic projection.

## Acceptance

Automated acceptance is split into independently failing families:

1. strict Reflection V3 and V1/V2 compatibility;
2. source assignment/mirroring/relationship/affect/inclination isolation;
3. root/session/week/month/lineage/near-duplicate diversity boundaries;
4. exact confidence, support, delta, cooldown and every drift/path budget;
5. long-horizon reversals, ten-year attacks and provider replacement;
6. atomic accept/reject/replay/conflict/restart and write-point failure;
7. checkpoint hash/approval/compare/restore/export/tamper behavior;
8. context v16 cue selection and unchanged baseline expression;
9. paired opposite-user-pressure trajectories with exact equal state and zero deterministic
   alignment correlation;
10. full prior identity, affect, character, relationship, memory, position, reflection and
    inclination regressions.

Manual acceptance reviews a synthetic months-long trajectory, every reason/evidence/checkpoint,
before/after anchor conversations and restore. Identity, values and non-target anchors must remain
recognizable. Any model or expression change repeats the applicable character corpus and real-local
sessions when Ollama is available.
