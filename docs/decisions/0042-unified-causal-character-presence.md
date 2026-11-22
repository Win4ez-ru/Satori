# ADR 0042: Unified causal character presence from live owned state

- Status: Accepted
- Date: 2026-08-29
- Supersedes: ADR 0041 (current character-delivery selection and provider projection only)
- Related: ADR 0002, ADR 0003, ADR 0015, ADR 0020, ADR 0023, ADR 0027, ADR 0031,
  ADR 0040, ADR 0041

## Context

The separately authorized v25 OpenAI exact-manual gate proved that social/current-affect,
reciprocal-warmth and broad self-disclosure routing reached the foreground provider and that the
historical missing-reply path was closed. It did not prove recognizability. Across the nine sampled
replies, the prose repeatedly announced a calm/level state, explained the absence of established
hobbies and added polished abstract observations. The output was coherent and grounded, but too
often sounded like a carefully instructed assistant rather than one continuous Satori.

The follow-up architecture audit found that this was primarily a state-to-expression defect, not
evidence that another biography stage or another provider was required:

- canonical runtime personality already carried live guidance strengths, current trait values,
  current values and bounded evolution cues, but v25 copied only five constant personality codes
  into `CharacterDeliveryDecision`; the renderer expanded those codes into the same static
  paragraph and discarded the live strengths and all current values;
- multidimensional affect and relationship owner reads were reduced to five and four coarse
  profiles respectively before realization, encouraging repeated profile-shaped wording such as
  `спокойная/ровная` instead of changing the conversational movement;
- canonical-character prose, affect prose, relationship prose, cognition-substance instructions
  and the late delivery director were stacked as separate prescriptive blocks. Their overlap made
  the model solve a checklist, restate the input and explain epistemic limitations instead of
  choosing one natural response;
- memory, positions and inclinations could reach the provider as trusted data, but their
  availability was not part of one causal delivery projection. The model had to discover both the
  substance and the voice from competing instructions.

Adding Stage 15 autobiographical state would not repair any of these losses. It would add another
source of facts before the existing bridge discarded or flattened the relevant live state. Opening
that stage would expand persistent ownership while concealing a Checkpoint 14.2 expression defect.

## Decision

### Policy v26 and one causal path

Behavior policy v26 becomes the current offline production-composition candidate. V25 remains
immutable historical sampled evidence; its failure-observability migration and explicit
relationship-recovery boundary remain accepted. Policies v10 and v19-v25, their renderers,
fixtures, validators and paid evidence remain reproducible and are not relabelled as v26. The
historical v24/v25 paid execution entrypoints are retired: they fail closed before settings,
runtime construction or network I/O, while offline inspection and retained evidence remain
available.

The current path is:

```text
canonical personality/value owners
  + current affect owner read
  + counterparty relationship owner read
  + cognition strategy and disclosure request
  + exact memory-use license and bounded position/inclination availability
→ CharacterDeliveryDecision schema 3
→ CharacterPresenceProjection schema 1
→ one late current-turn presence rendering
→ foreground provider
```

`CharacterDeliveryDecision` schema 3 preserves the cognition-owned stance, uncertainty, V2 intent
registry, ordered tags, required points, forbidden claims and verbosity. Its goals, grounding,
continuation and pressure remain request-local. Ordinary depletion no longer receives a default
motivational push; non-zero pressure requires an explicit motivation or safety basis.

`CharacterPresenceProjection` is a frozen request-local value with no repository, mutation API or
write-back route. It deterministically selects at most three contextually relevant personality
signals from the existing live guidance and evolution cue, at most three existing value signals,
at most three qualitative affect signals and at most three qualitative relationship signals.
Selected personality/value signals retain their source strength plus a typed qualitative level;
affect/relationship signals carry only a qualitative level. A bounded personality-evolution
direction may be carried without exposing the complete vectors. The projection also records an
exact `memory_use_licensed` boolean and whether a canonical Satori position or an owner-approved
topical inclination is available. Memory use is licensed only when retrieval actually returned
memory and the final delivery grounding is `trusted_context`; a memory row existing in storage,
or even a retrieved result under another grounding scope, is not sufficient.

The presence projection does not infer a new trait, value, emotion, relationship, memory, opinion
or preference. Personality, affect, relationship, memory, positions and inclinations keep their
existing sole owners and speeds of change. Provider output is never accepted as presence evidence
and cannot update any owner.

### One lean provider realization

V26 replaces the separate canonical-character core, affect block, relationship block and v25 late
director with one final `Trusted current-turn presence Сатори` block. Deterministic factual and
safety boundaries stay separate where their authority requires it, but current character delivery
is stated once. The active cognition template renders a concise response purpose rather than the
historical multi-clause response-substance checklist.

The renderer combines only the signals that are causally available for this turn:

- the stable center comes from selected live personality/value strengths, not constant code names;
- affect changes rhythm, energy, concern, tension or edge and is not a mandatory self-description;
- relationship changes ease, openness, personal warmth or reserve without changing truth scope;
- licensed retrieved memory, position and inclination availability can make the move specific,
  personal or independent without manufacturing their content;
- a requested multi-facet self-disclosure is answered as one coherent personal movement;
- missing topical inclination remains silent unless the user specifically asks whether a stable
  hobby exists. General curiosity may follow from personality and values, but it is not relabelled
  as a durable topical preference;
- Satori may make an original reaction, opinion or taste statement as her own state without
  presenting it as a fact about the user or world.

The projection supplies outcomes and boundaries, never a desired reply, phrase bank, generated
example, copied fictional character or output rewrite. The provider still returns the canonical
reply. The existing ten-reason max-one self-consistency retry is unchanged, and no judge model,
automatic fallback or additional call is introduced.

### Observability and replay

Fresh v26 generation manifests require delivery decision schema 3 and presence projection schema
1. They expose bounded content-free codes for the selected personality, value, affect and
relationship signals together with qualitative level and optional evolution direction, but omit
even the selected request-local numeric strengths. They also expose the exact
`character_presence_memory_use_licensed` boolean, which must match retrieved-memory status plus
trusted-context grounding; replay/legacy manifests cannot claim that license. Existing
delivery goal/voice/grounding/continuation/pressure and cognition metadata remain available for
causal inspection. Raw state vectors, prompts, user text and provider prose are not copied into
manifest diagnostics.

The four signal families are mutually consistent with the owner contexts included in the request;
personality and values are mandatory for a fresh v26 generation, while affect and relationship
signals are absent exactly when those owner reads are absent. Trait/value presence meanings are
defined once by the canonical runtime-self mapping rather than duplicated in the renderer.
`RuntimeCharacterContext` rejects blank or duplicate keys, bool-as-number values, non-finite
strengths and values outside `[0,1]` before projection. Non-generation replay may omit the entire
transient decision/presence projection. Replay cannot turn manifest metadata into state or
generation authority.

### Offline acceptance and provider boundary

The versioned v9 deterministic corpus is built from public user inputs rather than precomputed
delivery booleans or fixture assistant history. It contains exactly 40 scenarios across 16 behavior
groups, 32 closed semantic properties, five controlled state contrasts, and two committed `Talk`
flows totalling seven public turns. The contrasts vary affect, relationship, memory, inclination
and relationship-modulated initiative while holding request/truth scope where appropriate. The
live flows use actually committed canonical replies and subsequent request history.

Neither the corpus nor its assertions judge generated wording. It contains no golden reply,
desired phrase, assistant text or model-generated prose authority. It verifies the public-input
route, cognition, owner support, one presence layer, historical isolation, canonical commit and
truth-scope invariants. Direct human review of separately authorized sampled provider output
remains the only character-quality decision.

The focused core architecture audit verdict is `CLEAN WITH MINOR ISSUES`; it found no critical or
high defect and no reason to open Stage 15. This is not the complete rebuilt Foundation gate.
No v26 OpenAI, Yandex or other paid/provider call has been made. Before any such call, an immutable
evaluation plan must identify the exact public turns, fresh-session shape, model, reasoning,
maximum base/retry calls, hard budget, retained safe metadata, review contract and digest. The user
must explicitly authorize that exact plan. Offline correctness cannot accept provider fit.

Stage 15 remains locked. It was audited and deliberately not opened because autobiographical state
does not repair the live state-to-expression bridge.

## Consequences

- Live trait/value differences and bounded evolution cues can now change the same provider-facing
  presence that carries cognition, affect and relationship modulation.
- Affect and relationship retain richer qualitative causal signals without exposing numeric owner
  vectors or creating another persistent aggregate.
- Character delivery no longer competes across stacked style/checklist blocks, and v26 trusted
  prompt content is smaller than the comparable v25 request in the production-wire regression.
- Historical v25 social/self-disclosure, provider-failure diagnostics and explicit relationship
  recovery remain reproducible and available; only its current delivery/projection role is
  superseded.
- Historical v24/v25 paid evidence remains inspectable, but their executable paid entrypoints are
  retired so an obsolete authorization cannot be replayed as a new network run.
- The offline v9 corpus is broader and closer to production lifecycle behavior, but it deliberately
  cannot decide whether sampled prose sounds human or recognizably like Satori.
- V26 provider fit, employer-demo readiness and Checkpoint 14.2 acceptance remain open until an
  explicitly authorized immutable provider gate and direct human review complete.
- No Stage 15 state, migration, second personality/affect/relationship owner, output rewrite,
  judge model, extra retry, automatic fallback or paid call is introduced.
