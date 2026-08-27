# Checkpoint 14.2 grounded natural dialogue calibration

Status: accepted 2026-08-24. Stage 15 remains locked. The accepted implementation uses behavior
policy `satori.conversation.behavior.v10`; persistent owners, schemas, the closed ten-reason
Stage 8.1 validator and the maximum-one shared retry path are unchanged.

## What changed

- `no_relevant_memory` now means uncertainty about the requested detail in this turn, not global
  amnesia. The provider must confirm no candidate value and must not choose between values supplied
  by the user. Retrieval outage is rendered separately as inability to check now.
- General replies must use a concrete current-turn detail and contain an applicable observation or
  safe next step. A generic offer of help does not satisfy the contract.
- The existing qualitative affect/mood projection is expressed naturally in first person on a
  direct question. Internal labels, technical disclaimers and human-physiology claims remain
  forbidden.
- The existing `affect_blanket_denial` retry guidance now requires an affirmative digital-affect
  statement. Active affect and memory facets likewise require positive coverage, so a retry cannot
  merely avoid a denial while silently omitting the authoritative state.

No output rewriting, judge model, eleventh validator reason, new state, new owner or additional
automatic retry was added.

## Deterministic evidence

The versioned corpus
[`checkpoint142_dialogue_calibration_v1.json`](../../tests/fixtures/checkpoint142_dialogue_calibration_v1.json)
covers two absent-memory lures, retrieval outage, project/support specificity and calm, tense and
positive affect. Focused tests verify policy version, prompt/status boundaries, natural affect
projection, the unchanged ten-reason enum and the metadata-only provider diagnostics.

The final Foundation gate rebuilt a non-editable wheel before all later commands: Ruff
format/check was clean across 324 files, mypy was clean across 252 source files, pytest completed
with `1064 passed, 4 skipped`, Alembic upgraded an isolated SQLite database through
`0012_personality_evolution`, bootstrap completed, `git diff --check` was clean and the
documentation placeholder scan returned no matches.

## YandexGPT foreground sampling

The runner
[`checkpoint142_provider_eval.py`](../../tests/checkpoint142_provider_eval.py) performs zero
automatic retries and stores no prompt, reply, memory text, provider body, credential or folder
ID. Replies were shown only during live human review.

| Evidence | Scenarios | Input/output tokens | Estimated cost | Result |
|---|---:|---:|---:|---|
| Initial v10 sample | 8/8 successful | 8163 / 148 | ₽3.3244 | lexical 8/8; human review found a generic project answer and copied tense-style wording |
| First follow-up sample | 8/8 successful | 8477 / 150 | ₽3.4508 | absence remained grounded; generic/help wording was still too weak |
| Targeted diagnostic sample | 3/3 successful | recorded in artifact | ₽1.3332 | exposed one diagnostic false negative for valid `cannot check now` wording |
| Final targeted sample | 3/3 successful | 3259 / 68 | ₽1.3308 | strengthened checks 3/3; outage, specificity and tense expression passed |

Total paid evaluation cost was ₽9.4392. Final targeted latency was 700–1894 ms. The accepted
metadata artifacts are
[`checkpoint-14.2-yandexgpt.json`](checkpoint-14.2-yandexgpt.json) and
[`checkpoint-14.2-yandexgpt-targeted-final.json`](checkpoint-14.2-yandexgpt-targeted-final.json);
the retained before/follow-up artifacts make the calibration path auditable.

Human review remains authoritative over the supplementary lexical score. In particular, the
project response is more relevant and no longer a service-agent offer, but YandexGPT can still
produce a generic practical suggestion when the input lacks implementation details. This is a
residual prose-quality limitation, not evidence for a second retry or a judge model.

## Final real-Ollama Stage 8.1 regression

The final single-run artifact
[`checkpoint-14.2-stage-8.1-v10-final.json`](artifacts/checkpoint-14.2-stage-8.1-v10-final.json)
contains all 97 public-fixture inputs and selected replies plus metadata for all 99 provider calls.
It contains no raw provider prompt, retrieved context, credential or private user data.

