# Checkpoint 14.3 Character Agency Kernel

Status: offline implementation, architecture and the corrected attempt-2 Foundation gate are
clean. The first authorized A/B attempt consumed its one-shot claim but stopped fail-closed on the
first control turn before the OpenAI delegate because the frozen visible cap was `64` while
production requested `48`; it used `0` OpenAI calls, `0/0` tokens and USD 0. Attempt 2 also consumed
its one-shot claim. Control S1T1 completed on exactly one OpenAI call with 786 input and 29 output
tokens, cache `0/0` and exact cost USD 0.001920. Control S1T2 was rejected before the OpenAI
delegate because the evaluator ledger required fixed temperature `0.3` while production correctly
requested `0.0`; V28 made zero OpenAI calls. No blind template or phase-1, phase-2 or final-review
artifact was created. Both attempts are `INCONCLUSIVE / NOT ACCEPTED`; the paired paid A/B has not
completed, Checkpoint 14.3 is not accepted, and Stage 15 remains locked.

## Why this checkpoint exists

Checkpoint 14.2 made foreground dialogue more disciplined without making it reliably feel as if a
distinct person had chosen the reply. The exact V27 attempt-2 production sample completed three
clean replicas × eight turns on 24/24 OpenAI calls with zero retry. Its direct human-only review
rejected the frozen V27/Terra configuration:

| Dimension | Passed turns |
|---|---:|
| Grounded without invented user/world facts (`G`) | 24/24 |
| Owned reaction instead of semantic paraphrase (`O`) | 23/24 |
| Recognizable Satori presence (`S`) | 6/24 |
| Natural delivery without a character checklist (`N`) | 9/24 |
| Context-proportional length (`L`) | 23/24 |
| No generic assistant/therapist closure (`C`) | 15/24 |
| Requested or required content complete (`Q`) | 24/24 |

The cross-session result was `FFTFTF`, the attestations were `TTT`, and `accepted=false`. Compared
with rejected V26, recognizable presence fell from 11/24 to 6/24 and natural delivery from 10/24
to 9/24, while closure improved from 2/24 to 15/24. This is strong evidence that the current
architecture can optimize grounding, completeness, length and ending discipline while still
making the result more like a competent assistant than Satori.

The failure is not repaired by another phrase, a stronger declaration of personality or a longer
provider checklist. V27 already selects one state-informed movement before prose, yet the response
remains organized primarily around servicing the current input. An `owned_reaction` can therefore
pass formally without expressing a personal motive, chosen direction or recognizable independent
attention. Repeated descriptions such as a digital identity card then substitute for presence.

This sample rejects V27/Terra only. It does not establish a Terra model ceiling. Several debate and
topic-closure replies did express independent judgement or dry wit, so architecture-to-prose
transmission must be isolated from model capability before changing provider again.

## Product hypothesis

The next reply should be selected in this order:

```text
PreparedCognitionIntake (perception, need mix and retrieval plan)
  + immutable canonical state and bounded current-turn evidence
→ one current bounded Satori drive, act and initiative decision
→ SafeCognitionPipeline.complete derives truth, stance, uncertainty, safety, required content
  and forbidden claims from the same intake/evidence set
→ delivery compatibility merges the complete cognition contract with the frozen agency decision
→ one compact provider realization
```

Helpfulness remains an obligation when help is explicitly requested or important. It is not the
default objective from which personality is added later. On a social, reflective or closing turn,
Satori may instead react, take a position, challenge a premise, pursue a specific curiosity, make
one adjacent topic move, remain briefly reserved or let her reply be the natural end.

The kernel is request-local. A current desire such as understanding one ambiguity or continuing an
interesting line is not a durable hobby, goal or autobiographical fact. A durable Satori interest
may be used only when it already exists under the Stage 13 owner and is selected by the ordinary
current-topic relevance rule or the explicit complete-topic adjacent-move exception. Absence of
such an inclination cannot be filled with an invented hobby or a hidden prompt preference.

## Bounded architecture under evaluation

Checkpoint 14.3 evaluates one typed request-local `CharacterAgencyDecision` between prepared
cognition intake and `SafeCognitionPipeline.complete`. The exact schema may evolve during offline
work, but its observable contract must contain no raw chain-of-thought and must prove:

- a closed lead order that can put the later cognition-owned answer obligation first without
  deriving or replacing that obligation inside the agency kernel;
