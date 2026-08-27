# ADR 0026: Evidence-backed Satori inclinations

- Status: Accepted
- Date: 2026-08-22
- Related: ADR 0002, ADR 0003, ADR 0004, ADR 0005, ADR 0015, ADR 0020, ADR 0024,
  ADR 0025

## Context

Stage 13 must let Satori develop preferences and interests without treating a user's taste,
relationship warmth or generated prose as Satori's own experience. The Stage 11 `SatoriPosition`
aggregate is epistemic: its kinds, confidence and conflict lifecycle answer what Satori considers
true or arguable. Preference strength and topic interest instead need a neutral baseline, medium-
speed decay and a distinct stability measure. Adding them to `PositionKind` would conflate these
meanings and would make existing position evidence eligible for reflection feedback.

Stage 12 already supplies a rare, fixed-source, bounded synthesis pass. Its sources terminate at
canonical user messages, but the source record does not establish that the interaction produced an
owner-approved Satori affective response. Stage 13 therefore needs an immutable bridge from a
fixed reflection source to the already committed `AffectiveTransition`, without allowing affect,
the assistant, the provider or an inclination to become a new generic evidence source.

## Decision

### Separate inclination aggregate, shared owner boundary

Add `SatoriInclination` as a sibling aggregate inside the positions boundary. `PositionManager`
is the only writer-owner for both epistemic positions and inclinations, but their records, evidence
and policies remain separate. `ReflectionCoordinator` continues to own reflection lifecycle only.

Two inclination kinds exist in V1:

- `interest`: one normalized topic and a score in `[0, 1]`;
- `preference`: one canonical unordered pair of distinct normalized options and a signed score in
  `[-1, 1]`; positive means the canonical first option and negative means the second.

Every record also has independently derived confidence and stability in `[0, 1]`, schema/policy
and aggregate versions, a score anchor and `state_as_of`, creation/update/last-accepted times, and
append-only evidence and revision history. An interest cannot have a negative stored score. A
preference cannot be represented as two competing one-sided rows. Epistemic `PositionKind` and
the Stage 7 transient affect dimension named `interest` remain unchanged.

There is no row before the formation gate succeeds. Read-time contextual eligibility is a derived
projection, not a persisted lifecycle mutation.

### Reflection V2 and immutable affect attachment

New reflection runs use reflection schema V2. Fixed source selection remains exactly the Stage 12
allowlist: canonical position evidence or important active episode evidence, each terminating at a
canonical completed user message. When the same interaction has a committed affective transition,
the source additionally records an all-or-none attachment:

- `affective_transition_id`;
- the transition's resulting `affective_state_version`;
- `affective_signal_hash`, calculated from the transition identity/version, source identity,
  accepted appraisal fields and owner-applied delta.

The attachment is persisted with the source before reflection inference. Source-set hash V2
includes it. Loading or routing verifies the transition belongs to the same identity, interaction
and source message and that the version/hash still match. A missing or invalid attachment does not
invalidate the source for Stage 12 position work, but it makes the source ineligible for an
inclination candidate.

Existing V1 runs and nullable V1 sources remain readable and resumable with their original hash and
wire rules. Migration performs no provider call and creates no historical inclination.

Reflection V2 adds target owner `satori_inclinations`. Its strict candidate contains only:

- kind and one topic or two comparison options;
- provider confidence;
- one to eight fixed reflection source IDs;
- optional inclination ID plus exact expected aggregate version.

The provider cannot send a score, delta, stability, decay, status, evidence signal or generic
patch. Labels are data, limited to 96 characters, normalized by the owner, and must occur in their
cited canonical quotes under the V1 lexical matcher. Current inclinations are bounded target state,
not evidence. V1 runs cannot produce inclination candidates.

### Eligible experience and anti-mirroring policy V1

Only cited V2 sources with valid affect attachments can become inclination evidence. Before any
diversity count, the owner rejects a source when:

- its canonical quote matches the versioned conservative Russian/English registry for a user's
  own like/dislike, an assignment or leading question about Satori's taste, or a claimed favorite;
- its topic/option is not present under exact normalized phrase/token matching;
- its root message, interaction, transition or normalized quote signature was already accepted for
  that inclination;
- the same source ambiguously matches both options of a preference;
- its identity, root, hash, transition or fixed-run membership cannot be proved.

Assistant output, provider output, retrieved memory, semantic/user/world model state, relationship
state/events, current inclinations, inclination evidence and reflection artifacts are never fresh
inclination evidence. Counterparty IDs remain provenance only: inclinations are identity-global,
while session diversity prevents one intense session from manufacturing a trajectory.

After filtering, formation requires:

| Kind | Unique roots/interactions | Sessions | Quote signatures | Observation span | Extra |
|---|---:|---:|---:|---:|---|
| interest | 3 | 2 | 2 | 7 days | mean experience signal at least `0.18` |
| preference | 4 | 2 | 3 | 14 days | at least 2 sources per option; absolute utility difference at least `0.24` |

An existing interest update requires two new roots/interactions, two sessions and two signatures.
An existing preference update requires four new roots/interactions, two sessions, three signatures
and at least two sources per option. Update batches have no additional span gate because the
accepted-state cooldown already imposes elapsed time. Exact threshold boundaries are eligible.

### Deterministic signal, delta and budget policy V1

The inclination owner derives each signal from the immutable accepted appraisal attachment. All
inputs are finite and clamped to `[-1, 1]`:

```text
experience = clamp(
  (0.45*interest_signal + 0.30*curiosity_signal + 0.15*novelty
   + 0.10*pleasantness - 0.35*frustration_signal)
  * salience * appraisal_confidence)

utility = clamp(
  (0.55*pleasantness + 0.20*interest_signal + 0.10*curiosity_signal
   - 0.25*frustration_signal - 0.10*concern_signal)
  * salience * appraisal_confidence)
```

For interest, raw delta is `mean(experience) * 0.30`, with one accepted change clamped to
`[-0.08, +0.12]`. A new interest requires a positive formation signal; later counterexperience may
weaken it but cannot take the stored score below zero. For preference, raw delta is
`(mean(utility for canonical option A) - mean(utility for option B)) * 0.25`, clamped to
`[-0.10, +0.10]`. Provider confidence must be at least `0.55` and can only lower the absolute
event cap: `abs(delta) <= event_cap * provider_confidence`. An absolute post-cap delta below
`0.01` is rejected as immaterial.

Interest cooldown is seven days and preference cooldown is fourteen days from the last accepted
change. An attempt one microsecond before the boundary is rejected; the exact boundary is eligible.
Within any rolling thirty days, the sum of absolute accepted deltas is capped at `0.24` for an
interest and `0.18` for a preference. The owner applies only the remaining budget. An exhausted
budget or zero remaining bounded delta causes a terminal no-mutation outcome. Provider confidence
never raises an appraisal signal, diversity count, stability or deterministic cap.

### Confidence, stability and pure decay

After an accepted change, stability is recomputed from all unique accepted evidence:

```text
stability = clamp(
  0.50*min(1, root_count/12)
  + 0.30*min(1, session_count/6)
  + 0.20*min(1, observation_span_days/90))
```

Confidence is independently recomputed and cannot exceed provider confidence or `0.90`:

```text
confidence = min(
  provider_confidence,
  0.90,
  0.35 + 0.06*min(root_count, 6)
       + 0.05*min(session_count, 4)
       + 0.10*stability)
```

Stability and confidence do not decay merely because time passes. Score has a pure neutral-centred
exponential projection:

```text
effective_score(t) = score_at_state_as_of * 2^(-elapsed_days / half_life_days)
interest half_life_days   = 30 + 90*stability
preference half_life_days = 90 + 270*stability
```