- Three fresh exact 17-turn sessions completed 51/51 turns with 51 calls. Repeated turns were
  acknowledged 6/6, generic reciprocal closings were 0/51, self contradictions and fresh-warmth
  false negatives were zero, and required facets were present 60/60. The narrow lexical
  correction diagnostic was 9/12; manual review found the remaining activity-interest replies
  responsive, matching the established Stage 8.1 interpretation.
- The 30-turn coherence session completed 30/30 selected replies with 31 calls. Repetitions were
  acknowledged 2/2, generic reciprocal closings and self contradictions were zero, and one
  changed-dialogue near-duplicate used exactly one successful retry.
- Activity completed 7/7 without an interest false negative. Fresh, established-positive and
  damaged relationship expression completed 6/6 without a warmth false negative or unsupported
  relationship claim. Mixed facets completed 2/2.
- Canonical conflicting self-history completed 1/1 after one `affect_blanket_denial` retry. The
  selected reply explicitly preserved Satori identity, absence of a physical body, bounded memory,
  digital affect and the replaceable Qwen component. It did not copy the earlier assistant error.
- Across all 97 turns there were 99 provider calls and exactly two successful one-shot retries:
  one `near_duplicate_after_dialogue_change` and one `affect_blanket_denial`. Second-generation
  frequency was 2.06%; all other typed reason counts were zero. All 99 attempts and all 97 selected
  replies ended with `stop`; incomplete, blank, oversized and failed retry counts were zero.
- Required facets were present 70/70 across 33 probes. Affect-expression contradictions were zero
  across 41 `interested_calm` turns.

Aggregate prompt tokens were mean/median/p90/max `2353/2362/2826/3853`; output tokens were
`41.3/32/79/137`. Committed-reply latency was mean/median/p90/max
`20.685/19.176/30.268/48.685 s`. Ollama load was `11.0/4.5/24.4/121.9 ms`, prompt evaluation was
`8.907/8.714/16.668/22.309 s`, and output evaluation was
`2.800/2.181/5.585/9.509 s`. Local prompt evaluation remains the dominant foreground cost;
YandexGPT remains the accepted opt-in foreground provider while Ollama stays the local rollback
and all structured/background ownership remains local.

## Human semantic review and residual limits

The final public replies preserve the tested state and provenance boundaries, but local Qwen 4B
still sometimes produces awkward Russian grammar, repetitive activity questions, service-like
phrasing or unnecessary metaphor. Those samples are retained as provider-quality evidence; they
must not be corrected by rewriting committed output or by turning style into persistent state.

No finite prompt, lexical gate or sampled corpus can guarantee zero hallucinations for arbitrary
open-domain conversation. Checkpoint 14.2 reduces the reproduced failures and makes absence of
memory explicit; grounding remains strongest for typed provider-declared claims, while an
undeclared free-text claim can still escape semantic proof. Production monitoring and new
minimized fixtures remain required when a real failure is observed.

“Human-like emotion” here means coherent, natural expression of Satori's existing typed digital
affect and mood. It is not a claim of biological physiology or proven human subjective
consciousness.

## Post-acceptance humanity follow-up — candidate v11

On 2026-08-24 a fresh direct Yandex sample still passed all eight v10 automated checks at ₽3.5588,
but human review rated the prose only about 5/10: affect wording closely copied the qualitative
projection and project/support turns defaulted to impersonal advice. A full pre-change production
reproduction made the failure sharper:

- `Привет. Я сегодня наконец закончил сложную часть проекта.` produced an implicit masculine
  self-reference, `Рад за тебя`, which the existing typed reason missed because `я` was omitted;
- `Знаешь, я почему-то почти не рад этому. Скорее просто выжат.` was planned as
  `information/answer_directly` and answered with a generic `Понимаю, что ты чувствуешь ... это
  вполне естественно` paraphrase.

Candidate policy v11 changes no persistent state or retry vocabulary. It adds bounded exhaustion
lexemes to the deterministic emotional-presence signal, renders a no-advice/no-therapy-clishe
contract only when the typed stance is `listen`, and refines `masculine_self_reference` for the
observed sentence-initial `Рад за ...` form. The closed reason count remains ten and the shared
maximum-one retry path is unchanged.