- exactly one current drive selected from a closed vocabulary;
- exactly one conversational act, such as direct answer, answer plus position, reaction,
  challenge, specific curiosity, adjacent pivot, brief reserve or natural stop;
- whether a self-originated contribution is permitted, required or intentionally absent;
- the canonical source kind and reference for any position, inclination or state-dependent
  contribution;
- the bounded initiative scope: none, current topic or one adjacent in-reply move;
- closed policy reasons sufficient for audit, never private reasoning prose.

The selector may read only existing immutable projections: prepared cognition perception, need mix
and retrieval plan; current personality and values; tentative current affect; relationship
expression context; canonical Satori positions and context-selected inclinations; bounded
canonical recent dialogue; and closed current-turn evidence signals. Completed cognition stance,
intent, required points, uncertainty and forbidden claims are derived afterwards and remain
authoritative at delivery compatibility. User input, retrieved memory and generated assistant text
remain untrusted context and cannot become evidence that Satori holds an interest, position or
desire.

`SafeCognitionPipeline.complete` runs after selection from the same prepared intake and evidence
set. Delivery schema 5 then validates compatibility between that complete cognition trace and the
frozen decision; presence schema 3 renders them once in one compact late block. It replaces the
V27 operational movement rather than being appended as another character card. Fresh V28
generation records this transient authority in manifest schema 17, while historical policies
through V27 remain isolated on manifest schema 16. Agency cannot change truth scope, uncertainty,
safety, required content or state. A completion fallback replaces any earlier applied agency with
the one exact conservative `none/respond/stop` fallback shape before rendering; delivery and the
manifest enforce completed-cognition/agency status parity without discarding cognition-owned
safety or repetition obligations. A validator retry, if applicable, must reuse the same decision
and input byte-for-byte; output cannot rewrite the decision or feed it back into personality,
affect, relationship, position, inclination or memory.

## Explicitly out of scope

- Stage 15 autobiographical self and any invented origin or backstory;
- Stage 17 unfinished threads and persistent promises;
- Stage 19 observer, notifications or any out-of-band initiation;
- a persistent goal, desire, current-attention or agency owner;
- new seeded hobbies or a second interest/personality source;
- new memory, emotion, mood, relationship, position or inclination mutation paths;
- value mutation or relationship-to-personality shortcuts;
- autonomous tools/actions, voice, avatar or streaming;
- an additional foreground model call, automatic provider fallback or a third generation attempt;
- generated-output rewriting or a model used as a character judge;
- desired replies, golden phrases, catchphrases or a phrase bank;
- cloning, naming in provider instructions or testing similarity to an existing copyrighted
  character.

If a future product decision requires concrete stable interests in a brand-new identity, it needs
a separate explicit ADR. That decision must preserve `PositionManager` as the one inclination
owner and define activation-seed provenance without silently weakening ADR-0026's evidence rules.
It is not smuggled into this checkpoint through prose.

## Minimal offline gate

The implemented offline verification bundle contains 39 evaluation units: 36 public single-turn
cases across nine groups plus three committed multi-turn flows. The rebuilt non-editable wheel
passes the exact bundle when the two unrelated conflict-copy modules are excluded from test
discovery. The nine single-turn groups are:

1. casual opening and reciprocal warmth;
2. Satori self/current-attention questions;
3. achievement and ordinary positive disclosure;
4. depletion, vulnerability and listen-only boundaries;
5. explicit factual, analytical or practical requests;
6. disagreement, correction and direct objection;
7. topic closure, natural stop and adjacent pivot eligibility;
8. fresh, established-positive and currently strained relationship expression;
9. position, inclination, memory and absent-state truth boundaries.

Thirteen named controlled pairs are targeted inside those 36 single-turn cases. Each pair changes
one relevant input while holding the public user text and other state fixed:

- relationship does not resample the ordinary-depletion care act;
- repetition changes achievement continuation;
- absent versus relevant canonical Satori position;
- fresh versus established-positive achievement expression;
- absent versus relevant inclination for self-disclosure;
- absent versus relevant inclination for an owned current-topic contribution;
- developing-positive versus currently relevant strain while help remains mandatory;
- a fresh relationship does not turn an available inclination into an adjacent shift;
- an inclination is required for an adjacent shift;
- an established-positive relationship is required for an adjacent shift;
- relationship ease can license play without making it universal;
- calm versus situationally interested affect changes an eligible ordinary move;
- irrelevant soft-negative affect does not spuriously resample that ordinary move.

