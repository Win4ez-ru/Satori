# ADR-0058: Mixed affect before reaction freeze

- Status: Accepted
- Date: 2026-10-03
- Authority: the user's Character continuation after the first six-pair V34 human feedback
- Supersedes: ADR-0054 only where it preserves active reaction selection unchanged; ADR-0045
  only for V35 discretionary initiative selection and compatible delivery. Historical policies
  through V34 and all persistent-owner, cognition, evidence and correctness boundaries remain.
- Related: ADR-0047–0054, ADR-0057

Architecture acceptance does not accept candidate V35 or declare Character complete.

## Evidence and hypothesis

V34 exposes an existing bounded affect mixture inside the already selected move. The first human
review does not confirm the desired continuity. At the fourth reviewed node, canonical residual
frustration survives two subsequent turns and reaches the request alongside fresh positive
energy and interest. Both arms nevertheless select warm exploration with optional continuation.
This establishes a selection/disclosure mismatch to investigate, not the cause or target of the
emotion. Other feedback about excessive care, recap, technical reactions and malformed Russian
does not license fixing those independent problems in the same iteration.

Hypothesis: moderate mixed affect can constrain **discretionary readiness** before reaction freeze
while preserving interest, the current interpretation and required help. Realizing shades inside
an already expansive move is insufficient in some conversations. A positive new event need not
erase prior tension; persistent interest need not entail another question or playful warmth.

## Decision

Introduce candidate policy V35 with the same behavior principles, native parameters and V34 mixed
affect expression. Add one pure request-local projection in `character_affect_selection.py`.
It reads only the canonical tentative `EmotionalExpressionContext`; it has no text, relationship,
repository, provider, clock, evidence or mutation capability. Agency and delivery use this same
projection function on the same immutable tentative input before generation.

For fast frustration/tension, mood tension and negative fast/mood valence, normalize excess over
the owner's resting baseline by that dimension's existing one-event cap, clamp to [0, 1], and take
the maximum. This bounded load does not add correlated fast and mood dimensions. Readiness is
one minus load. Positive curiosity/interest/valence/amusement retain their baseline and scale
only positive excess by readiness. Missing affect yields the existing neutral selection; no
negative relationship or invented feeling is inferred. Concern alone does not establish
reluctance to engage and is outside this reducer.

This exclusion concerns the direct concern axis. Owner-derived mood tension remains canonical
input regardless of its origin; the projection cannot infer a cause from the snapshot. Mood
half-lives and per-event caps are unchanged, so their selection influence requires calibration
against sampled behavior rather than an assumed emotion or grievance duration.

These are selection units, not a new emotion policy or an offense label. Do not repeat the
personality sensitivity/patience factors already applied by EmotionManager. Existing canonical
personality/value scores, current evidence precedence and independent relationship selectors
continue to provide their distinct context. There is no new relationship mutation, permission,
causal attribution, trait seed, timer, persistent readiness aggregate or second personality.

### Reaction and expression

Preserve raw exploration and selected achievement motives, current subjects and source reasons
when advance is withheld. Adjusted readiness bounds discretionary advance/play eligibility using
existing activation thresholds; unrequested bare play and social play can give way to an ordinary
response or connection without assigning a new negative emotion.
Exploration may stay on the current topic without launching a new step. An activity can remain
interesting without asking the user another question. Optional adjacent movement and achievement
follow-through also respect readiness; explicit stop remains terminal. Where a canonical
inclination, activity or established relationship licenses optional movement without positive
affect activation, require at least half the readiness budget. This predeclared selection cutoff
is independent of test utterances and does not classify emotion. A withheld adjacent pivot uses
the existing closure; it does not manufacture a topic contribution after closure.

Delivery uses bounded continuous affect contributions within the existing act's licensed voice
set, rather than relying exclusively on one coarse profile. Familiarity remains independent;
its discretionary playful bonus cannot erase mixed affect. When nonzero mixed-affect load
constrains an ordinary move, optional continuation respects the frozen agency initiative instead
of being reopened solely by established relations. Resting/zero-load and missing-affect selection
stay identical to V34, so this iteration does not mix a global continuation cleanup into the
affect hypothesis.
Concern remains outside the initiative reducer; compatible delivery retains the existing
coarse tense-profile contribution when concern independently licenses it. An arbitrarily small
modeled residual cannot erase that prior contribution.
Required factual answers, specific follow-up, requested collaboration/motivation, safety,
distress, listening, correction and repair retain their existing substance and routing. The
provider realizes one chosen movement; no required phrase, question, grievance, apology or
visible emotion is added.