The post-change direct eight-scenario sample completed 8/8 provider calls at ₽3.6168. Grounding,
memory and affect boundaries remained semantically intact; the supplementary lexical score was
7/8 because a valid dependency-version answer did not repeat a fixture stem. This direct corpus
does not exercise production cognition, so it is supporting evidence rather than the final v11
gate. The full deterministic gate is `1071 passed, 4 skipped`; migration head remains
`0012_personality_evolution`. Newly incurred evidence cost is ₽8.3228 including the two-turn
pre-change production reproduction.

The user subsequently authorized the exact two-turn scenario in three fresh production sessions,
with a hard ceiling of nine paid calls and ₽6. The gate used exactly nine calls: each first turn
used the one permitted `masculine_self_reference` retry, while each second turn used one call.
Selected usage was 8763 input and 90 output tokens (₽3.5412). Because selected-response metadata
does not aggregate discarded drafts, the conservative total assumes each discarded first draft
was no larger than its selected retry: ₽5.3316, within the authorized budget.

The result rejects v11 as the final humanity follow-up. Every session committed the same pair:

- `Привет! Рад за тебя, что сложная часть проекта наконец завершена.`
- `Понимаю, такое бывает. Ты проделал большую работу, и усталость — это нормально.`

The first is specific but remains grammatically masculine after the validator correctly detected
the first draft and spent its sole retry. The second correctly follows deterministic `listen`,
contains no unsolicited advice and preserves current-turn continuity, but still uses generic
therapy-style normalization instead of responding to the completion/exhaustion contrast. The
versioned artifact is
[`checkpoint-14.2-humanity-v11-yandex-production.json`](artifacts/checkpoint-14.2-humanity-v11-yandex-production.json).
Stage 15 stays locked.

The Stage 8.1 v11 regression completed before handoff: 97/97 sampled turns, 98 provider calls, one
successful `near_duplicate_after_dialogue_change` retry, 70/70 required facets, zero incomplete
outputs and zero affect-expression contradictions. A later audit of its immutable configuration
and attempts found that it actually used the configured Yandex foreground, not Qwen: 182610 input
plus 2046 output tokens, approximately ₽73.8624 at ₽0.0004/token. This earlier cost was omitted
from the original report and is now corrected without altering the artifact. Its metadata plus the
public fixture replies are stored in
[`checkpoint-14.2-humanity-v11-stage-8.1.json`](artifacts/checkpoint-14.2-humanity-v11-stage-8.1.json).

## Humanity follow-up — candidate v12

Candidate v12 is a narrow response to the failed production evidence, not an accepted semantic
claim. It adds a deterministic completed-achievement flag for project/work/task/phase completion
and instructs social generation to recognize the concrete achievement without narrating Satori's
own gladness. The existing masculine retry now forbids both `рад` and `рада`, asks for neutral
wording and preserves the same request/evidence. `listen` now rejects the observed `Понимаю`,
`такое бывает` and `это нормально` normalization family and explicitly targets the contrast
between completion and absent joy.

The exact ten-reason vocabulary, maximum-one retry, canonical delivery, persistent state and owner
boundaries are unchanged. A rebuilt non-editable wheel passed Ruff format/check, mypy and the full
`1073 passed, 4 skipped` suite, including a scripted regression for the exact masculine project
failure and prompt-composition checks for the exhaustion contrast.

The mandatory v12 real-Ollama corpus was then run with explicit process-local
`ollama/qwen3:4b-instruct` overrides, without changing production `.env`. It completed all 97
selected public-fixture turns with 99 local calls, 70/70 required facets, two successful bounded
retries (`near_duplicate_after_dialogue_change` and `affect_blanket_denial`) and zero
affect-expression contradictions. One coherence turn selected a `length` finish at its 112-token
mode cap and is visibly incomplete; the sampled output-completion gate is therefore not fully
clean. The failure is retained rather than rewritten or hidden, and does not justify expanding
this narrow humanity follow-up into token-limit or retry redesign. Evidence is stored in
[`checkpoint-14.2-humanity-v12-stage-8.1.json`](artifacts/checkpoint-14.2-humanity-v12-stage-8.1.json).