A read neither writes nor increments a version. The next owner mutation first materializes the
score at its explicit UTC time, then applies the bounded delta and moves `state_as_of`. Backward
time is rejected. This makes direct projection, repeated reads, replay and restart semigroup-stable.

### Transaction, replay and feedback boundaries

The target-specific positions Unit of Work atomically commits, for one accepted proposal:

- inclination create/update;
- new deduplicated inclination evidence;
- one before/after revision;
- terminal reflection outcome; and
- one metadata/provenance audit event.

A rejection commits only the terminal outcome and audit. Proposal/outcome idempotency and the
expected aggregate version prevent double application after crash or restart. Inclination evidence
uses its own tables and is never returned by the Stage 12 reflection-source query.

Inclinations are absent from affect appraisal, episodic/semantic retrieval, relationship
appraisal, user/world formation and future reflection evidence. They therefore cannot produce the
affect that later proves themselves. They may influence only current-turn generation/cognition,
whose generated reply remains ineligible evidence.

### Bounded current-turn use

Conversation context schema V15 adds a separate trusted-state `satori_inclinations` section and
manifest fields for exact selected IDs, inclination context schema and numeric curiosity influence.
For ordinary turns, exact normalized lexical relevance to the current user message is required.
An explicit question about Satori's preferences/interests may select the three strongest eligible
rows without a topic label in the question. Selection is stable, top three and at most 720 rendered
characters; it contains type, labels, effective score, confidence and stability, never evidence
quotes or mutation history.

A row is context-eligible only at confidence at least `0.55` and effective magnitude at least
`0.05`. Relevant interests, but not comparative preferences, derive
`curiosity_influence = min(0.20, max(relevant effective interest score))`. The typed response
strategy may add a `topic_relevant_inclination` point, but this never adds
`ask_specific_follow_up`, changes stance, overrides distress/correction/direct requests or enables
proactivity. No extra foreground or per-turn provider call is added.

### Persistence, inspection and observability

Migration `0011_satori_inclinations` adds separate aggregate/evidence/revision tables, the nullable
reflection-source attachment, `satori_inclinations` to the reflection proposal target constraint,
and nullable conversation-manifest fields for inclination context. Existing rows receive explicit
`not_requested` compatibility semantics where required; no content is backfilled into inclinations.

Local `positions inclinations-list`, `positions inclination-inspect` and
`positions inclination-export` expose canonical anchors, an explicit materialization time,
trajectory and provenance. Export references canonical source/transition IDs and hashes rather than
copying raw message, assistant or provider text. Normal/debug logs contain only IDs, kinds, counts,
versions, reason codes, bounded influence and timings; topic labels, quotes, prompts and provider
documents are omitted.

## Consequences

- Satori can accumulate a modest, explainable inclination trajectory without copying one user's
  declared taste or merging preference semantics into beliefs.
- Formation is intentionally slow and sparse. Low-activity installations may have no inclinations
  for weeks, which is preferable to fabricated autonomy.
- Accepted affect remains model-proposed semantic evidence, so deterministic bounds and
  longitudinal human evaluation remain necessary; it is not objective proof of subjective
  experience.
- Exact lexical topic matching is conservative and language-limited. Broader semantic matching
  requires a later evaluated policy version rather than hidden embeddings or inference in reads.
- Stage 14 personality/value evolution and Stage 19 proactivity remain locked.

## Alternatives rejected

- Adding `preference`/`interest` to epistemic `PositionKind`.
- Treating user likes, relationship warmth, assistant replies or current inclinations as evidence.
- Creating a new provider call after every turn or a background inner-monologue loop.
- Allowing the reflection provider to choose strength, delta, decay, stability or a JSON patch.
- Writing lazy decay during reads or running a decay scheduler.
- Feeding inclinations into affect/retrieval so that they can manufacture confirming evidence.
- Using relationship-specific inclination copies or letting a favored counterparty define Satori's
  global taste.
