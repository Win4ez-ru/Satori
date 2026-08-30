# ADR 0043: Live-state-selected character movement

- Status: Accepted
- Date: 2026-08-30
- Supersedes: ADR 0042 (current candidate selection and provider rendering only)
- Related: ADR 0002, ADR 0003, ADR 0015, ADR 0020, ADR 0021, ADR 0023, ADR 0027,
  ADR 0031, ADR 0040, ADR 0041, ADR 0042

## Context

V26 connected live owner state to one typed presence projection, but its final OpenAI attempt-5
sample was rejected by direct human review. Across 24 replies, recognizable Satori presence passed
11 times, natural non-checklist delivery 10 times and absence of generic assistant/therapist closure
only twice. The all-replica gate failed. This rejects the frozen V26/Terra composition and sample;
it does not prove a provider ceiling.

The audit found a reverse-causality defect inside the otherwise valid V26 owner architecture:

- delivery goal and voice were selected before live personality strengths, current values and
  evolution cues were considered;
- the later presence projection selected traits and values to explain the already chosen voice,
  so state changed a provider checklist more often than it changed the conversational movement;
- every current value is canonically `1.0` and has no current drift writer, so treating equal value
  strengths as a source of dynamic variation would be false; values can constrain and
  counterweight a move, but current variability must come from situation, personality cues,
  affect and relationship;
- V26 rendered a 1,825–2,341-character inventory of center, values, affect, relationship,
  cognition and prohibitions on each calibration turn. The model was asked to execute a character
  card instead of making one situated response;
- ordinary depletion still left room for an unrequested recovery action, while explicit
  disagreement and topic closure had no typed delivery act of their own;
- tests proved prompt differences and schema validity, but not that live state selected a different
  movement before rendering.

Stage 15 autobiographical state would add facts but would not repair this ordering or instruction
shape. A new personality owner, output rewriter, phrase bank or judge model would violate existing
ownership and canonical-output boundaries.

## Decision

### V27 remains inside Checkpoint 14.2

Behavior policy v27 is the current offline production-composition candidate. It advances
`CharacterDeliveryDecision` to schema 4 and `CharacterPresenceProjection` to schema 2. It does not
open Stage 15, add persistence or change any provider port.

The current request-local path is:

```text
cognition-owned stance, intent, points, uncertainty and forbidden claims
  + current request evidence and disclosure
  + live personality strengths and bounded evolution cues
  + canonical values as contextual guards
  + current affect and relationship owner projections
→ one schema-4 delivery movement selected before rendering
→ one schema-2 presence projection proving selected state provenance
→ one compact operational-move block
→ unchanged foreground provider and canonical reply
```

No second `CharacterExpressionPlan`, personality engine or persistent posture object is added. The
existing typed decision fields are the request-local posture: goal, voice, grounding, continuation
and pressure. The schema-2 presence signals record which live owner inputs caused or bounded that
posture. The projection has no writer and provider output has no route back to it.

### Live state selects before prose

Schema 4 receives the complete validated runtime personality expression, traits and values before
constructing `CharacterDeliveryDecision`. It selects only among voices licensed for the already
cognition-owned conversational stance and act. This cannot change facts, evidence scope,
uncertainty, safety or whether a substantive answer is required.

Personality strengths and bounded `slightly_stronger`/`slightly_softer` cues can change the selected
voice and primary operational impulse. Affect can change energy, openness, concern, reflective
weight or playful edge. Established positive relationship may allow more ease and teasing.
`guarded_only_when_relationally_relevant` affects delivery only when the current turn is actually
relationship-relevant; it is not a global punishment mode. Important help remains complete even
when its voice is reserved.

Topic closure is explicitly relationship-relevant only for its bounded closure movement: fresh or
strained closure completes and may use licensed reserve, while established positive closure may
use greater ease and make exactly one adjacent or new-topic move. Direct current-turn devaluation,
safety, explicit listen-only need, repetition and cognition-owned repair keep their earlier
precedence.

Canonical values remain immutable strengths of `1.0` in the current product. V27 selects exactly
one contextually relevant value guard for a move; it does not claim value drift. A counterfactual
contract test proves the selector remains well-defined if a future accepted owner changes value
strengths, but current runtime variation is not attributed to that unavailable path.

### Two narrow conversational acts

Schema 4 adds request-local `respond_to_objection` and `close_topic` goals under the existing
`ANSWER` cognition stance.