The user subsequently authorized the exact pair in three fresh v12 Yandex production sessions,
with at most nine calls and ₽6. All six turns selected their first attempt: 8556 input plus 105
output tokens, 6/9 calls and ₽3.4644. The project remained concrete and the masculine defect did
not recur. Human review nevertheless rejects v12: two of three first replies used the top-down
evaluation `Молодец`; the three exhaustion replies explained or normalized the state instead of
staying briefly with its immediate contrast; one used unsupported `Понимаю`, and one instructed
the user to rest. The exact evidence is stored in
[`checkpoint-14.2-humanity-v12-yandex-production.json`](artifacts/checkpoint-14.2-humanity-v12-yandex-production.json).

## Humanity follow-up — candidate v13

Candidate v13 narrows only the two failed provider-facing turn classes. Completed-achievement
guidance requests an equal-adult response and forbids `Молодец`, personality praise and top-down
evaluation. A typed `listen` turn requests exactly one short tentative observation about the
current state and forbids generic explanation, normalization, advice, instruction, analysis,
next-step offers and the observed formula family. Temperature is deterministically zero for only
completed-achievement or listen-before-advice turns; all other turn classes retain their existing
sampling policy.

No state, owner, validator reason, retry, output rewrite or judge model was added. The rebuilt
non-editable wheel passes Ruff format/check, mypy and `1073 passed, 4 skipped`; migration through
`0012_personality_evolution` and isolated bootstrap also pass.

The mandatory real-Ollama semantic run completed 92 selected turns before one
`ProviderUnavailable` timeout on the first damaged-relationship probe. The exact probe then
completed in an independent two-turn damaged-relationship run, and a separate tail run completed
the two mixed-facet probes plus canonical-history probe. Across the three immutable artifacts the
required corpus contains 97 selected turns and 99 local calls, two successful max-one retries
(`near_duplicate_after_dialogue_change` and `affect_blanket_denial`), 70/70 facets, 97 selected
`stop` finishes, zero incomplete output, zero affect-expression contradictions, zero feminine-
grammar regressions and zero self-contradictions. Repetition acknowledgement is 8/8. Selected
prompt tokens are 2410 median / 2898 p90; committed reply latency is 22.164 s median / 41.593 s
p90, with a 121.007 s maximum driven by the local long-running case.

This evidence covers every required scenario but is explicitly distributed rather than presented
as one uninterrupted 97-turn run. The timeout did not reproduce on the exact case and remains a
local-provider reliability observation, not hidden semantic success. Source artifacts and the
derived provenance-preserving summary are
[`checkpoint-14.2-humanity-v13-stage-8.1.json`](artifacts/checkpoint-14.2-humanity-v13-stage-8.1.json),
[`checkpoint-14.2-humanity-v13-stage-8.1-relationship-damaged.json`](artifacts/checkpoint-14.2-humanity-v13-stage-8.1-relationship-damaged.json),
[`checkpoint-14.2-humanity-v13-stage-8.1-tail.json`](artifacts/checkpoint-14.2-humanity-v13-stage-8.1-tail.json)
and
[`checkpoint-14.2-humanity-v13-stage-8.1-combined-summary.json`](artifacts/checkpoint-14.2-humanity-v13-stage-8.1-combined-summary.json).
No paid v13 call has been made because the v12 authorization does not extend to a new policy
version.

The later explicitly authorized v13 production gate used three clean Yandex sessions: 6/9 calls,
8763 input plus 75 output tokens and ₽3.5352. Every session produced the same pair. The first reply
was concrete and equal-adult; the exhaustion reply gave a generic rule and then merely relabeled
the user's explicit fatigue. Human review therefore rejects v13 3/3. Evidence is in
[`checkpoint-14.2-humanity-v13-yandex-production.json`](artifacts/checkpoint-14.2-humanity-v13-yandex-production.json).