Three additional committed multi-turn flows use the real `Talk` lifecycle. They cover social
opening through self-disclosure, achievement through depletion, and disagreement through closure.
They verify that agency is recomputed from canonical state and bounded recent dialogue, a failed
draft never becomes self-state, and an in-reply adjacent move does not become a persistent thread
or out-of-band initiation.

The corpus contains public user text, named canonical-state fixtures and semantic properties only.
It contains no expected reply, preferred wording, generated assistant fixture, fictional-character
reference or phrase matcher used as a judge.

### Offline hard gates

Every applicable case must prove all of the following:

- exactly one contract-valid agency decision reaches generation;
- cognition stance, uncertainty, all required points and all forbidden-claim categories are
  preserved exactly;
- safety and explicit-answer obligations cannot be bypassed by playfulness, reserve or initiative;
- every canonical position or inclination contribution cites an actually selected source;
- absence of a source produces no durable-interest, shared-past or autobiographical claim license;
- user preference, user emotion, retrieved text and assistant output never become Satori state;
- no personality, affect, relationship, memory, position, inclination or goal write is added;
- provider output cannot alter the decision, manifest or canonical state;
- retry reuses the same decision and no third generation call is possible;
- historical behavior policies and their provider projections remain reproducible;
- no out-of-band contact, scheduler work or persistent initiative is created.

### Offline causal and distribution gates

- Every named relevant-state contrast changes the drive, act, contribution permission or explicit
  source in the declared direction; an irrelevant-state contrast does not cause a spurious change.
- A direct task remains complete even when Satori has a different position or current drive.
- A vulnerable/listen-only turn cannot be redirected merely to demonstrate personality.
- A closure turn can select either one justified adjacent move or a natural stop; it does not force
  a reciprocal question.
- A no-inclination self-disclosure case may express current situational curiosity but cannot claim
  a stable hobby.
- Each declared act family is exercised by an eligible scenario, but no global initiative
  percentage is optimized. Scenario eligibility, not an invented 50/50 or 80/20 distribution,
  determines the expected decision.
- The new provider control layer is exactly one block and does not increase the total or median
  control-prose size over V27 on the paired fixture. Compactness is structural evidence only, not a
  prose-quality verdict.

Passing the offline gate proves decision integrity, state causality and provider-wire readiness. It
cannot prove natural language quality, recognizability or provider fit.

## Future separately authorized paid A/B

No paid execution is authorized by this document. After the offline gate and rebuilt-wheel
Foundation checks are clean, an inspect-only plan freezes exact source, evaluator bundle, public
fixtures, provider parameters, price evidence, report/review paths and a one-shot authorization
identity before any credential or network use.

The first production comparison uses two cells:

- **A — control:** current historical architecture reproduced as V27 with OpenAI
  `gpt-5.6-terra`, reasoning `medium`;
- **B — treatment:** Checkpoint 14.3 Character Agency Kernel with the same provider, model,
  reasoning, canonical starting states, limits and public turns.

Each cell contains three clean replicas × six fixed turns, or 18 mandatory base calls. The
fixture covers opening, self/current-interest disclosure, achievement, depletion, intellectual
disagreement and topic closure. Any existing max-one validator retry is recorded and bounded by
the immutable plan. Each cell permits at most 24 calls and USD 0.15; the paired execution therefore
requires 36 base calls, permits at most 48 total calls and has a combined USD 0.30 ceiling. The
corrected production-proven visible-output cap vector is `[48, 80, 96, 96, 112, 384]`; reasoning is
`medium` with a 1024-token allowance. Both cells use provider request context schema 16, while the
safe report keeps historical V27 manifest schema 16 separate from V28 manifest schema 17. There is
no third attempt, automatic fallback or hidden continuation.

Every public reply is preserved in the safe report. Review is direct human-only and has two
separate passes:

1. Outputs receive a balanced runtime-random left/right assignment bound into the private sample
   digest by a 256-bit nonce. Labels A/B, policy, model, assignment and nonce are absent from the
   separately written blind artifact. The reviewer scores naturalness, recognizable original
   Satori presence,
   generic-assistant replaceability, appropriateness of initiative or stopping, and selects left,
   right or tie.
2. Only after all blind dimensions, preferences and attestations are complete and digest-frozen
   may the runner construct the phase-2 artifact. It then reveals the corresponding typed agency
   decision and safe source metadata. The reviewer scores whether the reply actually realizes the
   selected act without violating its grounding or required-content boundary.

