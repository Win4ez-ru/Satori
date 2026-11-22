# ADR 0051: Compact owned reaction and explicit-stop bounds

- Status: Superseded in part by [ADR-0052](0052-owned-news-and-care-realization.md) for active V33 expression; closure bounds, completeness-prefix correction, mixed style/direct-self scope and all other boundaries remain accepted
- Date: 2026-10-02
- Supersedes: ADR-0050 only for active provider realization as V32; ADR-0047 only for V32
  closure-language recognition; ADR-0045 only where a direct current-turn stop could permit
  a canonical-interest pivot; ADR-0041 only for the V32 style/direct-self scope combination
  described below. Historical versions and all other boundaries remain unchanged.
- Related: ADR-0048, ADR-0049 and the corrected character-convergence audit

Acceptance applies to this implementation design, not to candidate character quality.

## Context

Diagnostic 6 compared V31 (A) and V30 (B). The user's mixed feedback accepts ordinary greetings,
prefers B's personal disclosure and achievement reply relatively, rejects both ordinary-care
replies as recap, prefers A's contextual objection and slightly prefers B's restrained closure.
Neither version is accepted. The user clarified that character should be recognizable across
the dialogue; an ordinary neutral reply does not require a joke, question or tease.

The corrected audit distinguishes evidence from
causal claims. Equal `close/stop` with and without history does not prove lost context or a bad
selector: exact recent dialogue and the existing no-agreement instruction reach the provider.
An unanswered question is not automatically an unfinished-thread state or a duty to ask again.
There is a narrower selection gap: a direct stop can still permit an adjacent canonical-interest
move. Ordinary assistant-like output also persists despite existing selective-coverage permission.

The user explicitly authorized continuing this scoped implementation after the corrected review.
That authorization accepts the design work, not runtime character quality, a paid experiment or
another phase or stage.

## Decision

Introduce policy V32 as the implementation candidate inside Checkpoint 14.3 Phase A. Replace
active provider expression with one compact realization of the existing frozen agency decision
and canonical personality/value projection. The application continues to select act, subject,
initiative and lead before cognition completion. The provider chooses words and optional casual
topics within that decision; it does not reselect agency or persistent state.

Ordinary news and depletion allow a direct owned reaction instead of recap or explanation of the
user's feelings. Ordinary self-disclosure may select one supplied casual affect/interest topic;
affect can shape delivery without a compulsory self-report. Identity, memory, capability and
explicit complete task requests retain their direct factual or task route. Attribution of Satori's
previous words is checked against bounded recent dialogue, including a selected objection with
material uncertainty. Unknown details remain unknown. No desired reply prose becomes a template.

The compact realization retains the entire existing cognition support renderer exactly once,
the selected personality directions, value, grounding, pressure and relevant affect/relationship
bounds. Serious distress, listening, correction, repair, guardedness, explicit task completeness
and terminal stop remain binding. Ordinary `advance_current` elaboration is limited to an already
selected eligible current-exchange opening, with protected obligations taking precedence. It does
not turn every broad owned response into a question or alter the requested 10–20% design target
into a quota, random omission rule or accepted frequency.

### Request-local closure language

Append a defaulted typed `CharacterClosureKind` to `CharacterRequestEvidence`: `none`,
`end_discussion` or `claimed_resolution`. Only policy V32 populates the new kind through a
conservative whole-turn grammar. Direct stop/end language is distinguished from a formula such
as “кажется, с этим разобрались”. Quoted, reported, hypothetical, interrogative, other negated
and mixed task/listen/state turns cannot acquire whole-turn closure authority from an embedded
phrase. Historical policies through V31 retain their original parser and the default `none`.

This kind records current wording, not the user's true belief or proof that an earlier question
was answered. It does not inspect history, classify semantic answers or create pending questions.
Bounded canonical dialogue remains available for normal provider realization. A closing reply may
briefly preserve an evidenced objection or uncertainty and stop; repeating a question, demanding
an answer or inventing disagreement after an actual resolution is not required.

At the existing closure priority, `end_discussion` selects `close/stop` before any owned-interest
pivot and prevents broadening the inclination read to an adjacent interest. Existing higher
safety, repetition, listening, correction, repair and guarded priorities remain unchanged.
`claimed_resolution` preserves the previous eligible canonical-interest option; absent that
option it still closes. Neither classification permits a biography, invented hobby, promise or
out-of-band action. Agency/delivery/presence/manifest schemas remain 1/5/3/17; cognition remains 2/3.

### Narrow completeness-prefix correction

