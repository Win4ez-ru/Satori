# Satori preferences and interests

Stage 13 implements identity-global, medium-speed inclinations under
[ADR-0026](decisions/0026-evidence-backed-satori-inclinations.md). An inclination is persistent
Satori state, not a prompt persona, user/world fact, relationship dimension, transient emotion or
epistemic position.

## Boundary and ownership

`PositionManager` is the only writer-owner. It owns a separate `SatoriInclination` aggregate with
`interest` and comparative `preference` kinds. `ReflectionCoordinator` may route a strict candidate
but cannot calculate or persist an inclination. Conversation, cognition and CLI receive immutable
read projections only.

An interest has one normalized topic and non-negative score. A preference has a canonical unordered
option pair and one signed score. Confidence describes evidence coverage; stability describes
longitudinal diversity; score describes current strength/direction. These values are never aliases.

## Evidence contract

Inclination policy V1 evidence must be a source in a persisted Reflection V2 fixed set with a
verified immutable attachment to the owner-approved affective transition for the same identity,
interaction and user message. The owner derives experience/utility from the attached accepted
appraisal and never from a provider-supplied delta.

The following are structurally ineligible:

- a user's declared like/dislike or instruction about what Satori likes, including imperative and
  obligation forms in the versioned Russian/English registry;
- assistant/provider output and generated summaries;
- retrieved memory and semantic/user/world claims;
- relationship state/events;
- existing inclinations, their evidence or any reflection artifact.

Root message, interaction, transition and normalized quote signature are deduplicated. Preference
sources must match exactly one option. Interest formation needs three roots across two sessions and
seven days; preference formation needs four roots, two per option, across two sessions and fourteen
days. Exact formulas, update gates and caps live only in ADR-0026 and versioned code policy.

## Lifecycle

```text
canonical eligible roots
→ Reflection V2 fixed source set + affect attachment
→ strict inclination candidate
→ PositionManager validates mirroring, relevance and diversity
→ deterministic signal, confidence, stability, cooldown and budgets
→ commit/reject + evidence + revision + reflection outcome + audit
```

Formation and updates are reflection-paced; no per-turn formation call exists. Accepted state uses
a score anchor at `state_as_of`. Pure exponential decay projects it toward neutral. Reads never
write; the next accepted mutation materializes decay first. Replay, intermediate reads and restart
at the same explicit time therefore produce the same result.

## Conversation use

Only context-eligible, current-topic-relevant inclinations enter the bounded trusted-state section.
An explicit question about Satori's own interests/preferences may request the top three. Evidence
quotes and trajectory are never rendered to the provider.

Relevant interests derive a typed curiosity influence bounded to `0.20`. This can encourage a more
engaged treatment of the current topic, but cannot force a question, change the current stance,
override the user's need or enable autonomous initiation. Preferences may inform an honest
comparison but do not increase curiosity by themselves.

Inclinations are deliberately absent from affect appraisal, retrieval, relationship appraisal and
future evidence formation. The generated response is not evidence.

## Inspection and acceptance

Local positions commands list, inspect and export canonical inclination state with an explicit
materialization instant and provenance IDs. Ordinary logs omit labels and content.

Acceptance requires mirroring attacks, source/transition/signature double-count protection,
formation/update diversity, exact cooldown boundaries, per-event and rolling caps, pure-decay
semigroup/restart tests, transaction recovery, export integrity, behavior relevance and a
longitudinal corpus in which opposite user tastes produce the same Satori trajectory when the
owner-approved experience trajectory is identical.

Personality/value mutation, semantic topic expansion, proactive initiation and Stage 14 are out of
scope.