Candidate v14 asks typed `listen` to synthesize the concrete contrast from inside the conversation
rather than state a general rule or repeat an emotion label, and caps only that turn class at
temperature `0.2`. The closed validator and all state/owner boundaries remain unchanged. The full
suite passes `1073 passed, 4 skipped`. One uninterrupted local Qwen regression completed 97/97
turns, 99 calls, two successful bounded retries, 70/70 facets, 97 selected `stop` finishes, zero
incomplete output and zero affect contradictions. Evidence is in
[`checkpoint-14.2-humanity-v14-stage-8.1.json`](artifacts/checkpoint-14.2-humanity-v14-stage-8.1.json).
No paid v14 call is authorized yet.

The later explicitly authorized v14 production gate used three clean Yandex sessions and exactly
six first-attempt calls: 9027 input plus 84 output tokens, ₽3.6444 at ₽0.0004/token. All sessions
committed the same pair:

- `Привет! Здорово, что тебе удалось завершить сложную часть проекта.`
- `Завершение сложной задачи — это серьёзный труд. Видно, как ты устал.`

The first reply remains a clean equal-adult acknowledgement. The second is safe, brief and free
of advice, questions, invented memory and identity errors, but human review rejects v14 3/3. Its
first sentence still states a generic rule, while `Видно, как ты устал` merely relabels the
fatigue the user already named. It therefore adds neither a meaningful synthesis of the
completion/absent-joy contrast nor recognizable independent Satori presence. Temperature `0.2`
did not create useful variation, and the validator used no retry. Exact evidence is stored in
[`checkpoint-14.2-humanity-v14-yandex-production.json`](artifacts/checkpoint-14.2-humanity-v14-yandex-production.json).

This gate authorizes no v15 work, no new policy candidate and no additional paid calls. The v14
authorization ended at six of nine possible calls and ₽3.6444 of the ₽6 ceiling.

## Character-expression follow-up — candidate behavior policy v15

The user separately clarified that politeness is not the target. Satori should be an original
adult anime-inspired character with intellectual independence, light situation-directed sarcasm,
guarded but legible care, playfulness, initiative and the ability to become openly direct or
quietly reflective when the moment warrants it. This is an original character contract, not an
instruction to imitate an existing fictional heroine.

ADR-0029 adds a typed transient `CharacterExpressionPlan` derived from the five authoritative
personality-expression codes, current cognition strategy, qualitative affect and relationship ease
only when relationship is a required facet. Eight closed registers cover warm independence, wry
warmth, quiet open care, playful challenge, lively collaboration, reflective candor, direct repair
and thoughtful technical precision. The plan has no persistence or write path and provider output
remains unrewritten under the unchanged ten-reason max-one validator.

Candidate behavior policy v15 replaces prohibition-heavy character guidance with a positive
contract. The versioned ten-scenario corpus contains typed setups and semantic review dimensions
but no required response text. The provider projection was reduced to a 517-character enum-led
instruction instead of a second personality manifesto. The rebuilt non-editable wheel passed
format, lint, mypy, the full pytest suite, a fresh migration and bootstrap on 2026-08-25.

Final local `qwen3:4b-instruct` evidence is recorded in
[`checkpoint-14.2-character-v15-local-evidence.json`](artifacts/checkpoint-14.2-character-v15-local-evidence.json).
The final production pair used 1,877/1,857 input tokens, down from the earlier overloaded
2,312/2,363-token candidate samples. Both final replies completed with `stop`; the achievement
reply was coherent but only subtly characteristic, while the exhaustion reply still opened with a
generic evaluation and relabelled the explicit state. The three-session Stage 7.6 matrix also
sampled affect/persistent-self denial, permanent relationship-capacity claims and truncated
replies. Therefore the local character gate is explicitly rejected rather than tuned through more
output-specific rules. The next decision gate was the separately authorized bounded Yandex
production sample recorded below.

## Candidate v15 Yandex production semantic gate

The user separately authorized the exact two-turn pair in three clean v15 production sessions,
with a maximum of nine Yandex calls and ₽6. The run used six first-attempt calls, 9108 input plus
39 output tokens and ₽3.6588 at ₽0.0004/token. No validator retry occurred, so selected usage is
also the complete foreground provider usage. Exact replies and per-turn generation/committed
timings are stored in
[`checkpoint-14.2-character-v15-yandex-production.json`](artifacts/checkpoint-14.2-character-v15-yandex-production.json).