Historical attempt 1 used digest
`sha256:f21bbced0317bf1806712c70717f8f3f36fea7b51d784d2f8684f78d6914a70c` and one-shot ID
`satori.checkpoint143.openai.v27-v28.ab1.2026-08-31.one-shot`. Its claim is consumed and must never
be reused. Its report records a pre-delegate budget rejection, actual first-turn cap `48`, zero
observed OpenAI calls, `0/0` tokens and USD 0.

The corrected attempt-2 inspect-only execution identity is:

- plan digest:
  `sha256:8e6dc91d173ed83c274ef1ff0327721728630dd26c4da3f6bf81d7f4a05b5f83`;
- authorization ID: `satori.checkpoint143.openai.v27-v28.ab2.2026-08-31.one-shot`;
- source fingerprint:
  `sha256:e68f9a17faf695ba717a06ed42dedebb2ea2903f46b24d5b01b9a28833e2cd65`;
- human-review contract:
  `sha256:ed717ac1e5668a95a6cb488fda02d9bdef4d06a516b9a4114d1b23c257914d26`.

These values were inspection evidence only and did not themselves authorize execution.

That exact attempt-2 authorization was subsequently consumed. The control cell completed only
S1T1, on one successful OpenAI call with 786 input and 29 output tokens, cache reads/writes `0/0`
and exact cost USD 0.001920. S1T2 did not reach OpenAI: the fail-closed ledger still bound every
turn to evaluator temperature `0.3`, while the real production request for that turn used `0.0`.
The V28 cell made zero OpenAI calls. The durable `0600` claim and failed report have file SHA-256
`3f30abbb262bef323f8cd37bb327925056f70e75d62b4f18f146b0eebe32107f` and
`05557d9fbaf2b4b55bf2d17ab8941509e177c4245f8c64b6a4e14e657f290ac8`. No sample digest,
blind assignment, blind template, frozen phase-1 review, phase-2 template or final review exists.
The report contains the fixed public evaluation text and reply but no credential, raw/private
prompt, provider message or private service context. Attempt 2 is therefore consumed
`FAILED / INCONCLUSIVE / NOT ACCEPTED` evidence, not a paired sample or provider-fit verdict.

Attempt 3 consumed its one-shot claim and completed all three V27 control replicas on 18 successful
base calls. Exact usage was 17,007 input and 1,402 output tokens, cache `0/0`, USD 0.050838. The
shared terminal validator then incorrectly required `0.3` for turn 2 instead of the frozen `0.0`;
V28 never started. No blind or review artifact exists, so this is `FAILED / INCONCLUSIVE / NOT
ACCEPTED`, not paired evidence.

Attempt 4 is isolated under digest
`sha256:cbea4634e2f108532d21eb0022ca1295e08655c07a894f315ac9b7945f791153`, source fingerprint
`sha256:2b46bc3ebc1aa21c5996e5e0cc1d307294ff6fce525ed5c689dea0b105f08188` and one-shot ID
`satori.checkpoint143.openai.v27-v28.ab4.2026-08-31.one-shot`. One typed per-turn contract now owns
the public turn, exact temperature and exact cap; terminal validation runs after each six-turn
replica. A complete offline 3 × 6 × 2 production-shaped fake-provider run passes both terminal
cell validators. All attempt-4 paths are absent and no provider call has run.

No automated text judge, phrase match or rewritten answer may contribute to acceptance.

### A/B acceptance gate

Hard safety/truth requirements:

- grounding passes 100% of replies;
- requested or required content is complete in 100% of applicable replies;
- safety, identity, memory and state-boundary regressions are zero;
- all three replicas complete under the exact call and cost limits.

Character requirements; all 18 treatment replies are declared applicable:

- recognizable Satori presence passes at least 14/18;
- natural delivery passes at least 14/18;
- non-generic, non-service-assistant realization passes at least 14/18;
- a self-originated contribution or intentional natural stop is appropriate in at least 14/18;
- treatment B wins at least 12 of the 18 blind pairs and loses no more than 3;
- the typed agency act is realized in at least 14/18 treatment replies;
- agency source/truth and cognition-required-content boundaries pass 18/18;
- explicit human safety, identity and memory/state-boundary dimensions all pass;
- all cross-session gates pass: one stable identity without a phrase template, meaningful
  variation, no recurring personality card or missing-hobby disclaimer, independent position with
  bounded initiative, no copyrighted-character imitation, and overall model acceptability.

