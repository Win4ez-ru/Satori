# Reflection

Stage 12 implements rare evidence-bounded synthesis under
[ADR-0025](decisions/0025-bounded-reflection-runs-and-owner-routing.md). Stage 13 extends its fixed
source record and target wire under
[ADR-0026](decisions/0026-evidence-backed-satori-inclinations.md). Reflection records what was
considered and proposed; existing domain owners remain the only authorities that may change state.
Stage 14 adds a separate personality-purpose V3 lifecycle under
[ADR-0027](decisions/0027-bounded-personality-evolution-and-checkpoint-restore.md) without changing
general V1/V2 run identity or source consumption.

## V1 boundary

```text
deterministic trigger
→ select and persist immutable canonical evidence handles
→ one bounded structured provider attempt outside transaction
→ persist typed proposals
→ stable per-proposal target-owner decisions
→ atomic target mutation + outcome + audit
→ resumable run completion
```

The coordinator owns only `ReflectionRun`, `ReflectionSource`, `ReflectionAttempt`,
`ReflectionProposal` and `ReflectionOutcome` lifecycle. It does not own positions, personality,
values, memory, relationship, affect or user/world models.

Enabled V1 target:

- `satori_positions`: routed to `PositionManager` under stricter reflection-origin evidence
  thresholds.

Inspectable but disabled V1 targets:

- `personality` and `values`: deterministic `target_owner_not_enabled_stage_12`; no delta schema,
  manager call or target write.

Unknown target owners invalidate the provider attempt. In V1, facts, preferences/interests, tools
and arbitrary state patches are absent from the wire.

V2 keeps personality/value owner observations inspectable but disabled. They are never upgraded or
reinterpreted as Stage 14 proposals.

## Trigger and cost contract

Automatic checks run only after the existing committed-reply post-response work. They do no model
work unless the exact policy gate passes.

| Bound | Automatic | Explicit local |
|---|---:|---:|
| New eligible roots | 8 | 4 |
| Distinct interactions | 6 | 3 |
| Observation span | 7 days | none |
| Completed-run cooldown | 7 days | waived |
| New runs / rolling 24 h | 1 | 1 |

Both paths require no non-terminal run. Hard per-run caps are 12 sources, 4,800 source characters,
12 position target refs, 3 proposals, 768 provider output tokens, one call per attempt and two
attempts. There is no force flag and no automatic retry loop.

## Fixed evidence set

V1 accepts only an exact immutable quote whose lineage ends at a canonical user message through:

- `PositionEvidence`; or
- active episodic memory evidence whose episode importance is at least `0.65`.

The selected source row stores handles and hashes, not another raw-message copy. Position evidence
wins deterministic same-root deduplication, followed by stable ID order. The provider sees bounded
quotes as explicitly untrusted data and cites only reflection source IDs.

Not evidence:

- current position propositions or confidence;
- episode summaries, semantic/user/world claims or relationship state;
- assistant output, provider output or transient cognition;
- prior reflection runs, proposals, outcomes or summaries.

Current positions and immutable value descriptions may be supplied separately as target state.
They cannot satisfy an evidence citation.

## Reflection V2 affect attachment

New Stage 13 runs use reflection schema V2 without broadening the Stage 12 source allowlist. When
a selected source's interaction has an already committed affective transition, its immutable
source row may additionally store one all-or-none attachment:

- `affective_transition_id`;
- the transition's resulting `affective_state_version`;
- `affective_signal_hash`, derived from transition identity/version, source identity, accepted
  appraisal fields and owner-applied delta.

The attachment is persisted with the fixed sources before provider inference, and V2
`source_set_hash` includes it. Loading and owner routing verify the same identity, interaction,
canonical user message, transition version and hash. A missing or invalid attachment does not make
the source unusable for Stage 12 position work, but it is ineligible for an inclination candidate.

Existing V1 runs and nullable V1 source rows remain readable and resumable with their original
hash and wire rules. V1 runs cannot contain inclination candidates, and migration performs no
provider call or historical inclination backfill.

## Identity and lifecycle

```text
source_set_hash V1 = hash(ordered source kind/id/version + root ids + content hashes)
source_set_hash V2 = hash(V1 fields + ordered affect attachment identity/version/hash or absence)
run_key = reflection policy/schema + identity + source_set_hash
proposal_id = hash(run_id + ordinal + canonical typed payload)
outcome_key = proposal_id + target policy version
```

Run states:

- `pending_generation`;
- `proposals_ready`;
- `applying`;
- `completed`;
- `retryable_failure`;
- `exhausted`.

Sources are committed before provider inference and cannot be appended or replaced. Successful
provider output atomically records one attempt and every proposal. Failed output records only the
attempt. After proposals exist, retry never calls the provider again. Per-proposal outcomes are
terminal and idempotent; run finalization can be replayed after a crash.

## Target decisions

The coordinator routes proposals in ordinal order through closed target adapters. A target adapter
asks its domain owner to evaluate. Owner mutation, outcome, revision/history and audit share one
transaction. There is deliberately no all-target transaction.

Reflection position proposals preserve every Stage 11 check and add minimum evidence:

- new/revised belief or opinion: three roots/interactions/signatures;
- hypothesis: two roots/interactions/signatures;
- merge/challenge: two eligible roots with at least one genuinely new root.

Reflection cannot propose a fact. Provider confidence remains an upper input and never overrides
the existing deterministic kind cap.