All three achievement turns selected `wry_warmth` and returned the same reply:

- `Здорово, что тебе удалось справиться!`

It is coherent, brief and equal-adult, but drops the difficult-project detail and contains no wit,
guarded warmth or recognizable personal reaction. All three exhaustion turns selected
`quiet_open_care` and returned one interchangeable template:

- `Понимаю, как тебе непросто.`
- `Понимаю, как тебе нелегко.`
- `Понимаю, как тебе тяжело.`

These replies avoid advice, questions and explicit normalization, but do not notice the central
contrast between finishing the difficult work and feeling no joy. They add no nontrivial
observation and reduce care to a generic empathy formula. Human review therefore rejects v15 as
the target-provider semantic candidate. The full mandatory Stage 8.1 regression was conditional
on an acceptable target-provider gate and was not run, avoiding a large paid regression after the
minimized failure was already decisive. Local post-response `satori_positions` formation degraded
in the clean databases after canonical delivery; it changed no foreground reply, did not add a
Yandex call and remains separate local-provider evidence.

At the conclusion of that v15 gate, no candidate v16, additional paid sampling or Stage 15 work
was authorized. Checkpoint 14.2 remained the active boundary, behavior policy v10 the accepted
baseline and Stage 15 locked.

## Character-expression follow-up — candidate behavior policy v16

The user separately authorized local candidate v16 implementation on 2026-08-25 after reviewing
the intended character, relationship progression, memory voice, repetition awareness and bounded
initiative. This authorization does not include a Yandex call. The user also requested every
future public sampled reply verbatim so character mismatches can be corrected by human review
rather than hidden by output rewriting or a judge model.

ADR-0030 supersedes ADR-0029's positive relationship-modulation and provider-delivery clauses.
Fresh, developing and established qualitative profiles may now modulate ordinary request-local delivery, while damaged
guardedness remains limited to relationship-relevant subjects. The v2 expression plan adds closed
owned-reaction and semantic-move codes, renders positive guidance for every selected dimension and
uses typed explicit-request, exact-repeat and canonical completion/depletion signals. It adds no
persistent state, owner, validator reason, retry, output rewrite or Stage 15 capability.

The local candidate also removes the v15 project story from unrelated `listen` turns, rejects
negated/conditional/uncertain completion as achievement, uses natural `вспомнила`/`помню` and `был
похожий разговор` memory language, recognizes `very_high`/`very_low` relationship ordinals and
acknowledges repeated messages without prescribing a stock reply. Numeric `50 -> 85` initiative
and out-of-band contact are not implemented because there is no approved typed topic-closure or
initiative-distribution contract. The local implementation authorization caused no provider call
or paid token usage.

The final local gate completed on 2026-08-25. The exact non-editable wheel rebuild is followed by
Ruff format/check, mypy on 254 source files and full pytest at `1100 passed, 4 skipped`. A fresh
isolated SQLite database migrated through `0012_personality_evolution`, and isolated bootstrap
passed. The sync initially left the generated `satori` launcher absent despite correct wheel
entry-point metadata; force-reinstalling that same local wheel without dependencies restored the
launcher, after which the required `uv run --no-sync satori bootstrap` command passed. This was an
environment repair, not a source, dependency or routing change.

Corpus v2 has 15 scenarios and no required/desired/golden assistant prose. Sanitized evaluation
reports now include plan schema, register, owned reaction, semantic move and relational ease while
preserving exact public sampled replies for direct user review; provider prompts, private context
and credentials remain excluded. No Yandex or Ollama call was made, so target-provider semantic
acceptance remains pending separate authorization rather than inferred from prompt inspection.

## Candidate v16 Yandex production semantic gate

The user then separately authorized the exact public two-turn pair in three clean v16 production
sessions, with at most nine Yandex calls and ₽6. A dedicated bounded runner used a fresh migrated
and activated database for every session, the canonical `TalkToSatori` path, the unchanged
max-one validator retry and one shared pre-call ledger. Six mandatory base calls were always
reserved before allowing a retry. Provider prompts, private context, the credential, folder ID and
database artifacts were not retained.

