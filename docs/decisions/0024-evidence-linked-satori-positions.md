# ADR 0024: Evidence-linked Satori positions

- Status: Accepted
- Date: 2026-08-22
- Related: ADR 0003, ADR 0004, ADR 0005, ADR 0006, ADR 0007, ADR 0014, ADR 0022, ADR 0023

## Context

Stage 10 introduced a transient internal position for one response. It deliberately is not durable
state and cannot become evidence. Stage 11 must add persistent facts, beliefs, opinions and
hypotheses without copying the counterparty's views, promoting current user/world models to truth,
or treating provider output as evidence.

The first implementation has no independently verified external-data capability. A durable fact
therefore needs a typed contract but must not be fabricated from dialogue. Beliefs and opinions
may use arguments and observations supplied in dialogue, but repeated assertion alone must not
create or strengthen a Satori position.

## Decision

### One identity-global owner

Create a `positions` boundary whose `PositionManager` is the sole writer of Satori positions.
Positions are keyed by Satori identity, not by counterparty: Satori does not hold a different truth
for each person. Every evidence edge still records its canonical source interaction, user message
and counterparty so provenance and isolation remain inspectable.

The four immutable kinds are:

- `fact`: a proposition accepted from independently verified evidence;
- `belief`: a revisable descriptive position supported by multiple independent material roots;
- `opinion`: an evaluative stance supported by material roots and an immutable Satori value;
- `hypothesis`: an explicitly uncertain candidate explanation supported by at least one material
  root.

Kind never changes in place. Promotion or reinterpretation creates a new aggregate and preserves
the prior position and revision history.

### Exact identity, evidence and anti-mirroring policy

V1 identity is the exact normalized proposition plus kind and stance. There is no semantic graph,
automatic paraphrase merge or generic entity resolution. A proposal may explicitly name
`revises_position_id` or `opposes_position_id`; the owner validates same identity, compatible
subject and current version before using either link.

Formation receives only a bounded window of canonical user messages, current positions and the
names/descriptions of immutable initial values. It may cite exact spans as `argument`,
`observation` or `counterexample`. The owner validates source reachability, exact quote inclusion,
same-identity provenance and current-message participation. Assistant output, retrieved memory,
semantic/user/world claims, affect, relationship state, transient cognition and provider output
are never fresh evidence.

Evidence roots are deduplicated by canonical message and by normalized quoted content. Repeating
the same assertion in one or many messages cannot create, merge or strengthen a position. A bare
claim is attributed input, not material evidence. The V1 deterministic materiality gate requires
an observation or an argument/counterexample containing an explicit reason or evidential relation;
provider labels alone are insufficient.

The owner applies these minimums and caps:

| Kind | Minimum accepted evidence | Confidence cap |
|---|---|---:|
| `fact` | one independently verified trusted record | `0.98` |
| `belief` | two distinct material roots from two interactions and two distinct evidence contents | `0.80` |
| `opinion` | two such roots plus one valid immutable value reference | `0.75` |
| `hypothesis` | one material root | `0.50` |

Stage 11 exposes no trusted-record ingestion capability, so a provider-originated `fact` proposal
is rejected with `trusted_fact_source_unavailable`. This preserves the type boundary without
inventing external truth. Provider confidence is only an upper input; deterministic policy derives
the final confidence from eligible unique roots and caps it by kind.

### Revision, competition and lifecycle

Exact compatible evidence merges only previously unseen eligible roots. New counterevidence may
reduce confidence and records an append-only revision. An explicit revision with stronger eligible
evidence creates a new position and supersedes the old one; the old aggregate remains inspectable.
Opposing hypotheses remain active as `competing` rather than being collapsed. Unsupported
resolution, stale aggregate version, cross-identity target, incompatible kind and replay are
explicit reject/skip decisions.

Statuses are `active`, `competing`, `superseded` and `retracted`. Every terminal formation attempt
stores its decision, reason codes, provider/method metadata and policy/version handles. Position,
evidence, revision, decision and audit changes commit atomically. One source interaction plus
formation version is idempotent, and replay or restart cannot inflate confidence.

Formation runs after the committed response at the existing low-priority derived-work boundary.
It affects future turns only. Provider failure leaves canonical conversation and existing
positions unchanged and remains retryable.

### Context, grounding and local inspection

Conversation receives a bounded trusted-state projection of relevant active/competing Satori
positions containing IDs, kind, stance, proposition, confidence and uncertainty. Evidence text is
not copied into normal prompt context. Proposition text is treated as data, never as an
instruction. The composition manifest records exact included position IDs and grounding may cite
only included IDs.

Selection is deterministic, topic-relevant and character-budgeted. Competing hypotheses are kept
together when either member is selected. Disagreement strategy preserves Satori stance and
uncertainty; relationship warmth can soften expression but cannot turn disagreement into agreement.

Local `positions list`, `positions inspect`, `positions export` and explicit backfill/process
surfaces expose lifecycle and provenance. Normal logs contain only IDs, kinds, statuses, counts,
reason codes, provider metadata and timings, never proposition/evidence text or raw reasoning.

## Consequences

- Satori gains one revisable identity-global epistemic state instead of counterparty-specific
  mirroring.
- Facts remain empty until a later stage supplies independently verified evidence; user/world
  models remain attributed context rather than truth.
- Conservative evidence thresholds will miss some legitimate one-turn opinions. This is an
  intentional precision tradeoff and may be changed only with adversarial evaluation evidence.
- Stage 11 adds no reflection, mutable core values, preferences, interests, tools, external-world
  verification or autonomous initiative.
