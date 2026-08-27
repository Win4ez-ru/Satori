# ADR 0027: Bounded personality evolution and checkpoint restore

- Status: Accepted
- Date: 2026-08-23
- Related: ADR 0002, ADR 0003, ADR 0004, ADR 0005, ADR 0010, ADR 0015, ADR 0020,
  ADR 0021, ADR 0025, ADR 0026

## Context

Stage 14 must permit a recognizable Day-500 continuation without turning ordinary conversation,
user pressure, relationship warmth or provider style into a personality write path. The existing
Stage 2 personality projection already contains immutable activation baselines and is read on
every interaction, but it deliberately has no post-activation owner, history or restore contract.

The Stage 12/13 reflection lifecycle cannot simply enable its existing `personality` observation:

1. `ReflectionOwnerObservation` has free text and source IDs but no exact trait, direction,
   confidence or expected personality version.
2. General completed reflection runs consume their roots weekly. One general run therefore cannot
   reliably assemble the months-long evidence window required for slow personality evolution.
3. Stage 13 affect attachments are not safe trait-direction evidence. Affect appraisal already
   reads current personality; feeding it back would create `personality → affect → personality`.
4. Current context schema v15 renders stable voice instructions but omits the computed guidance
   strengths. Most small trait changes would be numerically real yet behaviorally invisible.
5. Dropping history after a real evolution would leave changed trait rows without provenance.

The decision must therefore close the distance metric, evidence independence, cumulative budget,
checkpoint, rollback, expression and migration gates before any personality mutation exists.

## Decision

### One post-activation owner

`PersonalityManager` becomes the only post-activation writer for the existing identity-global
`Personality` aggregate. `InitialSelfRepository.add()` remains activation-only. Application
orchestration may commit an owner plan but cannot calculate or override a delta.

Values remain immutable. `ValueManager` is not implemented, a personality restore never changes
values, and a value candidate remains disabled until a separate authorization, ADR and evaluation
gate.

### Reflection V3 purpose and consumption namespace

Reflection V1/V2 and their existing general runs remain readable and resumable with their original
schema, policy, source hash and target behavior. New personality work uses Reflection V3 with the
separate purpose and consumption namespace `personality_evolution`.

Purpose is part of the run identity. A root consumed by a completed general run may still be
considered once by personality evolution; a root consumed by a completed personality run cannot
be selected for another personality run. General reflection continues to use its existing source
and cooldown policy independently.

Personality-purpose source selection uses the same canonical Stage 12 allowlist:

- exact `PositionEvidence` rooted in a canonical completed user message; or
- exact evidence of an active episodic memory with importance at least `0.65`.

It does not use current positions, episode summaries, semantic/user/world claims, assistant text,
provider output, affect, relationship state/events, current inclinations, inclination evidence or
reflection artifacts as evidence. A root already accepted as Stage 13 inclination evidence is
conservatively ineligible for personality V1.

V3 source identity adds the upstream canonical lineage (`position_id` or `memory_id`). Its source
hash includes purpose, ordered root/edge/lineage identity and content hash. It deliberately excludes
the optional V2 affect attachment. Personality source rows persist no affect attachment and the V3
provider request receives no affect state or signal.

Before provider inference and again inside the owner, a versioned conservative lexical policy
normalizes Russian `ё`/`е` spelling and rejects direct or modal character assignment, user desire that Satori change, user evaluation of
Satori's stable character, user self-ascription that invites mirroring, and explicit relationship,
love, trust, closeness, exclusivity or obedience material. This is a conservative safety floor,
not a complete semantic proof.

The deterministic time-diverse selector considers a bounded reservoir, groups normalized exact and
near-duplicate content, and chooses at most twelve sources under the existing 4,800-character
budget. Provider inference is ineligible until the fixed set has all of:

| Dimension | V1 minimum |
|---|---:|
| Roots/messages/interactions | 8 each |
| Sessions | 6 |
| Normalized/near-duplicate clusters | 8 |
| ISO week buckets | 6 |
| Calendar month buckets | 4 |
| Observation span | 90 days |
| Upstream lineage groups | 4, with at most 2 counted roots per lineage |

The exact boundary is eligible. One intense session, repeated/paraphrased pressure and one upstream
episode or position lineage cannot satisfy the gate.

Automatic personality checks remain post-response and make no provider call before eligibility.
They allow at most one new personality-purpose run per rolling day and require thirty days since
the last completed personality-purpose run. Explicit local processing may waive that run cooldown,
but never the daily cap, evidence gate or owner mutation policy.

### Strict PersonalityChangeProposal

Reflection V3 accepts at most one personality candidate and no other target in the same
personality-purpose run. The strict candidate contains only:

```text
target_owner = personality
trait_key = one exact canonical trait key
direction = increase | decrease
confidence = [0, 1]
citations = 8..12 × {fixed source_id, support | counterevidence}
expected_personality_version
```

It contains no delta, new value, score, budget, checkpoint choice, arbitrary patch or free-form
chain-of-thought. The request exposes the allowed trait keys and opaque current aggregate version,
but not current/baseline trait values, positions, inclinations, relationship or affect. Provider
confidence must be at least `0.80`, citations must cover at least 80% of the fixed set, at least
eight independent sources must support the direction, and support must be at least 80% of cited
non-neutral evidence.

One canonical root may become accepted personality evidence for only one trait and direction in V1.
The owner rechecks fixed-run membership, identity, content hash, lineage, all diversity gates,
source eligibility and exact expected aggregate version. Provider replacement can change whether a
candidate is proposed, but cannot change any owner arithmetic or bypass rejection.

Decision confidence is explanatory metadata and does not scale the delta:

```text
cap = 0.80
    + 0.015 * min(4, supporting_roots - 8)
    + 0.010 * min(2, sessions - 6)
    + 0.020 * I(observation_span >= 180 days)

decision_confidence = min(provider_confidence, cap, 0.90)
```

Rejected candidates persist only their terminal outcome and metadata-only audit. They do not add
personality evidence, checkpoint, revision or trait state. There is no retroactive activation of
V1/V2 personality observations.

### Trait metric, delta and cumulative budgets

For two complete canonical trait vectors `a` and `b`:

```text
D∞(a, b) = max_i |a_i - b_i|
D1(a, b) = sum_i |a_i - b_i|
P         = sum over accepted evolution revisions |applied_delta|
```

Endpoint distance and cumulative path are distinct. A reversal may reduce `D∞`/`D1`, but never
refunds path spend.

One accepted evolution changes exactly one trait by exactly `+0.005` or `-0.005`. There is no
partial/clamped application: if the exact step would exceed a value bound or any remaining budget,
the proposal is rejected. V1 enforces all limits together:

| Limit | V1 bound |
|---|---:|
| Trait value | `[0, 1]` |
| Per-trait cooldown | 90 days |
| Global personality cooldown | 30 days |
| Rolling 365-day path, one trait | `0.015` |
| Rolling 365-day path, all traits | `0.060` |
| Lifetime path, one trait | `0.080` |
| Lifetime path, all traits | `0.300` |
| Distance from activation | `D∞ <= 0.080`, `D1 <= 0.300` |
| Distance from last approved checkpoint | `D∞ <= 0.020`, `D1 <= 0.050` |

Cooldown and budget equality is eligible; one microsecond or one exact step beyond a limit is not.
These constants are conservative acceptance fixtures, not a claim of psychological calibration.
Changing them requires a new policy version and the complete longitudinal/stability corpus.

### Checkpoints, approval and restore

Migration `0012_personality_evolution` creates an immutable activation checkpoint from the
already-authoritative current/baseline vector without changing a trait or creating an evolution
event. It is the first approved budget origin.

Every accepted evolution atomically stores:

- deduplicated canonical personality evidence;
- one before/delta/after revision with decision confidence and drift/path metrics;
- an immutable full-vector checkpoint for the resulting aggregate version and canonical hash;
- the terminal reflection outcome; and
- one metadata/provenance audit event.

The repository also ensures an immutable checkpoint exists for the prior aggregate version. An
automatic evolution checkpoint is restorable but does not reset the approved-checkpoint budget.
Only an explicit local approval command appends a separate checkpoint-approval record and chooses a
new reviewed budget origin.

Restore is an explicit typed local proposal containing checkpoint ID, canonical hash and exact
expected current personality version. `PersonalityManager` verifies identity, complete trait key
set, hash, baseline invariants and bounds. Restore appends a restore event, increments the aggregate
version, writes a new resulting checkpoint and audit, and never deletes or rewrites earlier
evidence, revisions or approvals. It bypasses evolution evidence/cooldown because it is recovery,
but it never refunds rolling or lifetime evolution path spend. Values and activation baselines are
unchanged.

Export/compare exposes current and baseline vectors, per-trait checkpoint diff, `D∞`, `D1`, path
and remaining budgets, policy/aggregate/checkpoint versions, evidence IDs and revision/restore
lineage. It contains no source quotes, assistant/provider text, prompts, secrets or raw CoT.

### Bounded observable expression

Personality Expression Projection V2 keeps the existing five baseline voice instructions exactly
as soft guidance. It adds a pure, non-persistent qualitative evolution projection derived from the
current authoritative vector relative to activation baseline:

- the five existing trait composites plus a separate grounded-optimism composite;
- a cue only when the absolute composite change reaches `0.005`;
- `slightly_stronger` or `slightly_softer`, never numeric values;
- stable top two cues and closed versioned wording;
- no evidence, checkpoint, budget or mutation history in provider context.

Context/manifest schema v16 records the exact personality aggregate version and selected cue codes
for behavioral comparison. It creates no second personality source and no response template or
catchphrase. Affect continues to read the current immutable personality snapshot as reactivity
input, but affect is never returned as Stage 14 evidence.

### Transaction, migration and downgrade

The separate Personality Unit of Work is the only infrastructure boundary that can update current
trait rows after activation. Optimistic aggregate versioning prevents lost updates. Target
mutation/evidence/revision/checkpoints/reflection outcome/audit commit together; a rejected outcome
and audit commit without personality state.

Migration changes no current trait value or activation baseline and performs no provider call.
Existing interaction manifest rows receive explicit nullable pre-Stage-14 semantics. Downgrade is
allowed only while there is no V3 personality run, evidence, revision, approval or restore event;
otherwise it stops and requires explicit export/recovery rather than leaving evolved traits without
their provenance.

## Consequences

- Personality may evolve only after a multi-month, multi-session, multi-lineage independent set.
- Day-500 differences are small, measurable, behaviorally projectable and fully reversible without
  changing identity or values.
- Active general reflection cannot starve the separate personality evidence window.
- User pressure, relationship state and Stage 13 affect/inclinations have no personality shortcut.
- The policy intentionally prefers sparse or absent evolution to fabricated autonomy.
- Provider semantic classification remains fallible; deterministic policy limits damage but does
  not prove that every accepted semantic direction is psychologically correct.

## Alternatives rejected

- Enabling the V1/V2 free-text personality observation as a write command.
- Using relationship warmth, inclination state/evidence or affect transitions as trait evidence.
- Treating current trait values or generated responses as evidence of their own future change.
- A direct provider delta/new-value/JSON patch.
- One-session change, per-turn personality calls or a background inner-monologue loop.
- Endpoint-only drift budgets that reversals can reset.
- Automatic checkpoint approval after every accepted change.
- Destructive rollback that deletes history or decrements aggregate versions.
- Numeric trait dumps, post-generation rewriting or a second persistent personality source to make
  evolution visible.