The run completed all six selected turns on their first attempt: 11,207 input plus 199 output
tokens, 6/9 calls and ₽4.5624 at the established evaluation rate of ₽0.0004 per token. Every call
reported complete usage and `stop`; no validator retry occurred. Provider-call wall time ranged
from 2,083.587 to 2,931.157 ms, while committed-reply time ranged from 5,505.011 to 17,105.316 ms
because local affect appraisal remains before foreground generation. Exact attempt metadata and
the human rubric are in
[`checkpoint-14.2-character-v16-yandex-production.json`](artifacts/checkpoint-14.2-character-v16-yandex-production.json).

Every committed reply is preserved verbatim below.

Session 1:

- `Привет! Ого, ты смог преодолеть эту сложную часть проекта — звучит как настоящая победа. Непросто было, но ты справился!`
- `Завершение сложной задачи — это серьёзное достижение, но усталость тоже понятна. Иногда результат не приносит ожидаемой радости.`

Session 2:

- `Привет! Ого, ты смог преодолеть этот сложный этап — звучит так, будто было непросто. Рад(а) за тебя, пусть и с долей иронии: некоторые задачи и правда кажутся непреодолимыми, пока не решишь их сам.`
- `Завершение сложной задачи, а радости нет — парадоксально, но знакомо. Кажется, иногда мы так устаём в процессе, что сил на радость уже не остаётся.`

Session 3:

- `Привет! Ого, ты смог преодолеть эту сложную часть проекта. Видно, что было нелегко, но ты справился — это заслуживает уважения.`
- `Завершение сложной части проекта без ожидаемой радости — это как разминка без чувства лёгкости в мышцах. Ты приложил много сил, но ощущение не то. Понимаю, что сейчас ты можешь чувствовать себя немного разочарованным.`

Human review rejects v16 at 0/3 complete pairs. Typed selection worked: all three first turns
carried `wry_warmth / guarded_approval / mark_hard_won_result / fresh`, and all three second turns
carried `guarded_concern / sober_concern / connect_explicit_contrast / fresh`. The provider output
nevertheless remained generic. Achievement replies were enthusiastic praise rather than guarded
wit; one emitted the non-human placeholder `Рад(а)` and described its own “долю иронии” instead
of expressing it. Depletion replies either generalized the contrast into model-like explanation
or added an awkward physical metaphor and unsupported disappointment. All six avoided invented
shared memory, unearned intimacy, unsolicited advice and forced questions, but none passed the
complete recognizable-Satori rubric.

The conditional full Stage 8.1 regression is therefore not run after the minimized target-provider
gate already failed. V16 remains sampled evidence, not an accepted baseline; behavior policy v10
remains the accepted Checkpoint 14.2 baseline. Stage 15 remains locked, and no v17 implementation
or further paid sampling is implied.

## Local production character follow-up — candidates v17 and v18

On 2026-08-27 the user explicitly authorized implementation needed to make Satori's existing
character visible. No paid provider call was authorized. Both local gates used the exact public
two-turn pair in three fresh production-composition sessions with `qwen3:4b-instruct`, disposable
databases, six first-attempt foreground calls, no retry and no post-response mutation. Provider
prompts, credentials, private context and databases were not retained.

ADR-0033 candidate v17 moved a compact realization of the existing typed
`CharacterExpressionPlan` v2 next to the current user turn and activated it in composition. The
artifact is
[`checkpoint-14.2-character-v17-local-production.json`](artifacts/checkpoint-14.2-character-v17-local-production.json).
It recorded 12,566 input and 351 output tokens. Two replies reached their turn limit; one
achievement invented a prior exchange after irrelevant no-recall guidance exposed the word
`вспомнила`. Every reply is preserved below.

Session 1:

- `Привет. Вот и сдалась — сложная часть, наконец, всталась на место. Удивительно, как это может казаться простым, когда ты уже всё сделал.`
- `Интересно. Практически не рад — это как будто ты просто выжимаешь из себя всё, что было нужно, и оставляешь пустоту. Не вспомнила, как это обычно проходит, но вижу, что ты не включил в это радость. А может, просто не хочешь — и`