`respond_to_objection` requires a direct unquoted, non-hypothetical disagreement cue and an
immediately preceding canonical assistant turn. Assistant history is only conversational context:
the move asks for a current evidence-grounded evaluation and explicitly forbids promoting the
previous generated wording into a durable Satori position. Canonical positions remain owned only
by the Stage 11 position aggregate.

`close_topic` is recognized only by a narrow complete current-turn closure. It never summarizes the
old topic. Its continuation is complete unless live relationship/affect permits one bounded move.
Quoted examples, hypotheticals and first-turn disagreement fail closed.

Ordinary depletion with `pressure=none` selects one personal practical reaction and no default
advice or action plan. Its visible output limit is 96 tokens. High distress and explicit
listen-only language still select presence; harmful overextension still selects a firm protective
boundary; explicit motivation may license a bounded push. These are prompt-level deterministic
guarantees, not a claim that unaudited provider prose already satisfies them.

### Cognition remains the owner of substance

The compact renderer may not discard cognition merely to reduce prompt size. V27 validates the
complete V3 cognition contract and carries, in one compact boundary, the current-request point,
relevant analysis/creative/follow-up support, uncertainty, all four forbidden-claim categories and
the cognition-owned verbosity. Character movement controls how that substance is spoken, never
what evidence becomes true.

No cognition schema or owner is added because objection and closure do not introduce a new durable
belief or semantic planner. If a future feature needs to revise a canonical Satori position, it
must use the existing typed position proposal/owner path and a separate ADR.

### Compact rendering and historical isolation

V27 renders one `Trusted current-turn presence Сатори / operational move v2` block. It contains
one movement, one selected voice, one or more directly operational personality effects, exactly one
value guard, relevant qualitative state, the compact cognition boundary, grounding and one ending.
It does not render the V26 inventory headings `Устойчивый центр`, `Текущие значимые
ориентиры` or `Момент`.

On six identical public inputs, the V26 presence layers total 11,866 characters and V27 totals
6,465 characters, or 54.48% of V26. This is structural prompt evidence, not character-quality
acceptance.

Policies v24–v26 remain explicitly reproducible. All 40 V26 public scenarios retain an aggregate
provider-projection digest of
`183ab47b3cbae0e5a1f124253f0182dbc279489bda7fbee460efa22887d6acb5` after V27. Four selected
presence blocks also retain their exact historical SHA-256 and size.

The V26 paid runner is retired before settings, filesystem claim, fingerprint, provider runtime or
network access. Retained attempt-5 evidence is validated only against its embedded immutable plan
and source fingerprint, never against the current V27 tree. Local claim/report/review files remain
private runtime artifacts and are ignored by Git.

### Evaluation and provider boundary

The V27 deterministic corpus contains only public user text and named owner-state variants. It has
no desired reply, assistant reply, golden phrase, precomputed goal/voice or provider output. The
exact eight-turn flow, all 40 historical public scenarios and a separate broad behavior corpus test
routing, state contrasts, precedence, memory license, relationship initiative and affect
continuity without judging prose.

The real OpenAI adapter is exercised offline: messages remain byte-identical, `store=false`, no
tools or stateful conversation handle is sent, visible limits receive only the configured reasoning
allowance, and normal generation uses one call. The existing validator may make at most one retry;
the selected V27 movement, parameters and user message remain byte-identical and no third call is
possible.

Only a separately authorized immutable production sample with direct human review can decide
whether V27 actually sounds recognizable, natural and non-generic. No OpenAI, Yandex, Ollama or
other provider generation was performed for V27 by this decision.

## Consequences

- Live personality cues, affect and relationship can now alter a typed conversational movement
  before provider wording is produced.
- Values constrain one move honestly without inventing a value-evolution path that does not exist.
- Cognition truth and response-substance ownership remain intact in a materially smaller prompt.
- Objection and closure receive bounded situated behavior without treating assistant history as
  self-state or adding an initiative percentage engine.
- Historical V26 paid evidence remains inspectable and byte-stable, but cannot be executed again.
- The broader deterministic corpus improves architecture confidence but cannot establish
  recognizability, humanity or provider fit.
- No persistent state, migration, Stage 15 feature, output rewrite, judge model, phrase bank,
  automatic fallback, extra retry or paid call is introduced.
- Checkpoint 14.2 remains open and behavior policy v10 remains the last provider-accepted baseline
  until a new human-reviewed V27 production gate passes.
