# ADR 0054: Bounded mixed-affect expression

- Status: Superseded in part by ADR-0058 for active V35 reaction selection; mixed expression and all owner/correctness boundaries remain accepted
- Date: 2026-10-03
- Supersedes: ADR-0052 only for active affect realization as candidate V34. Its news/care,
  closure, protected-request and historical-isolation decisions remain accepted.
- Related: ADR-0015, ADR-0019, ADR-0020, ADR-0021, ADR-0045, ADR-0053

Acceptance covers this narrow architecture change, not Character quality or completion.

## Context and hypothesis

The user authorized one complete Character iteration after mixed V31/V33 feedback. Diagnostic 8
did not establish a whole-version winner or a canonical emotion reset. Its source-backed diagnosis
shows that the affect owner carries prior state, while peer realization consumes only one coarse
profile and ignores the existing detailed affect signals. Appraisal lacks recent dialogue;
that separate limitation is not changed in this experiment.

The absolute threshold of the coarse tense profile is 0.35. Starting from policy baselines,
one bounded event cannot reach it through tension (0.08 + 0.16), frustration (0.04 + 0.14),
concern (0.08 + 0.18), or mood tension (0.10 + 0.03). The normalized detailed projection may
nevertheless carry a meaningful qualitative signal. This is a deterministic disclosure gap;
these example bounds do not reconstruct the missing numeric Diagnostic 8 state.

Hypothesis: preserving the existing bounded mixture in the single expression block will make
moderate emotional consequences available to later replies without amplifying canonical emotion,
requiring offense, or changing the selected conversational action.

## Decision

Introduce behavior policy V34 with V33 principles and all retained V33 realization boundaries.
Replace only its coarse affect sentence with the existing, ordered, at-most-three
`CharacterAffectSignal` values and their qualitative strengths. Reuse the canonical rendering
meanings; do not add a second taxonomy, numerical narration, another block, a new ranking or a
mandatory reaction. Historical versions through V33 retain their exact requests.

The five boundaries remain distinct:

| Boundary | Authority and effect |
| --- | --- |
| Emotion and mood | `EmotionManager` owns numeric state, bounded impulses, lazy decay and atomic commit with the reply. |
| Relationship | `RelationshipManager` owns counterparty-specific evidence and post-response updates; affect is not new relationship evidence. Existing committed recent strain remains independently projected. |
| Current interpretation | Existing appraisal and cognition retain their input contracts, uncertainty and provenance. Recent dialogue remains bounded conversational context. |
| Reaction selection | The same frozen `CharacterAgencyDecision` and complete cognition obligations select act, subject, initiative and lead. V34 does not reselect them. |
| External expression | Existing affect shades may jointly influence attention, pace and sharpness within that selected action. A vector alone does not establish cause, target, offense or relationship change. |

A positive shade can coexist with residual tension; a neutral current message is not an emotion
reset. An explicit stop still ends the discussion. Genuine disagreement is not automatically
hostility, and an apology does not oblige immediate forgiveness. Technical work, safety,
listening, repair, truth and explicit-answer completeness remain protected. The provider may
realize a calm reply when appropriate; no phrase, question, resentment or conspicuous emotion is
required to pass a test.

Schemas remain agency/delivery/presence/manifest 1/5/3/17 and cognition 2/3. No owner, persistent
state, migration, seed, appraisal wire, decay parameter, selector, model, native output cap,
temperature or validator reason changes. The new expression marker joins the existing shared
max-one retry; the tentative evidence/state and chosen reaction remain identical across attempts.
Generated text is neither rewritten nor judged by another model nor treated as fresh self evidence.

## Verification and diagnosis

Preserve the complete pre-change V33 source and freeze all 95 successful V33 request/parameter
hashes before implementation. Verify those hashes, retained failures, state contrasts sharing the
same coarse profile, low/moderate/mixed/decayed affect, relationship isolation, protected requests,
fallback, replay, failure and retry. Exercise owner carry over neutral turns separately from
provider disclosure; projection must not create state or audits.

Run the complete rebuilt-wheel Foundation workflow, then the applicable real-Ollama 125-turn
Character/dialogue matrix and exact four-turn real `satori chat --debug` identity scenario.
Preserve failures, response and token/timing evidence, numeric before/after state and source-bound
appraisal metadata. No latency or instruction presence is semantic Character acceptance.

Prepare a small public local V33/V34 expression diagnostic with a predeclared shared-history
anchor, identical tentative state/cognition/relationship/native parameters per pair, and private
balanced random A/B labels. A shadow reply is not committed or used as evidence. This controls
expression, not two independently evolving relationships. Retain failures without resampling;
human review assesses continuity and proportionality, including calm negative controls. No paid
provider call is licensed by this ADR; any later paid test needs its own concrete authorization.

If state reaches the request but the sample remains generic, the realization hypothesis is
unsupported. Do not add rules for the tested input; separately investigate appraisal context or
selection with a new controlled hypothesis. Mandatory technical checks are not waived by an
optional blind sample. Results are tracked in `../performance/checkpoint-14.3-v34.md`.

## Parallel-workstream boundary

This is Character 14.3 Phase A. It changes no immutable owner read or storage contract consumed
by Foundations 14.4.1 under ADR-0053. Use an isolated pre-change source snapshot plus this
candidate for reproducible Character checks while preserving concurrent foundation edits.
Passing or failing this iteration does not accept Character, close Foundations or open a later
Character phase or Stage 15.