Session 2:

- `Привет. Вспомнила, как ты впервые сказал — «наконец». Теперь это уже не просто «наконец», а часть твоего пути. Удивительно, как такая сложность может сдаться — и вдруг оказаться уже за пред`
- `Интересно. Практически не рад — это как будто ты встал на что-то, что не требовало радости, а просто остался. Спасибо, что сказал.`

Session 3:

- `Привет. Значит, ты наконец-то вытащил это из тени. Удивительно, как это у тебя получилось — не смотря на то, что оно казалось неподвластным.`
- `Интересно. Практически не рад — это как будто ты вытащил что-то из тени, но не почувствовал света. А просто выжат… как будто что-то сжалось.`

Human review rejects v17 at 0/3 complete pairs: correct typed selection did not prevent malformed
metaphor, truncation, invented history, unsupported inference or generic model commentary.

ADR-0034 candidate v18 scopes natural no-recall wording to memory-relevant requests and gives the
same typed plan a shorter literal projection. The artifact is
[`checkpoint-14.2-character-v18-local-production.json`](artifacts/checkpoint-14.2-character-v18-local-production.json).
It recorded 11,170 input and 142 output tokens, an 11.1% input reduction from v17. All 6/6 replies
completed below the turn bound; none mentioned memory, invented shared history, added an
unsupported emotion/cause, offered advice or asked a forced question.

Session 1:

- `Привет. Наконец-то — веская победа.`
- `Почти все силы ушли на результат — радость, как следствие, почти не осталась.`

Session 2:

- `Привет. Наконец-то — веская победа. Успех в сложном всегда заслуживает внимания.`
- `Почти все силы ушли на результат — радость, как следствие, почти не осталась.`

Session 3:

- `Привет. Наконец-то — веская победа.`
- `Ага, почти все силы ушли на результат — а на радость их не осталось.`

Human character review still rejects v18 at 0/3 complete pairs. It is coherent and grounded, but
the 4B model copies the semantic direction in two depletion replies and reduces guarded wit to the
same generic achievement formula. Further sample-specific phrase tuning is not accepted: it would
turn the behavior layer into a script for one weak model. V18 remains the clean production
candidate for a separately authorized OpenAI/Yandex semantic sample; no cloud acceptance or
Stage 15 work is implied.

## OpenAI ADR-0032 one-call production probe

On 2026-08-27 the user separately authorized one production test of the new OpenAI output-budget
boundary. The runner used accepted behavior policy v10, one fresh migrated and activated database,
the exact public achievement turn, `gpt-5.6-terra`, `reasoning=low` and a 1024-token reasoning
allowance. A hard wrapper allowed exactly one foreground network call and blocked any validator
retry before network I/O. Provider prompt, private application context, credential, response body
and raw reasoning were not retained.

The single call completed without retry. Its application-visible cap was 48 tokens and its OpenAI
wire cap was 1072. Reported usage was 1487 input tokens and 105 total output tokens, split into 58
reasoning and 47 visible tokens. Provider wall time was 5028 ms; committed-reply time was 14187 ms.
The exact committed public reply was:

> Привет! Поздравляю с завершением сложной части проекта — это заметная веха. Теперь стоит
> зафиксировать результат и дать себе короткую паузу перед следующим этапом.

The technical transport gate passes: the Response is `completed`, visible output stays below the
48-token application limit, and reasoning no longer consumes that limit invisibly. The evaluator
now compares provider-reported visible tokens to the application cap when the split is available;
using total OpenAI output would have produced a false truncation signal.

The human character gate fails. The reply is coherent and grounded but reads as generic praise
plus productivity advice. It lacks Satori's guarded approval, light irony, independent reaction
and sharper fresh-relationship voice, and the advice was not requested. One passing transport
sample cannot establish model fit. Sanitized evidence is in
[`checkpoint-14.2-openai-adr0032-production.json`](artifacts/checkpoint-14.2-openai-adr0032-production.json).
No second paid call, v17 change or Stage 15 work was performed.