There are still five separate boundaries:

| Boundary | Authority |
| --- | --- |
| Emotion and mood | EmotionManager: bounded event application, numeric persistence, decay and reply-atomic commit. |
| Relationship | RelationshipManager: counterparty-specific canonical evidence and independently committed expression context. |
| Current interpretation | Existing appraisal and prepared cognition intake; no new context or causal attribution in this iteration. |
| Reaction | Request-local agency, then compatible delivery using the same canonical affect projection and complete cognition obligations. |
| Expression | Existing V34 mixed shades and the selected voice/continuation; prose cannot mutate or reinterpret the owners. |

Agency/delivery/presence/manifest schemas remain 1/5/3/17; cognition remains 2/3. Narrow newly
licensed non-advancing exploration topologies use the existing enums and provenance grammar.
Historical requests through V34 remain byte-identical. V35 joins the existing max-one retry;
both attempts keep the same tentative state, evidence, cognition and reaction. There is no text
rewriting, model judge, new validator reason or extra normal-turn provider call.

## Verification

Before edits, archive the verified V34 source and freeze all 95 successful V34 requests, their
native parameters and the existing inherited failure. Retain actual baseline failures and the
first human feedback without editing consumed execution records.

Version a deterministic corpus crossing real intake, agency, cognition and request assembly.
Hold current input/history/relationship fixed while varying canonical residual state; include
resting, mild, moderate, strong, mixed-positive, missing and owner-decayed controls. Test
non-increasing optional advance under increasing negative load, retained interest, relationship
isolation, personality variation, unrelated negative events, polite disagreement, protected
obligations, fallback, failure, retry, replay and absence of new state/audits. Discrete voice
changes are observations, not universal emotion obligations.

Run the rebuilt noneditable Foundation workflow, required 125-turn real-Ollama matrix and exact
four-turn `satori chat --debug` identity scenario. Report failures and all public replies together
with token/load/prompt-eval/eval/commit/background timings. A small separately frozen local
V34/V35 diagnostic must reselect agency independently in each arm from identical intake, state,
history and relationship; reusing the old frozen agency would not test this hypothesis. Shared
anchor history is a conditional control, not two independently evolving dialogues. Cross carried,
absent and decayed states offline; do not claim synthetic state contrasts are actual emotional
trajectories. Retain missing cells without resampling. No paid call is authorized.

Stop at the small human review if behavioral judgment is needed. Correct selection or an
instruction reaching the request is not proof of felt continuity. Appraisal's limited dialogue/
relationship context and emotion-cause attribution remain separate hypotheses, not extra scope.

## Parallel workstream

Character stays in Checkpoint 14.3 Phase A. No owner, read schema, migration, memory eligibility,
source-policy admission or recovery contract changes. Verify reproducibly on the immutable V34
snapshot plus this Character delta and retain current Foundations edits in the shared tree.
Its separate roadmap proceeds independently. Validation must distinguish isolated source parity
from any partial shared-tree parity; a Character result cannot accept or block Foundations.

## Later evaluation authorization — 2026-10-03

After questioning local dialogue representativeness, the user explicitly authorized one frozen
V34/V35 target-API diagnostic under
`satori.checkpoint143.v34-v35.api.2026-10-03.one-shot`, digest
`sha256:5e9552fed210a468b38204aaeae331588157af2ab9600c1d8f86410ad955d295`.
This later grant supersedes only the earlier absence of paid authorization for that exact
24-base / 36-maximum, USD 0.20 aggregate / USD 0.10 per-arm plan. Existing cloud foreground/local
appraisal boundaries, native caps, Character source and owners are unchanged. The invocation
completed 24/24 responses for USD 0.074660; its claim is consumed. Human review remains open,
with unchanged samples presented in two phases to prevent later anchor history from identifying
earlier samples. No additional call, architecture change or Character acceptance is authorized.