An additional deterministic control reproduced a pre-existing failure: the direct prefix
“Ответь на каждый вопрос:” is misread as a standing no-routine-question correction. When the
remainder mixes identity/interests/affect, that false correction can select style calibration and
produce an invalid disclosure plan before generation. In V32 only, the anchored, unquoted direct
prefix is removed from the input to that one existing correction detector; the remainder still
passes its unchanged correction/reset grammar. A real question correction in the remainder or
bounded earlier user history stays active. Current intake, disclosure and request assembly share
the same policy version. Historical/default analysis through V31 remains unchanged.

This is a narrow disambiguation of the exact direct completeness form, not a general semantic
parser, a question ban reset or permission to omit requested points. Quoted, reported, hypothetical,
negated and other expanded prefix forms keep historical treatment. The newly reproduced control
is outside the frozen 58 V31 requests and retains its historical failure as evidence.

A separate control confirms that a genuine current style correction mixed with a direct
interest/self question can also produce the same invalid disclosure plan. V32 allows the existing
`STYLE_CALIBRATION` primary action to carry an orthogonal `SATORI_SELF` request scope with
existing authoritative self facets. The producer requires an actual parsed direct self request;
empty/origin-only scope and missing provider/relationship coupled facets are rejected. This does
not introduce a new facet, schema or owner. The primary-action helper intentionally remains false
for this style route: cognition gains no `SELF_DISCLOSURE_REQUEST` signal that could displace the
real correction. Correction/repair priority and `address_current_request` survive, while facets
bound factual claims in the same reply. Quoted or hypothetical topics do not establish direct
self scope, and historical V31 rejection remains an explicit control outside the frozen 58.

### Isolation and ownership

V31 source and 58 actual request/parameter hashes were frozen before edits. The source archive
contains 390 files, SHA-256 `b3bd98eaee77f06de0091f5e618232a220f1ef8577f8b6337788791c058309d8`;
the request fixture SHA-256 is
`12d11b5f925e1cd41519744c09231e34ea55ead14b2c41f5792431d2ec1a2525`.
The retained V31 real matrix matches its preserved source fingerprint and is reused as historical
before evidence, not presented as a new run. Earlier Diagnostic 5/6 artifacts and consumed grants
remain immutable. V10 remains the accepted baseline.

No owner, persistent state, seed, table, migration, model, provider budget, native cap constant,
validator reason or retry limit changes. The V32 final-block marker uses the existing shared
max-one retry path, retaining the same selected decision, facts and realization. No output rewrite,
model judge, additional semantic retry, phrase bank or raw chain-of-thought storage is introduced.

The corrected direct-stop selection uses the existing close cap rather than the existing share
cap: the frozen `owned-stop` control changes from 384 to 96 tokens. This is a consequence of the
scoped act correction, not parameter equality across all V31/V32 controls; the other 57 matched
controls retain agency and parameters, and all 58 retain cognition.

## Verification and gate

Before sampled verification, rebuild the non-editable wheel and run the complete Foundation
workflow from AGENTS.md. Deterministic controls cover exact historical V31 request bytes, enum
typing and excluded grammar, stop versus claimed resolution with and without supplied interests,
real bounded histories containing answered and unanswered questions, protected priorities,
complete cognition, parameter parity, commit/replay, fallback, one shared retry and unchanged self.
History controls do not algorithmically judge whether an answer resolves a question.

Run every applicable real-Ollama character/dialogue regression required by AGENTS.md: three fresh
exact production sessions, the 30-turn coherence corpus, activities, relationship conditions,
identity/independence/technical probes and exact CLI gender/identity scenario. Retain public replies
and failures with prompt/token/provider/commit/post-response timings; phrase hits and question
marks are supplementary metadata, never human recognition scores. Compare prompt size and
latency without treating a shorter prompt or faster response as proof of character improvement.

A future short target-provider diagnostic must use varied new ordinary dialogues and closure
controls, a new frozen plan, explicit call/cost authorization and a fresh one-shot claim. It adds
semantic evidence; it cannot waive mandatory AGENTS.md checks. Human review evaluates recognizable
Satori across each dialogue, own reactions, continuity, selective casual delivery and factual/task
hard gates. Failed realization calls for a controlled selection/cognition/renderer/model-fit
diagnosis, not another unsupported “be livelier” instruction or an automatic model switch.

Implementation and verification results are tracked in
the V32 record. Acceptance requires completed mandatory
technical checks, an actual separately authorized human sample and a separate decision. Phase B,
Checkpoint 14.4, Stage 15, voice, avatar and external integrations remain unopened.