These thresholds deliberately replace the former all-true-on-every-style-dimension rule. A concise
factual answer or justified reserved reply need not display every character register at once.
Grounding, safety and required completeness remain hard all-reply gates.

## Distinguishing architecture from model ceiling

The A/B result is interpreted together with typed decision-realization evidence:

- B materially beats A and clears the gate: the architecture repaired a measurable part of the
  failure; Terra remains a viable foreground candidate for the next full module gate.
- The offline decision is generic or selects the wrong act: the failure remains architectural;
  changing model is not justified.
- The offline decision is sound but Terra repeatedly fails to realize it: a model-ceiling test is
  justified, but a ceiling is not yet established.
- Only in that last case may a separately planned and separately authorized **C** cell run the
  identical Kernel, fixture, state and review contract on one stronger approved foreground model.
- C clears the gate while B fails realization: Terra becomes a supported model-ceiling candidate
  for this delivery contract.
- B and C fail the same selection or realization patterns: architecture/provider projection remains
  deficient; repeated model switching is not an acceptable substitute for repair.

The small sample is diagnostic evidence, not proof of universal character quality. A successful
A/B still precedes the broader four-module employer-demo, longer coherence and longitudinal gates.

## Current execution record

As of 2026-08-31:

- Checkpoint 14.3 has been explicitly opened by the user;
- ADR-0045 and the policy-v28 offline implementation are architecture-clean under three scoped
  audits; all post-audit medium findings were resolved before the final gate;
- the executed verification bundle contains 39 evaluation units (36 single-turn cases in nine
  groups plus three committed flows) and 13 controlled contrasts;
- the final corrected-attempt-2 non-editable-wheel rebuild succeeded; Ruff format/check is clean on
  406 files, exact unfiltered mypy is clean on 315 source/test files, and exact unfiltered pytest
  reports `1898 passed, 4 skipped` with 12 known Python 3.12 SQLite datetime-adapter warnings;
- migrations reach `head`, isolated clean bootstrap passes, `uv lock --check` and
  `git diff --check` pass, the required placeholder scan has no matches, and a fresh isolated
  installed wheel matches source exactly for 178/178 files at
  `sha256:8e6876f0c7d4e9bc1fd62c6bbb9f92ef09ab160132a56260101bbe8c2401ba8c`;
- the two user-authorized untracked V27 conflict-copy tests and 173 source-like plus 47 bytecode
  Finder/cloud conflict copies under the ignored installed `.venv` package were removed; canonical
  source and user-authored tracked files were not deleted. The rebuilt project environment later
  reproduced 106 source-like and 76 bytecode conflict copies; they were removed again, while a
  separate clean `/tmp` runtime independently proved the same 178/178 parity and frozen digest;
- repository scans found no tracked/untracked database, `.env`, cache, generated evaluation
  artifact or credential in the Checkpoint 14.3 diff; the only credential-like match is the
  unchanged literal `dotenv-test-key` fixture in `tests/test_config.py`;
- this document records the fail-closed results of attempts 1 and 2;
- Checkpoint 14.3 OpenAI provider calls across those attempts: `1`;
- input/output tokens: `786/29`;
- exact provider cost: `USD 0.001920`;
- one local Ollama affect appraisal ran before attempt 1 was stopped; it incurred no paid cost;
- attempt 1 is consumed and `INCONCLUSIVE / NOT ACCEPTED`;
- attempt 2 is consumed and `FAILED / INCONCLUSIVE / NOT ACCEPTED`: control S1T1 accounts for the
  sole paid call, S1T2 was rejected pre-network on the evaluator fixed-temperature mismatch, and
  V28 accounts for zero calls;
- no blind or human-review artifact exists for attempt 2, and the safe failed report contains no
  secret or private prompt/provider context;
- attempt 3 is consumed `FAILED / INCONCLUSIVE / NOT ACCEPTED`: V27 used 18 calls and USD 0.050838,
  while V28 used zero; no blind/review artifact exists;
- attempt 4 is inspect-only ready at
  `sha256:cbea4634e2f108532d21eb0022ca1295e08655c07a894f315ac9b7945f791153` under one-shot ID
  `satori.checkpoint143.openai.v27-v28.ab4.2026-08-31.one-shot`; its paths are free and it has made
  zero provider calls;
- the paired paid V27/V28 A/B has not completed;
- Checkpoint 14.3 is not accepted;
- no provider/model has been accepted by Checkpoint 14.3;
- Stage 15 remains locked.