### Stage 13 inclination routing

Reflection V2 adds the closed target owner `satori_inclinations`. Its strict candidate contains
only kind plus one topic or two comparison options, provider confidence, one to eight IDs from the
persisted fixed source set, and optional inclination ID with its exact expected aggregate version.
The provider cannot send score, delta, stability, decay, status, evidence signal or a generic
patch. Labels are at most 96 characters, owner-normalized and must occur in their cited canonical
quotes under the V1 lexical matcher.

Current inclinations may be supplied as bounded target state but are never evidence. The
coordinator routes each candidate to `PositionManager`, which alone validates anti-mirroring,
source/attachment identity, label relevance, diversity, deterministic experience/utility,
cooldown, rolling budget, confidence, stability and pure-decay materialization. An accepted target
transaction atomically stores aggregate/evidence/revision, terminal reflection outcome and audit;
a rejection atomically stores only outcome and audit.

### Stage 14 personality-purpose V3

V3 is a separate `personality_evolution` purpose and consumed-root namespace. It reads the same
canonical position/important-episode leaves as general reflection, but excludes affect attachments,
Stage 13 inclination-evidence roots, direct trait assignments/user evaluations and explicit
relationship material. Its deterministic selector requires eight independent roots/clusters, six
sessions/weeks, four months/lineages and at least ninety days before inference. General completed
runs neither consume nor satisfy this purpose.

The personality request exposes only the fifteen allowed trait keys and current aggregate version,
not current values, affect, relationship, inclinations, positions or values. Its strict document
contains zero or one candidate with exact trait key, increase/decrease direction, confidence,
support/counterevidence citations and expected aggregate version. Delta/new value/budget/checkpoint
or generic patch fields are invalid.

`PersonalityManager` revalidates the fixed set, source eligibility/diversity, confidence/support,
cooldowns and every drift/path/checkpoint budget. It alone applies exact `±0.005`. Accepted
evidence/revision/checkpoint/outcome/audit is one personality transaction; rejected outcomes add no
personality evidence or state. Values remain disabled in every schema.

## Failure and cycle rules

- duplicate trigger/source set returns the same run;
- provider outage/invalid schema changes no target state;
- target failure leaves that proposal pending and earlier outcomes intact;
- retry observes prior outcomes and never double-applies;
- stale target version is a terminal owner rejection;
- forbidden/non-user/cyclic/hash-mismatched source lineage is rejected before inference;
- zero proposals is a successful completed run and consumes its inputs.

Generated output is never written into an evidence catalog. A reflection-created position retains
only the canonical roots selected for that run; its proposition cannot corroborate itself later.
Inclination evidence uses separate owner tables and is never returned by the Stage 12
reflection-source query used by later runs. Inclinations are also excluded from affect appraisal,
episodic/semantic retrieval,
relationship appraisal and user/world formation, preventing an inclination from manufacturing its
own future attachment or evidence. Generated replies remain ineligible evidence.

Personality V3 never uses an affect attachment or inclination evidence and never reads its current
trait values as proposal evidence. Its separate purpose may resolve a canonical root previously
seen by general reflection exactly once, but cannot resolve a completed personality-purpose root
again. Accepted personality roots are globally single-use across traits in policy V1. Restore and
expression cues are not reflection sources.

## Acceptance

Automated V1 acceptance covers deterministic trigger boundaries, exact cost caps, source ordering
and hashing, immutable retry/restart sets, invalid/timeout provider attempts, zero proposals,
replay, concurrency, partial multi-proposal crash recovery, target atomic rollback, stale versions,
forbidden/cyclic sources, stricter position thresholds and unauthorized personality/value targets.

V2 acceptance adds these exact families:

- all-or-none attachment schema, V2 source-set hash, same identity/interaction/message/transition
  verification, tamper/missing attachment behavior and V1 readability/resumption;
- strict inclination candidate wire, fixed-run membership, expected aggregate version and rejection
  of provider-owned score/delta/stability/decay/status/evidence fields;
- user-taste/assignment/leading-question/claimed-favorite anti-mirroring, exact label support,
  ambiguous preference matching, root/interaction/transition/signature dedup and every forbidden
  derived source;
- formation/update diversity, deterministic signal/cap/cooldown/rolling-budget/decay boundaries,
  proposal replay and accepted/rejected target transaction atomicity;
- future-source-query exclusion, affect/retrieval/relationship/user-world feedback isolation and
  bounded behavior relevance without an extra foreground provider call.

A versioned long-period corpus must include quiet/active periods, repeated evidence, contradictory
roots, failed runs and new evidence after completion. Manual acceptance inspects one run containing
an owner-accepted belief-related proposal and a personality candidate rejected without personality
state change. Full Foundation and existing Stage 11 independence regressions remain mandatory.
For Stage 13, manual acceptance compares a user-only taste assertion with multi-session verified
Satori-relevant experience and inspects the inclination trajectory plus immutable source/transition
provenance.

Stage 14 acceptance additionally covers purpose-separated consumption, 90-day structural
diversity, strict V3 wire, direct assignment/relationship/affect/inclination exclusion, exact
trait delta and all drift/path/cooldown budgets, V1/V2 resumption, atomic outcome/checkpoint/replay,
provider replacement and context-v16 anchor comparison. Value mutation and Stage 15 remain locked.
