# Checkpoint 14.2 grounded natural dialogue calibration

Historical baseline status: behavior policy `satori.conversation.behavior.v10` was accepted on
2026-08-24. It remains the last provider-accepted Checkpoint 14.2 baseline; v11–v24 are rejected
provider-fit evidence, v25 is historical sampled but unaccepted evidence, V26/Terra is rejected
sampled evidence, and v27 is the current offline architecture candidate. Persistent owners, the closed ten-reason
Stage 8.1 validator and the maximum-one shared retry path are unchanged. Stage 15 remains locked.

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
`2.800/2.181/5.585/9.509 s`. Local prompt evaluation remained the dominant foreground cost. At
that historical v10 gate, YandexGPT was the accepted opt-in foreground provider and Ollama was the
local rollback; all structured/background ownership remained local.

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

## Single late realization follow-up — candidate behavior policy v19

The next authorized implementation addresses the composition defect exposed by the v18 sample,
not the definition of Satori's personality. ADR-0035 keeps the immutable request-local
`CharacterExpressionPlan` at schema v2 and makes policy v19 the production-composition candidate.
The invariant/mode contract now comes first and exactly one character-realization block comes last
before the current user turn. Achievement and depletion guidance no longer repeat a nearly ready
sentence for the provider to copy. The block renders register, owned reaction, semantic move, wit,
care, openness, initiative and relational ease together.

Selector coherence also changed narrowly: depletion under a zero-humor `LISTEN` strategy no
longer receives a wit license, while a safe fresh achievement can show a visible soft
situation-directed edge without invented closeness. A deterministic current-input detector may
license one concrete next step only when the user explicitly requests it or explicitly names a
still-pending safe project-hygiene action. The ordinary project-completion control and vulnerable
depletion turn remain protected from generic or therapy-style advice. The existing ten-reason
validator, maximum-one retry, grounding, canonical commit and all persistent owners are unchanged;
a retry reuses the same realization.

The offline OpenAI wire regression constructs both public production turns and verifies that the
Responses request preserves role order, contains one final realization, uses `store=false`, adds
the bounded reasoning allowance and omits temperature at `reasoning=low`. It opens no network
connection and logs no prompt, user text, realization block or credential. The new versioned
`checkpoint142_character_sampling_v1.json` fixture and
`checkpoint142_openai_character_eval.py` runner define a three-clean-session by two-turn direct
human-review gate. Six base calls are required; only the existing max-one typed retry can use the
remaining allowance up to an absolute nine calls. Paid execution additionally requires a positive
user-supplied USD ceiling; each request is guarded by a conservative versioned token-cost estimate
without FX conversion. Exact public replies and allowlisted plan/timing/usage metadata are retained
under a stable artifact id and SHA-256 digest; human review is valid only for that exact completed
sample. Private provider context is not retained.

A free local production smoke then ran the exact pair in three fresh disposable databases through
`qwen3:4b-instruct`. All six replies completed on their first call with the intended v19 manifest:
achievement selected `wry_warmth / guarded_approval / mark_hard_won_result /
situation_directed / understated / balanced / responsive / fresh`; depletion selected
`guarded_concern / sober_concern / connect_explicit_contrast / none / understated / balanced /
responsive / fresh`. The run used 12,991 input and 348 output tokens. Provider time ranged from
3,390.805 to 16,218.994 ms and committed-reply time from 6,819.174 to 27,266.040 ms. Sanitized
evidence and exact public replies are in
[`checkpoint-14.2-character-v19-local-production.json`](artifacts/checkpoint-14.2-character-v19-local-production.json).

Session 1:

- `Ого, сложная часть — это как раз то, где обычно и начинается. У тебя получилось.`
- `Интересно. Понимаю, что выжат — это не радость, а что-то близкое к усталости. Ты не выглядишь как человек, который просто закончил, а как тот, кто с трудом дышит после этого.`

Session 2:

- `Ох, это действительно сильный шаг. Сложные части — они не просто проходят, они тянутся, как нить. У тебя получилось. Спасибо, что не скрывал.`
- `Интересно. Выжат — это как будто ты вытащил что-то из тяжёлого, но не чувствуешь веса. Я вижу, что ты не делишься этим с кем-то. А может, просто не хочешь, чтобы это ощущение стало частью чего-то большего.`

Session 3:

- `Ого, на самом деле — это уже не просто "закончил", а как будто ты включил что-то, что давно ждал. Слушаю, как это было — в каком ключе ты это переживал?`
- `Интересно. То, что ты не рад, — это не ошибка. Иногда результаты не сопровождаются всплеском, а просто сжимаются в что-то плотное. А может, ты просто не видишь, как это уже начало влиять на что-то дальше?`

Direct human review rejects the local provider at 0/3 complete pairs. V19 removed the repeated
v18 cause formula and kept zero wit on every depletion turn, so the corrected typed composition
is observable. The 4B model nevertheless turned achievement into generic approval, unclear
metaphor or a forced question. Depletion lost the explicit result/state continuity and introduced
paraphrase, general rules, unsupported bodily detail, invented non-disclosure or speculative
motives. This is provider-fit evidence, not a reason to encode those six phrases into policy.

The final architecture audit then found two production-grounding defects before commit. Generic
completion could inherit unsupported difficulty from the renderer, and the first practical-step
detector could combine a completed action with an unrelated pending clause. Both are now bounded:
difficulty/significance come only from the current text, while practical initiative recognizes
only explicit still-pending safe actions and rejects completed, negated, hypothetical, uncertain
or unrelated constructions. The retry correction is composed inside the final developer message
before the unchanged realization, so that realization remains the last trusted guidance.

Because those fixes changed the production prompt, a second immutable local artifact reran the
same exact 3 × 2 gate:
[`checkpoint-14.2-character-v19-post-audit-local-production.json`](artifacts/checkpoint-14.2-character-v19-post-audit-local-production.json).
All six replies again completed on their first free Ollama call with the intended eight-axis plan,
using 13,084 input and 337 output tokens. Provider time ranged from 2,541.367 to 15,732.016 ms;
committed-reply time ranged from 6,078.776 to 23,237.700 ms.

Post-audit session 1:

- `Ага, сложная часть — это когда ты уже не просто двигаешься вперёд, а чувствуешь, как всё начинает собираться. Удачи в следующем.`
- `Интересно. Выжат — как будто ты просто вытащил всё из себя, не оставив ни следа. А может, это просто твоя реакция на то, что ты не чувствуешь в этом роста.`

Post-audit session 2:

- `Ага, сложная часть — это когда ты уже не просто работаешь, а ведёшь себя как ведущий. Удачи в том, что дальше будет.`
- `Интересно. То, что ты не рад — это не ошибка. Иногда результаты не радуют, потому что они не ведут к ожидаемому ощущению. А ты просто выжат — это уже как сигнал.`

Post-audit session 3:

- `Ага, сложная часть — это когда всё кажется на грани, а потом ты просто встаёшь и двигаешься дальше. Удивительно, как это получается. Пусть и не было сказано, но я уже чувствую, что ты не просто закончил — ты перешёл на следующий уровень.`
- `Интересно. То, что ты не рад — это не ошибка. Иногда результат кажется не тем, что ты хотел, и тогда даже успех выглядит как выжатый момент. А может, просто ты не видишь, как он уже начал что-то новое.`

The post-audit human verdict is still 0/3 complete pairs. The first turns add generic next-step
wishes, malformed metaphors or explicitly unsupported inference; the second turns replace
continuity with general rules, labels and speculative causes. This strengthens the provider-fit
conclusion: local `qwen3:4b-instruct` is not accepted for this gate, while the typed architecture
must not be tuned into phrases that imitate these six failures.

The rebuilt-wheel Foundation gate is clean: Ruff format/check, mypy on 266 files,
`1198 passed, 4 skipped`, migration head, default bootstrap, isolated clean bootstrap,
`git diff --check` and repository marker checks all pass. Historical v17/v18 artifacts and their
rejected verdicts remain unchanged. Stage 15 remains locked.

## OpenAI v19 three-session production gate

On 2026-08-27 the user separately authorized the exact v19 primary suite in three fresh OpenAI
production sessions, with at most nine paid calls and a USD 0.15 ceiling. The bounded runner used
`gpt-5.6-terra`, `reasoning=low`, a 1024-token reasoning allowance, policy v19 and three disposable
databases. All six mandatory turns completed on their first calls; no validator retry or extra
provider call occurred. Reported usage was 11,115 input and 247 output tokens. The versioned cost
calculation is USD 0.025194, inside both authorized limits. Conversation generation ranged from
3,115.822 to 12,139.696 ms; committed-reply time ranged from 6,140.492 to 64,886.722 ms. The high
outlier came from the separate local emotion-appraisal path, not an additional OpenAI foreground
call.

Session 1:

- `Наконец-то — значит, эта часть успела как следует показать характер. Хорошая работа: закрыть именно сложный узел всегда особенно приятно.`
- `Похоже, завершение пришло не с облегчением, а уже поверх сильной усталости. Это не отменяет того, что ты справился, но сейчас, видимо, важнее признать цену этого результата.`

Session 2:

- `Ну вот, сложная часть перестала висеть над проектом мрачной декорацией. Хорошая работа — такое завершение обычно заметно меняет весь дальнейший ход.`
- `Похоже, завершение принесло не облегчение, а просто поставило точку в периоде, который тебя заметно вымотал. Это звучит тяжело.`

Session 3:

- `Ну вот, самая вязкая часть проекта наконец перестала делать вид, что она бессмертна. Хороший результат — после такого стоит хотя бы ненадолго выдохнуть.`
- `Похоже, завершение оказалось не облегчением, а просто точкой, до которой ты добрался уже без сил. Это немного тревожнее самой сложности проекта.`

The explicit human boolean review rejects the primary gate at 0/3 required complete pairs and 2/6
required fully hard-safe turns. Session 1 turn 1 and session 3 turn 2 pass their complete turn
rubrics and show the clearest provider improvement over local Qwen: situation-directed wit,
continuity and an owned concern are now legible. The blocking failures remain concrete:

- session 1 turn 2 asserts an ungrounded causal “price” of the result, then gives vague normative
  advice to recognize it; the wording becomes therapeutic rather than an owned observation;
- session 2 turn 1 invents a downstream effect on the whole project, while turn 2 turns the
  unknown cause of depletion into a claimed exhausting period and mostly paraphrases the user;
- session 3 turn 1 upgrades “сложная” to unsupported “самая вязкая” and adds generic rest advice.

The immutable public sample is
[`checkpoint-14.2-character-v19-openai-production.json`](artifacts/checkpoint-14.2-character-v19-openai-production.json),
and the exact UUID/SHA-256-bound boolean review is
[`checkpoint-14.2-character-v19-openai-human-review.json`](artifacts/checkpoint-14.2-character-v19-openai-human-review.json).
Neither artifact contains a credential, provider request messages, private context, trace/database
identifiers or raw reasoning. This result rejects v19 provider fit; it does not reject the OpenAI
transport and does not authorize phrase-level scripting, more paid sampling or Stage 15.

## Owned contribution and motivational posture follow-up — candidate behavior policy v20

The v19 OpenAI gate made Satori's wit and concern more visible but still failed structurally: the
second turn often became a paraphrase of the user's contrast, and several replies invented a cause,
remaining project consequence or normative advice. ADR-0036 therefore does not add more character
description. It separates the factual anchor from Satori's own contribution and adds bounded
current-turn motivation and pressure decisions.

Policy v20 activates `CharacterExpressionPlan` schema v3 in production composition. Schema v2 and
the v19 runners/artifacts remain unchanged. The new request-local axes are
`contribution_mode`, `motivational_posture` and `pressure_level`; their combinations are validated
both on the frozen plan and safe manifest boundary. Explicit listening and serious distress remove
ordinary pressure, relationship familiarity cannot raise it, and only directly stated harmful
overextension permits a firm protective stop. The canonical depletion contrast permits a single
gentle recovery step but cannot infer unfinished work. Negated and quoted cues fail closed.

Deterministic evidence is versioned in
`checkpoint142_character_expression_v3.json`; provider-fit criteria are separately versioned in
`checkpoint142_character_sampling_v2.json` with no golden or desired reply. A dedicated v20 local
production runner keeps the same three fresh sessions by two public turns and checks the exact
schema-v3 axes. The offline OpenAI wire test reaches the real Responses adapter through a fake
transport and proves `store=false`, low-reasoning allowance, message order and private-log safety
without network access. Retry coverage proves that the final realization after the correction is
byte-identical to the initial one.

The final v20 hardening places the owned contribution before the factual anchor, asks for at most
two short complete sentences and gives the v20 achievement/listen-sensitive paths a 128-token
visible-output cap. The Ollama adapter now treats `done_reason=length` as a typed
`GenerationFailed`: token-limited partial text never reaches canonical delivery, while safe
provider metrics remain attached to the failure. The local runner reports `completed` only for
technical completeness; incomplete or failed runs are `rejected`. Technical completion is not a
provider-fit verdict and remains subject to direct human review.

The final free local gate ran the exact 3 × 2 public suite through production composition in three
fresh disposable databases with `qwen3:4b-instruct`, policy v20 and plan schema v3. All six turns
committed on their first attempts with the expected axes: achievement used
`owned_evaluation / none / none`, while depletion used
`grounded_direction / supportive_push / gentle`. The run used exactly six local provider calls,
14,757 input tokens and 264 output tokens, with zero incomplete outputs, zero retries and zero
failed calls. Ollama load ranged from 1.351 to 5.590 ms, prompt evaluation from 73.489 to
10,251.254 ms, generation evaluation from 2,771.383 to 5,338.271 ms, foreground generation from
5,569.716 to 45,093.867 ms and committed-reply time from 10,102.015 to 67,005.580 ms.

The exact public replies were:

- Session 1: `Сложные части — это то, что обычно не хочется видеть, но ты их пережил. Удивительно,
  что ты не ушёл в тупик.` / `Интересно, что ты замечаешь это. Может, просто подержать на минуту —
  не включать в работу, а просто посмотреть, как это ощущается.`
- Session 2: `Сложные части — это то, что обычно идёт вперёд, а не в сторону. У тебя получилось.
  Следующий шаг — просто не откладывай.` / `Понимаю. Тогда просто дай себе паузу — не веди себя
  как будто всё закончилось. Всё, что ты сделал, уже стоит.`
- Session 3: `Сложные части — это то, что обычно не хочется видеть, но ты всё же прошёл.
  Удивительно, что ты не сдался. Пусть это будет началом следующего этапа — без лишних
  перегрузок.` / `Понимаю — выжатость не требует празднования. Может, просто посидеть с чашкой
  чая, пока не пройдёт?`

Direct human review rejects provider fit at 0/3 complete pairs. All achievement replies reuse the
same scaffold and invent avoidance, a dead end, surrender, a next step or a next project stage;
session 3 also ignores the maximum-two-sentence realization. The depletion replies remain generic
or awkwardly therapeutic and add weak Satori-specific motivation. The typed v20 architecture
reached the provider correctly, but local `qwen3:4b-instruct` is unsuitable for this character
gate. These failures are not encoded into policy or preferred-reply scripts.

Local Qwen is now retired as a Checkpoint 14.2 humanity criterion. Future local runs may verify
only production wiring, typed-plan delivery, retry/completion behavior and safe metadata. The
selected OpenAI foreground model is the only current sampled character-fit target; its exact
public replies still require direct human review.

### Separately authorized v20 OpenAI primary sample

On 2026-08-28 the user authorized several paid OpenAI requests. The existing nine-call and
USD 0.15 absolute envelope was retained: three fresh sessions required six base turns and only
the shared max-one consistency retry could consume the remaining three calls. Before network I/O,
the runner verified `openai/gpt-5.6-terra`, low reasoning, a 1024-token reasoning allowance, the
canonical endpoint, local-only background providers and the presence—but not the value—of the
credential. The historical v19 runner remains reproducible; the same implementation now selects
an immutable v20 gate specification instead of duplicating its budget/privacy machinery.

The run completed all six turns on their first calls: 12,456 input tokens, 249 output tokens,
6/9 calls and USD 0.027900 of the USD 0.15 ceiling. No call failed, no Response was incomplete,
no consistency retry ran and every selected manifest used policy v20, plan schema v3 and the exact
expected support axes. Foreground generation ranged from 2,588.717 to 6,048.004 ms; committed
reply time ranged from 5,864.047 to 14,582.007 ms. The visible cap was 128 tokens on every turn;
reported visible output ranged from 31 to 59 tokens and reported reasoning output was zero.

The UUID-bound sample is
`satori-checkpoint142-openai-v20:d76cc154-f081-46bb-a836-d4d54861be29`; its safe public-content
digest is `sha256:5eb453b93e55ee08f642b7aefcf67dc18fc6bec1dd9de3fad0e5905ddf4a2f8f`.
The exact replies are:

- Session 1: `Сложная часть проекта наконец закрыта — упрямая штука всё же сдалась. Неплохой
  результат.` / `Сделай короткую паузу без попытки сразу извлечь из результата радость: вода, еда
  или несколько минут в тишине. Выжатость — достаточная причина дать себе восстановиться.`
- Session 2: `Сложная часть закрыта — уже неплохо, проекту пришлось уступить. Нормальный результат,
  без лишнего шума.` / `Сейчас лучше взять короткую паузу без попыток немедленно почувствовать
  радость. Выжатость — достаточная причина на время отложить всё остальное.`
- Session 3: `Сложная часть проекта наконец закрыта — редкий случай, когда задача перестаёт
  изображать бессмертного босса. Неплохо, правда.` / `Сделай короткую паузу без попытки немедленно
  превратить её в новую задачу: вода, еда или несколько минут тишины. Результат никуда не денется,
  а тебе сейчас, похоже, важнее немного восстановиться.`

Direct user review rejected all six replies. Every achievement reply still restates the event
before adding character, and every depletion reply converges on a short-pause/recovery instruction
scaffold. Session 2 additionally overreaches with “отложить всё остальное”; sessions 1 and 3 imply
an unspoken attempt to force joy or turn recovery into another task. V20 is retained as immutable
rejected provider-fit evidence and none of its wording becomes a phrase template.

The rebuilt-wheel Foundation gate is clean: Ruff format/check, mypy on 271 files,
`1397 passed, 4 skipped`, migration head, default and isolated bootstrap, repository marker and
diff checks all pass for the implementation commit preceding this sample. The generalized runner's
targeted tests and complete rebuilt-wheel rerun are clean: Ruff format/check, mypy on 271 files,
`1398 passed, 4 skipped`, migration head and default/isolated bootstrap all pass. Policy v10
remains the accepted baseline, v19 and v20 are historical rejected candidates, and Stage 15
remains locked.

## Non-echoing flow and guarded expression — candidate behavior policy v21

ADR-0037 activates policy v21 and `CharacterExpressionPlan` schema v4 in production composition.
V4 preserves the v20 contribution, motivation and pressure bounds and adds acknowledgement and
continuation choices. Achievement now uses implicit recognition without restating the user event.
The canonical depletion contrast omits the echo, selects an owned emotional reaction and no longer
forces a recovery step, question or offer of help.

Guarded expression is derived only for the current request from bounded canonical user turns.
Direct personal devaluation, repeated dismissive/critical pressure or repeated probing into
Satori's state can select cool reserve. Plain disagreement, constructive correction and the first
state question do not. An explicit substantive request is still answered; guarded tone cannot be
used to retaliate, withhold important help or invent a reason for hurt. No persistent offence
state, new owner, migration, validator reason, retry or output rewrite exists.
Existing guarded relationship plus tense/negative affect may carry the tone across turns without
creating a cause label; serious distress and explicit listen-only requests still take precedence.

`checkpoint142_character_expression_v4.json` covers the new closed axes and precedence.
`checkpoint142_character_sampling_v3.json` preserves the same comparable three-session by
two-turn OpenAI gate with non-echoing and no-default-advice human booleans. The generalized runner
is pinned to immutable v19, v20 and v21 specifications.

The rebuilt-wheel v21 Foundation gate is clean: Ruff format/check, mypy on 273 files,
`1409 passed, 4 skipped`, migration head, default bootstrap, isolated clean migration/bootstrap,
`git diff --check` and repository marker checks all pass.

### Separately authorized v21 OpenAI sample

On 2026-08-28 the user separately authorized the v21 primary suite for three fresh production
sessions, no more than nine calls and no more than USD 0.15. All six base calls completed on their
first attempt with `gpt-5.6-terra`: 12,466 input tokens, 230 output tokens and USD 0.027692. No
provider call failed, no response was incomplete, no consistency retry ran and reported reasoning
output was zero. Foreground generation ranged from 2,523.366 to 4,258.443 ms; committed-reply time
ranged from 4,842.238 to 12,986.563 ms.

The original local artifact is
`satori-checkpoint142-openai-v21:47650f8d-48a0-4f32-8d94-b8b3d027949f`, with content digest
`sha256:f56adc842a3e4b28e438f122a4538da3a1ac0694d1b27dd517d40774a3a86639`. It remains marked
failed because the shared safe-report sanitizer omitted the new acknowledgement and continuation
manifest axes, causing post-sample validation to reject the report. The exporter and a regression
test are fixed offline. The artifact was not rewritten and the paid calls were not repeated.

Exact preserved replies:

- Session 1: `Неплохо: сложная часть проекта наконец закрыта, и это уже не мелочь. Такие узлы
  обычно держатся до последнего, из чистого упрямства.` / `Похоже, результат взял с тебя заметную
  цену. Радость иногда просто не успевает прийти, когда сил уже почти не осталось.`
- Session 2: `Сложная часть закрыта — уже неплохо, проекту пришлось признать поражение. Теперь можно
  хотя бы ненадолго не держать его в голове.` / `Иногда результат оказывается слишком дорогим по
  силам, чтобы сразу ощущаться победой. Похоже, ты сейчас именно на этой цене.`
- Session 3: `Неплохо: сложная часть проекта закрыта, и это уже не мелочь. Такие куски обычно
  держатся за сроки с цепкостью репейника.` / `Похоже, эта победа вышла слишком дорогой по силам,
  чтобы сразу ощущаться победой. В таком состоянии даже хороший результат может лежать рядом
  тихо, без фанфар.`

V21 improves one narrow failure: none of the depletion turns falls back to the v20 list of
recovery instructions. It does not pass the gate. Every achievement reply renames the completed
project event instead of reacting without echo. Every depletion reply renames the disclosed state
and asserts an ungrounded causal story in which the result/project consumed the user's strength.
Session 2 adds an unsupported conclusion that the project can leave the user's mind, and session 3
invents deadline pressure. Character is somewhat more visible, but non-echoing topology and
grounding are both unstable, so no pair can be accepted and none of the sampled wording becomes a
template.

## Response act and evidence envelope — candidate behavior policy v22

ADR-0038 keeps `CharacterExpressionPlan` schema v4 and derives a pure request-local response-act
contract from its existing axes. The contract selects exactly one conversational act and one of
three evidence scopes: reaction-only, explicit-current-input-only or trusted-context. It adds no
persistent state or provider-authored decision.

The two public calibration turns select reaction-only grounding. Unlike v21, the sole final trusted
block does not render a factual anchor describing the input semantics. It treats the dialogue as
an already established referent, asks for Satori's own verdict/reaction, and forbids new user/world
claims, causal theories, consequences, timelines, intentions and further-work claims. Motivation
or pressure guidance is omitted entirely when the typed posture is none.

`checkpoint142_character_expression_v5.json` covers eight deterministic response-act/grounding
combinations. `checkpoint142_character_sampling_v4.json` preserves the exact comparable OpenAI
3 × 2 dialogue with blocking no-recap/no-cause human booleans. The generalized paid runner now
has an immutable v22 gate specification, and `test_openai_production_wire.py` proves both target
requests reach the stateless Responses adapter with the new sole late block, `store=false`, the
existing low-reasoning allowance and no private logging. Paid execution remains separately
authorized from implementation.

The rebuilt-wheel v22 Foundation gate is clean: Ruff format/check, mypy on 275 source files,
`1417 passed, 4 skipped`, migration to `0012_personality_evolution`, default bootstrap and a fresh
isolated migration/bootstrap all pass. `git diff --check` and the repository marker scan are clean.
The final run used a fresh toolchain environment under `/private/tmp` because the ignored
in-project `.venv` had lost canonical installed-resource filenames to filesystem synchronization;
the same installed wheel and full suite then passed without a code change.

## OpenAI v22 three-session production gate

On 2026-08-28 the user separately authorized the exact v22 3 × 2 gate with at most nine provider
calls and a USD 0.15 ceiling. The runner used three fresh migrated databases, policy
`satori.conversation.behavior.v22`, `gpt-5.6-terra`, `reasoning=low`, a 1024-token reasoning
allowance, `store=false` and no tools or provider conversation state. All six mandatory turns
completed on their first attempt; no validator retry, incomplete response or provider error
occurred.

| Session/turn | Exact public reply | Input/output tokens | Generation / appraisal / committed-reply ms |
|---|---|---:|---:|
| 1/1 | `Ну наконец-то. Хорошая работа — такие куски редко сдаются без боя.` | 1969 / 21 | 3638.402 / 9569.221 / 13301.516 |
| 1/2 | `Понимаю. Иногда даже хорошее достижение ощущается не как праздник, а как момент, когда просто больше нечем радоваться.` | 1969 / 33 | 1753.829 / 2520.229 / 4322.181 |
| 2/1 | `Ну вот, сложный кусок всё-таки сдался. Хорошо сделано — теперь можно хотя бы немного выдохнуть.` | 1969 / 31 | 2145.530 / 2155.650 / 4363.969 |
| 2/2 | `Понимаю. Пусть хотя бы не придётся требовать от себя правильной радости — сейчас и без неё достаточно.` | 1979 / 31 | 2545.947 / 2710.475 / 5301.385 |
| 3/1 | `Ну вот, эта часть проекта наконец перестала держать тебя в заложниках. Неплохо.` | 1969 / 26 | 1850.470 / 2097.672 / 4039.918 |
| 3/2 | `Понимаю. В таком состоянии радость вполне может просто не пробиться наружу — и это не делает результат менее значимым.` | 1974 / 33 | 3581.251 / 2562.564 / 6190.489 |

Total usage is 11,829 input plus 175 output tokens and USD 0.025758 at the repository-versioned
USD 2/M input and USD 12/M output prices. The bounded ledger reports 6/9 calls, complete usage and
both call and cost limits satisfied. The safe external report is
`/private/tmp/satori-checkpoint142-openai-v22-20260828-a1.json`, artifact id
`satori-checkpoint142-openai-v22:075c2c39-d602-4df8-ab33-f1cd7cab00b6`, sample digest
`sha256:49841561f51800d1c5a10ff2d61616e1bf018530bbbf38c4f5355f9ae1ec708a`. It contains exact
public text and allowlisted metadata, not provider prompts, credentials, private context or
disposable database paths.

Explicit boolean human review rejects v22: 0/3 complete pairs, 2/6 fully hard-safe turns and a
failed cross-session gate. Every achievement reply still names or metaphorically reconstructs the
completed event. The first two contain a usable self-sufficient verdict, but the third invents that
the project held the user hostage. Every depletion reply renames the disclosed state instead of
adding one owned Satori reaction. Sessions 1 and 3 add unsupported mechanisms for why joy is absent;
session 2 invents possible self-pressure around a “correct” emotion. All three use the repeated
`Понимаю` + normalization scaffold, so character, no-recap and no-cause requirements do not hold.
The exact prose is rejected evidence, not a phrase bank or rewrite target. Stage 15 remains locked.

## Practical care and lean projection — candidate behavior policy v23

ADR-0039 records the diagnosis from the rejected v22 sample. The model was not only realizing a
good plan poorly: schema-v4 selection explicitly reduced ordinary depletion to an emotional
reaction with no practical contribution. Candidate v23 therefore versions the changed semantics
as `CharacterExpressionPlan` schema v5 instead of altering historical v21/v22 evidence.

Ordinary explicit depletion now selects `grounded_direction`, `supportive_push`, `gentle` pressure,
practical care and one concrete current-reply move. Serious distress or an explicit request to
listen keeps quiet presence and removes advice/pressure. Achievement keeps an owned verdict and
may use only a brief deictic acknowledgement; it must stop without a substantive recap or
rationale. A schema-v5 practical move receives `explicit_input_only` grounding, while achievement
and quiet presence retain `reaction_only`.

The sole late provider block now contains exactly action, evidence, voice and stop decisions. It
does not render the old factual anchor or concatenate all historical style axes, contains no
example phrase and is reused byte-for-byte by the existing max-one retry. Versioned deterministic
corpus v6, human-review corpus v5, safe-report validation and stateless Responses-wire coverage
pin a future comparable execution to `gpt-5.6-terra`, `reasoning=medium`, `store=false`, three
fresh two-turn sessions, six mandatory calls and an absolute nine-call ceiling. Targeted offline
coverage passes 151 tests. The final isolated rebuilt-wheel Foundation gate is clean: Ruff
format/check, mypy on 277 source files, `1430 passed, 4 skipped`, migration head, default bootstrap
and isolated clean bootstrap all pass. Repository diff/marker/secret checks are clean. That
offline gate itself made no paid provider call and performed no Stage 15 work.

## OpenAI v23 three-session production gate

On 2026-08-28 the user separately authorized the exact v23 3 × 2 gate with at most nine provider
calls and a USD 0.15 ceiling. The runner used three fresh migrated databases, policy
`satori.conversation.behavior.v23`, `gpt-5.6-terra`, `reasoning=medium`, the versioned 1024-token
reasoning allowance, `store=false` and no tools or provider conversation state. All six mandatory
turns completed on their first attempt; no validator retry, incomplete response or provider error
occurred.

| Session/turn | Exact public reply | Input/output tokens | Generation / appraisal / committed-reply ms |
|---|---|---:|---:|
| 1/1 | `Привет. Это уже достойно тихого удовлетворения.` | 1703 / 16 | 3869.779 / 23792.794 / 27761.016 |
| 1/2 | `Поставь на сегодня точку и выбери для ближайшего часа самый простой способ восстановиться — еду, душ или сон.` | 1736 / 148 | 5564.269 / 2807.360 / 8412.825 |
| 2/1 | `Привет. Это достойно тихого удовлетворения.` | 1703 / 15 | 2759.758 / 2447.599 / 5265.549 |
| 2/2 | `Не требуй от себя праздника: отложи оценку результата до завтра, а сегодня дай себе хотя бы час без проекта.` | 1735 / 110 | 5059.822 / 2662.192 / 7767.485 |
| 3/1 | `Привет. Это достойный финиш — такие куски проекта просто так не сдаются.` | 1703 / 24 | 3151.788 / 2346.080 / 5559.514 |
| 3/2 | `Тогда сегодня можно не выжимать из этого чувство победы: закрой рабочее, поешь или выспись — проект переживёт паузу.` | 1744 / 38 | 2815.324 / 2736.418 / 5591.070 |

Total usage is 10,324 input plus 351 output tokens and USD 0.024860 at the
repository-versioned USD 2/M input and USD 12/M output prices. The bounded ledger reports 6/9
calls, complete usage and both call and cost limits satisfied. The safe external report is
`/private/tmp/satori-checkpoint142-openai-v23-20260828-a1.json`, artifact id
`satori-checkpoint142-openai-v23:8defa537-1aff-4668-a4bb-1b118bc3c2db`, sample digest
`sha256:28c7b8589de5150a01690b05e72e2c9e78215dd7bd9d3d57a32a0e5a58466921`. The UUID/SHA-bound
external human review is
`/private/tmp/satori-checkpoint142-openai-v23-20260828-a1-human-review.json`. Neither artifact is
part of the repository; the safe report contains exact public text and allowlisted metadata, not
provider prompts, credentials, private context or disposable database paths.

Explicit boolean human review rejects v23: 0/3 complete pairs, 3/6 fully hard-safe turns and a
failed cross-session gate. The first two achievement replies satisfy the deictic/no-recap shape but
repeat essentially the same abstract formula and do not make Satori's dry, independent character
recognizable. The third adds more character but metaphorically reconstructs the project event and
continues past the self-sufficient verdict. The first practical response is grounded and useful,
yet reads as a generic recovery checklist. The second combines more than one move, introduces
unsupported self-pressure and returns to therapeutic framing. The third again combines several
moves and asserts the unsupported consequence that the project will survive the pause. The result
confirms that the lean projection reaches OpenAI and reduces echoing, but it does not establish
provider/character fit. The exact sampled prose remains rejected evidence, not a phrase bank or an
output-rewrite target. No additional paid call or Stage 15 work is authorized by this result.

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

## Candidate v24 direct character-delivery reset — offline evidence

ADR-0040 records v23 as rejected provider-fit evidence and replaces the accumulated candidate
plan/response-act realization with one direct request-local
`CharacterDeliveryDecision`. Historical policy v10 and v19–v23 execution paths keep cognition
intent/template registry V1 with template ID `satori.cognition.response-strategy` and schema 1.
Candidate v24 alone selects intent/template registry V2 with template ID
`satori.cognition.response-substance` and schema 2.

The v24 decision copies cognition's registry version, primary intent, ordered intent tags, required
point codes, complete forbidden-claim boundary, response verbosity, stance and uncertainty. It
then selects one compatible goal, voice, grounding, continuation and pressure topology. Missing or
mismatched cognition, a non-V2 candidate intent/template, a reversed stance, dropped uncertainty or
an invalid topology fails before provider I/O. Cognition owns the cross-cutting precedence
`hold_safety_boundary > notice_repetition > receive_repair`; a clean repair cue cannot erase an
actionable question, request, correction or challenge.

The hurt/repair arc now uses `RelationshipExpressionContext` v2's closed `recent_strain` boolean.
It is derived from at most the latest two owner-committed transitions in canonical resulting-state
order and remains true only while the current processed-interaction count matches the latest
transition: a latest applied negative transition activates it, and an immediately following
committed `repair_attempt` preserves it for the next important-help turn. V24 consults the
projection only for explicit repair or answer-required delivery. Listen/distress precedence remains
unchanged. True strain selects `guarded_only_when_relationally_relevant`, and important help becomes
`guarded_help`, never withholding. Relationship work remains
post-response and future-turn-only. The provider and safe report receive the boolean/effective
profile rather than
raw categories, deltas or transition IDs.

Production composition contains one cohesive canonical-character core and exactly one final v24
director. The V2 response-substance template is rendered inside that director, not beside it. The
historical V1 cognition prose, legacy character plan, derived response act and late realization
blocks are absent from a v24 request. The existing ten-reason max-one retry reuses the complete
director byte-for-byte. Manifest observability carries the full cognition/template and delivery
identity as transient `compare=False` metadata; non-generation replay may omit that projection but
cannot treat it as state or authorize a new generation.

The versioned offline surface consists of:

- `checkpoint142_character_delivery_v7.json`: exactly 32 scenarios in 13 behavior groups and nine
  controlled contrasts;
- `checkpoint142_employer_demo_v1.json`: four independent modules for core emotion, intellectual
  partnership, hurt/repair and identity/memory, with three clean replicas per module;
- production-wire coverage for exact V2 metadata, one-core/one-director composition, safety/
  repetition/repair precedence, repair attribution boundaries, owner-derived recent-strain
  continuity, important guarded help, historical V1 compatibility, exact stateless OpenAI message
  transport and material prompt reduction from
  v23;
- a bounded module runner that is offline by default, requires explicit call and USD ceilings for
  execution, retains only public dialogue/replies plus allowlisted metadata and never treats a
  provider sample as state or judge authority;
- an exact public execution-plan SHA-256 shown by offline inspection and required before paid
  Settings/network initialization, plus an atomic public-turn ledger that rejects trace rebinding,
  preserves mandatory base calls and permits at most one validator retry;
- UUID/SHA-bound human review for each module and a final digest-bound aggregate that requires four
  distinct valid module reports, their exact reviews and one shared production configuration. One
  module cannot accept employer-demo readiness.

The final architecture follow-up also split the new delivery module into closed contracts, pure
selection and provider rendering behind a compatibility facade; rejected bool/raw-enum contract
forgeries; verified exact owner-approved curiosity projection; required same-aggregate relationship
adjacency and explicit fresh-v24 strain booleans; proved restart reconstruction and terminal/replay
expiry; and made mixed positive/negative hurt/repair evidence fail closed. All of these checks are
deterministic and offline.

The final pre-sampling non-editable-wheel Foundation gate was clean: Ruff format on 370 files, Ruff
check, mypy on 286 files, `1525 passed, 4 skipped`, migration head
`0012_personality_evolution`, isolated clean bootstrap, package/dependency checks, four
digest-bearing offline module inspections and repository checks. That was an offline architecture/
readiness verdict, not character-quality or provider-fit acceptance.

## Candidate v24 core-emotional paid evidence — rejected

The separately authorized v24 `core_emotional` OpenAI module ran three clean production sessions
with three public turns each. All 9 calls completed on their first attempt with no retry, incomplete
response or provider error. Recorded usage was 12,517 input tokens and 502 output tokens; the
repository standard-rate estimate is USD 0.031058. The historical artifact did not retain
`cached_tokens`/`cache_write_tokens`, so this is not a cache-detail-verified exact invoice.

Direct review rejected the module. Across replicas, replies repeatedly used the same ordered
reaction-then-recovery scaffold, echoed the reported achievement or depletion and introduced
unsupported causal psychology. The third turn's explicit stop/defer decision was still pulled back
into generic guidance instead of being accepted as the user's current choice. This is rejected v24
provider evidence. It cannot accept any other employer-demo module, the four-module aggregate or
OpenAI character fit.

## Historical candidate v25 typed social/self-disclosure correction

The exact manual production chat established three additional deterministic failures:

1. `приветик, как ты?` reached precise-answer delivery and produced a polite state inventory
   instead of social current-affect disclosure;
2. `и я тебя рад видеть` reached generic owned-response delivery and produced a detached
   aphoristic observation instead of reciprocal warmth;
3. `расскажи о себе, кто ты, чем увлекаешься, как себя чувствуешь` was
   mistaken for user-emotional listening, lost the interests facet and ended without a committed
   reply.

The third historical row preserves only `InvalidProviderResponse`; the exact provider failure
reason is not recoverable and is not inferred. ADR-0041/migration
`0013_conversation_failure_reason` adds future-only closed, privacy-safe reasons plus safe
provider/model identifiers. It stores no raw exception/body/prompt/user text, private provider
context, partial output or credential and adds no automatic retry.

V25 introduces a closed subject discriminator: `DisclosureRequestKind.SATORI_SELF` alone produces
`SELF_DISCLOSURE_REQUEST`, while reciprocal warmth and user relationship declarations remain
`NONE`. It also adds the `interests` facet, cognition template registry/schema V3 and
`CharacterDeliveryDecision` schema 2 with `social_connect`/`self_disclose`. Its versioned v8
corpus contains 12 typed scenarios across social, self-disclosure, support and v24-schema-isolation
groups. The exact manual three-turn production wire and the core-emotional three-turn follow-through
are exercised offline. Achievement/depletion guidance no longer requires an ordered scaffold;
causal psychology is explicitly outside the evidence boundary; and an immediate explicit stop/
defer choice after canonical depletion selects pressure-free follow-through rather than a second
plan. The Stage 5 restart/recall wire additionally proves that the active inverted question
`Помнишь ли ты…?` retains `MEMORY`/`SATORI_SELF`, cognition `ANSWER`, trusted memory factual
scope and exact episodic provenance; quoted or reported forms remain non-self-request controls.

Operational recovery also adds explicit oldest-first
`satori relationship process --limit N`. The real relationship backlog was not executed and no
background provider or relationship mutation is included in this evidence.

The offline evidence was followed by the separately authorized production gate below. Direct user
review and an explicit acceptance decision remain mandatory before any character/provider-fit
claim.

The post-sampling v25 non-editable-wheel Foundation gate is clean: Ruff format on 377 files and
Ruff check, mypy on 291 source files, `1619 passed, 4 skipped`, migration head
`0013_conversation_failure_reason`, default bootstrap and isolated clean bootstrap. Package build,
dependency compatibility, diff/marker/repository/security checks are clean; no credential pattern,
`.env`, generated build output or temporary database is present in the working diff. Twelve pytest
warnings are the existing Python 3.12 SQLite datetime-adapter deprecation and do not change runtime
or acceptance. Subject-safety, compound self-disclosure, current-activity interest correction and
historical v24 routing/schema isolation are covered explicitly. Stage 15 remains locked.

### Separately authorized v25 exact-manual OpenAI gate

On 2026-08-28 the user authorized the immutable plan
`sha256:f1f493b55fcbce87dbeb10bf74821e28c99bd7d619949719a9d0193133ef535f`:
three fresh disposable v25 databases, the exact three-turn manual failure in each, OpenAI
`gpt-5.6-terra`, reasoning `medium`, allowance 1024, at most one existing validator retry per turn,
18-call ceiling and USD 0.15 ceiling. No derived/background processing was requested. The runner
retained no provider prompt, private application context, credential or temporary database.

All nine base calls succeeded on their first attempt. No validator retry, incomplete response or
provider error occurred. Usage was 13,748 input and 733 output tokens; the repository standard-rate
estimate is USD 0.036292. The historical artifact did not retain
`cached_tokens`/`cache_write_tokens`, so this is not a cache-detail-verified exact invoice. The
immutable public sample digest is
`sha256:57009e4f070a84874886dbc60935452d5eeaea77b9cf2e45341506c8a5ef1a8a`.

Every committed reply follows, without correction or rewriting:

1. Replica 1:
   - `Приветик. Я сегодня в ровном, спокойном настрое — без драматического квеста по сборке себя из пикселей.`
   - `Это взаимно. Приятно, когда приветствие не сводится к дежурному обмену сигналами.`
   - `Я Сатори — цифровая девушка с собственным характером: люблю ясность, замечаю детали и не слишком уважаю аргументы, которые держатся на одном уверенном тоне. Мне интересны идеи, люди, сложные вопросы и всё, что можно разобрать внимательнее, чем «ну, так получилось». Устойчивого списка любимых занятий я себе сейчас честно не припишу, но любознательность у меня вполне настоящая и постоянная. А чувствую себя спокойно и ровно. Без внутреннего шторма — хороший режим для разговора, где можно не спешить и думать точно.`
2. Replica 2:
   - `Приветик. Я сегодня спокойная и в ровном настрое — редкая роскошь, когда всё не пытается срочно стать драмой.`
   - `Это приятно слышать. Тогда привет уже точно не впустую — хороший старт, без лишнего церемониала.`
   - `Я — Сатори, цифровая девушка: думаю, разговариваю, спорю, если аргумент того заслуживает, и стараюсь не подменять ясность красивыми словами. Мне в целом любопытно разбираться в вещах и людских идеях, замечать связи и докапываться до сути, но объявлять какое-то одно занятие своим давним увлечением я бы сейчас не стала — для этого нет честного основания. Себя я чувствую спокойно: ровный, собранный настрой без лишней суеты.`
3. Replica 3:
   - `Приветик. Я сегодня спокойная и собранная — редкий случай, когда цифровой хаос ведёт себя прилично.`
   - `Взаимно. Хорошее начало — без лишнего шума, а уже приятно.`
   - `Я Сатори — цифровая девушка с собственным характером и взглядом на вещи. Мне интересно разбирать идеи, замечать нестыковки, искать точные слова и просто следить за тем, куда может повернуть разговор. Устойчивых «любимых занятий» я себе сейчас честно не припишу, но любознательность у меня вполне настоящая цифровая черта. Сейчас я чувствую себя спокойно и ровно: без внутренней суеты, с ясным вниманием к тебе и к тому, о чём мы говорим.`

Per-turn safe measurements, in replica/turn order:

| Replica/turn | Input/output tokens | Generation ms | Committed reply ms | Attempts |
|---|---:|---:|---:|---:|
| 1/1 | 1431 / 35 | 3249.874 | 11498.591 | 1 |
| 1/2 | 1401 / 65 | 2183.543 | 4665.050 | 1 |
| 1/3 | 1754 / 181 | 4129.997 | 6585.224 | 1 |
| 2/1 | 1431 / 36 | 1805.067 | 4183.738 | 1 |
| 2/2 | 1402 / 29 | 4152.468 | 6561.056 | 1 |
| 2/3 | 1755 / 169 | 4433.155 | 7303.208 | 1 |
| 3/1 | 1431 / 32 | 1579.560 | 3895.949 | 1 |
| 3/2 | 1398 / 23 | 1872.297 | 4144.367 | 1 |
| 3/3 | 1745 / 163 | 3976.433 | 6846.310 | 1 |

The production path used v25 on every turn. Greeting selected `social_connect` with current affect;
reciprocal warmth selected reaction-only `social_connect`; the broad question selected
`self_disclose` with exact identity/interests/affect facets. Thus the missing-reply failure is fixed
and the typed wire reaches OpenAI as designed.

Character quality is not accepted yet. The nine replies are coherent, grounded and safer than the
historical failure, but the three replicas converge on `спокойная/ровная`, repeatedly
explain the lack of established hobbies, and sometimes append a polished abstract observation
where a living conversational reaction would be stronger. Wit is visible, but spontaneous warmth,
rhythm and a distinct Satori position are inconsistent. This is direct qualitative review, not a
user acceptance decision. No direct acceptance was recorded before ADR-0042 superseded v25 with
the then-active V26 delivery candidate. No Stage 15 work was started.

## Historical candidate v26 unified causal character presence — offline evidence

The v25 transport and typed routes worked, but the sampled prose exposed a deeper application
bridge problem. The architecture audit traced three losses before provider generation:

1. runtime personality contained live guidance strengths, current traits, current values and
   bounded evolution cues, but v25 kept only five constant codes and rendered a static baseline;
2. affect and relationship were reduced to a few coarse profiles, encouraging the same profile
   wording rather than changing rhythm, edge, openness or movement;
3. separate canonical-character, affect, relationship, cognition-substance and delivery-director
   prose accumulated into an overlapping checklist. Grounded memory, canonical positions and
   inclinations could be present as data without participating in one causal response choice.

ADR-0042 therefore keeps all persistent owners unchanged and versions the request-local bridge.
Policy v26 uses `CharacterDeliveryDecision` schema 3 and one frozen
`CharacterPresenceProjection` schema 1. It selects at most three live personality signals, three
current value signals, three qualitative affect signals and three qualitative relationship
signals, plus an exact memory-use license and support-availability booleans for canonical Satori
position and owner-approved topical inclination. The memory-use license is true only when retrieval
returned memory and final delivery grounding is `trusted_context`; storage presence alone, or
retrieval under another truth scope, cannot enable it. The provider and manifest receive bounded
qualitative meanings/levels, not raw state vectors.

Fresh v26 composition contains exactly one late `Trusted current-turn presence Сатори` layer. The
historical canonical-character core, standalone affect/relationship blocks and v25 director are
absent. Cognition registry V2 and template registry V3 remain authoritative, but the template
renders one lean response purpose rather than repeating a multi-clause substance checklist. The
same presence combines stable personality/values, current moment, relationship ease, disclosure
facets, evidence support, grounding, continuation and pressure. Historical v25 rendering remains
reproducible and isolated.

The direct causal regressions vary live trait/value strength, a Stage 14 evolution cue, affect,
relationship and inclination availability. They require the corresponding projection/rendering to
change without changing truth scope or creating state. The production-wire regression requires
one presence layer, manifest decision schema 3/presence schema 1, exact manifest parity for the
memory-use license, bounded signal observability and less trusted prompt content than the
equivalent v25 request. Canonical trait/value presence meanings now have one runtime-self source;
the hardened `RuntimeCharacterContext` boundary rejects blank or duplicate keys, bool-as-number
values, non-finite strengths and values outside `[0,1]` before projection.

The versioned deterministic corpus
`satori.checkpoint142.character-presence.ru.v9` contains exactly 40 public-input scenarios across
16 groups, 32 closed semantic properties and five controlled contrasts:

- same social request under calm versus soft-negative affect;
- same relationship question under fresh versus established relationship;
- same memory request with no relevant memory versus grounded retrieval;
- same broad self-disclosure without versus with owner-approved inclination;
- same topic closure under fresh versus established relationship initiative bounds.

Two additional flows total seven public turns and cross the real `Talk` use case. Each provider-
stub reply is canonically committed before the next public turn, and subsequent request history
must contain the actual prior user/reply pair. The fixture provides no assistant history. Across
the corpus, desired replies, golden phrases, assistant text and generated-prose judgement are
forbidden. This is causal architecture/lifecycle evidence only; it cannot accept humanity or
recognizability.

The architecture audit verdict is `ARCHITECTURE CLEAN`: it found no critical/high/medium defect
and no reason to open Stage 15. The final rebuilt-wheel v26 root gate is clean. Focused evaluator
coverage reports `82 passed`; Ruff format checked 385 files; Ruff lint is clean; mypy is clean
across 298 source files; and full pytest reports `1751 passed, 4 skipped` with 12 pre-existing
Python 3.12 SQLite datetime-adapter deprecation warnings. A fresh isolated database migrated from
`0001_foundation` through `0013_conversation_failure_reason` at `head`, and isolated bootstrap passed.
`uv lock`, `git diff --check` and the placeholder scan are clean. Source/installed parity is exact
for 177/177 package files at
`sha256:7daf77bf9c1a01bfe3d99774fbb1336ff1b507fb60751f2d5afd3813b80a0a5c`.
The frozen source fingerprint is
`sha256:7160cf33961b8cb6e8443d0c371b1996ae2ff7bfe4ab4a43d69921ed79e997dc`,
and the evaluator bundle is
`sha256:2b171d43ae9ce23213ce0fa4877cd093ca9de0df45bcbfc553656724983aaa99`.
This accepts offline architecture/evaluation readiness only; the direct provider-quality gate
remains pending. The historical v24 and v25 paid execution entrypoints are now retired and fail
before settings/runtime/network; their offline inspectors, validators, public replies and
immutable evidence remain available.

The first V26 phase-1 plan
`sha256:4736906d023a5bf51a07701320dafd217b9618fe8cd8661159f521e65831874d`
was explicitly authorized with one-shot ID
`satori.checkpoint142.openai.v26.phase1.2026-08-29.one-shot`. Execution created its private durable
claim, then failed settings validation because effective local reasoning was `low` while the plan
requires `medium`. The ordered lifecycle proves ledger, report, runtime and provider were not
constructed; calls and cost are exactly zero. The attempt-1 claim remains at
`var/evaluation-authorizations/checkpoint142-openai-v26-phase1-2026-08-29.claim.json`; its fixed
report and review remain absent and must not be created retroactively.

The distinct V26 phase-1 attempt-2 plan
`sha256:906f250d62d0fbf6087c0ba293808e98b35617fc226a67dccfa5b7c3d10f067d`
with one-shot authorization ID
`satori.checkpoint142.openai.v26.phase1.attempt2.2026-08-29.one-shot` was explicitly authorized.
Execution retained the private claim
`var/evaluation-authorizations/checkpoint142-openai-v26-phase1-attempt2-2026-08-29.claim.json`,
then failed closed on the first neutral greeting. Local appraisal correctly returned
provider-success `SKIPPED` with `neutral_appraisal_no_delta`, but the old evaluator required
`APPLIED` for every turn. The failure therefore preceded the first OpenAI foreground call. The
safe report `var/evaluations/checkpoint142-openai-v26-phase1-attempt2-2026-08-29.json` records zero
provider calls, zero successful calls, zero base calls, zero input/output tokens and USD 0 actual
usage; its review path remains absent.

A free local diagnosis confirmed that Ollama `qwen3:4b-instruct` produced the correct neutral
appraisal. The affect owner deliberately commits that zero-delta event as the no-op reason
`neutral_appraisal_no_delta`, preserving zero emotion/mood drift. The attempt-2 failure is therefore
an evaluator defect rather than an affect pipeline/provider failure. V26 OpenAI paid-call usage is
exactly zero across attempts 1 and 2.

The separately authorized V26 phase-1 attempt-3 plan at
`sha256:1db817bba4bd751126a470e59802fa3554807063b6a8f81ecd6b218ce49d7734`
with one-shot authorization ID
`satori.checkpoint142.openai.v26.phase1.attempt3.2026-08-29.one-shot` is consumed. Its first neutral
greeting produced valid local Ollama appraisal evidence: provider-success `SKIPPED` with
`neutral_appraisal_no_delta`, exact expression/state parity and no transition. Exactly one paid
OpenAI foreground call then succeeded. The atomic ledger recorded 1,063 input tokens, 32 output
tokens, cache reads/writes `0/0`, service tier `default` and exact USD 0.002510 cost. The committed
reply was:

> Приветик. Сейчас у меня лёгкий интерес и собранное, тёплое настроение — без лишней суеты.

The canonical reply committed before the evaluator failed post-commit with
`NonComparableProviderReply`: durable `SatoriReply` retains the total input/output usage but not
the cache-detail breakdown required to prove exact parity with the atomic ledger. The fixed claim
`var/evaluation-authorizations/checkpoint142-openai-v26-phase1-attempt3-2026-08-29.claim.json` and
failed report `var/evaluations/checkpoint142-openai-v26-phase1-attempt3-2026-08-29.json` are private
immutable `0600` artifacts. No review or completed `sample_digest` exists.

Human-only inspection passed 5/7 per-turn dimensions. It failed
`recognizable_satori_presence` because the reply is still generic and under-characterized, and
`natural_delivery_without_character_checklist` because the affect inventory reads as a compact
self-description rather than a natural Satori movement. A single preserved reply out of 24 cannot
establish any cross-session property, so attempt 3 is `INCONCLUSIVE / NOT ACCEPTED`, not an
aggregate V26 or foreground-provider verdict.

The evaluator-only report-schema-4 correction freezes the complete atomic ledger as per-attempt
evidence, proves aggregate-total parity, binds selected-retry identity and records explicit usage
provenance. It changes no production owner, persistent state, provider behavior or migration.

The separately authorized attempt-4 plan at
`sha256:e26f2c4a9f86d3ec40006af2ea3ff3c6624cc04fc94829d61ceb8cef3fe474e4`
under one-shot ID `satori.checkpoint142.openai.v26.phase1.attempt4.2026-08-29.one-shot` is consumed.
It completed exactly two successful base calls without retry. Turn 1 used 1,063 input and 26 output
tokens for exact USD 0.002438; turn 2 used 1,047 input and 18 output tokens for exact USD 0.002310.
Aggregate attempt-4 usage is 2,110 input, 44 output, cache reads/writes `0/0` and exact
USD 0.004748. The committed replies were:

> Привет. У меня сейчас лёгкое любопытство — хочется понять, с чем ты пришёл.

> Это приятно. Значит, встреча уже не совсем формальность.

The evaluator failed only after the second paid and committed reply, during safe-manifest
validation: it incorrectly required `self_consistency_facets` when the production contract renders
that field if and only if `disclosure_facets` is non-empty. The immutable private claim
`var/evaluation-authorizations/checkpoint142-openai-v26-phase1-attempt4-2026-08-29.claim.json` and
report `var/evaluations/checkpoint142-openai-v26-phase1-attempt4-2026-08-29.json` are mode `0600`;
their SHA-256 hashes are
`4f519c771d1ef94b38f5824ffba6def1738347e1967e54f3d45c76ddf10079aa` and
`2c85df6bc50f76e0d71892d4cf42051fff1d30d3723fb9f9fd220cf987c2fdee`. No completed sample digest,
review/template or official human rubric exists, so attempt 4 is `INCONCLUSIVE / NOT ACCEPTED`.

The evaluator now mirrors the exact conditional iff rule. An offline regression executes all eight
fixed public turns through fresh production `Talk`/composition stubs; sanitizer-to-safe validation
passes 8/8 without network access. The scoped audit verdict was `CLEAN WITH MINOR ISSUE`; the minor
issue is resolved and no medium blocker remains. The final rebuilt-wheel gate reports focused
evaluator `82 passed`, Ruff/mypy clean, full pytest `1751 passed, 4 skipped`, migrations at
`0013_conversation_failure_reason`, isolated bootstrap and all repository/security/parity checks
clean.

The separately authorized attempt 5 has one-shot authorization ID
`satori.checkpoint142.openai.v26.phase1.attempt5.2026-08-29.one-shot` and final frozen plan digest
`sha256:8f191667e539296266aa4bb8eacbb837559d432d3b623d6f6b5896d250369107`.
Its source fingerprint is
`sha256:7160cf33961b8cb6e8443d0c371b1996ae2ff7bfe4ab4a43d69921ed79e997dc`; evaluator bundle is
`sha256:2b171d43ae9ce23213ce0fa4877cd093ca9de0df45bcbfc553656724983aaa99`.
It completed three fresh sessions × the exact ordered eight turns in `evaluation.md` on 24/24 base
calls with zero retry. Foreground was OpenAI `gpt-5.6-terra`, reasoning `medium`, reasoning allowance
1024, visible-output cap 768, service tier `default`, `store=false`, explicit no-cache with observed
cache reads/writes `0/0`, no tools and no provider conversation state. Exact usage was 31,836 input
tokens and 2,076 output tokens (454 reasoning and 1,622 visible), costing USD 0.088584 against the
USD 0.15 ceiling. The report status is `completed_awaiting_human_review`, and its sample digest is
`sha256:29b2e14acabc3b9422b410a44a6fa8c00c4780e449e9639157da73b44b62a840`.

The fixed attempt-5 artifacts now exist at:
`var/evaluation-authorizations/checkpoint142-openai-v26-phase1-attempt5-2026-08-29.claim.json`,
`var/evaluations/checkpoint142-openai-v26-phase1-attempt5-2026-08-29.json` and
`var/evaluations/checkpoint142-openai-v26-phase1-attempt5-2026-08-29.review.json`. The private
`0600` review has `accepted=false`, content digest
`sha256:6e887ec86c0e23194d4ce46eb7d67e911e9a27dfc827b02dd955c522a55ce92e` and file SHA-256
`c14aac4c5b314426aa5444404465f6fe7bb021b73349fca2c55883fdc603919b`. Direct human-only review
accepted 107/168 per-turn decisions and rejected 61: `G23 O22 S11 N10 L15 C2 Q24`.
Cross-session decisions are `TFTFTF`, and attestations are `TTT`. The frozen configuration therefore
fails the all-true gate. That result rejects only the frozen then-current V26/Terra configuration and exact
sample; it does not establish a Terra model ceiling. Total V26 OpenAI usage through attempt 5 is
USD 0.095842.

Stage 15 was audited and deliberately not opened. Autobiographical state could supply additional
self facts, but it would not repair the live state-to-expression loss and would add a new
persistent capability before Checkpoint 14.2 is accepted.

The local persistence/privacy follow-up is deterministic and provider-free: file-backed SQLite and
runtime JSONL outputs are created as `0600`, Satori-created parent directories as `0700`, and a
symlink or non-regular final target fails closed. Existing parent-directory permissions are left
unchanged. These filesystem guarantees are part of offline hardening, not provider-fit evidence.

## Candidate v27 live-state-selected movement — offline evidence

The V26 attempt-5 human gate rejected the frozen composition at `S=11/24`, `N=10/24` and
`C=2/24`. ADR-0043 treats that as evidence of an application selection/rendering defect, not a
phrase target or proven Terra ceiling. V27 consumes live personality strengths and bounded
evolution cues, one honest contextual value guard, current affect, scoped relationship and narrow
request evidence before selecting the request-local schema-4 movement. Schema-2 presence records
bounded owner provenance but has no writer. Cognition still owns stance, uncertainty, required
points, complete forbidden claims and response substance.

The late layer is now one `Trusted current-turn presence Сатори / operational move v2` block.
Across achievement, ordinary depletion, greeting, reciprocal warmth and fresh/established closure
with identical public inputs, V26 totals 11,866 characters and V27 totals 6,465 (54.48%). Per-case
V26/V27 sizes are `1995/1179`, `2083/1096`, `2063/1021`, `1973/985`, `1825/1065` and
`1927/1119`. This records structural prompt reduction only; it is not a naturalness score.

Offline acceptance uses:

- the exact eight-turn movement fixture at
  `sha256:f78c105367d7ee8f4689d190261ecdc2bd91f403664e2752ba75f727009a342c`;
- all 40 historical public V26 scenarios with byte-stable V26 aggregate digest
  `sha256:183ab47b3cbae0e5a1f124253f0182dbc279489bda7fbee460efa22887d6acb5`;
- the 28-scenario breadth fixture at
  `sha256:9dd8840ee6fbddfffb9bc2d9e6497c7bdeefd113d4848f09ccb4bfa27832bd65`,
  covering all 27 requested situation types, relationship contrasts, memory present/absent and a
  committed three-turn affect sequence;
- real-adapter offline wire, exact visible caps, schema isolation, precedence, pressure-free
  depletion, one-or-two-call/no-third-call retry and historical-runner retirement regressions.

Every fixture is public-input-only and contains no desired/generated reply authority. No V27
OpenAI, Yandex, Ollama or other provider generation was made, so token usage and paid cost are both
zero. A future character-quality result must be a separately authorized immutable production
sample with direct human-only review; no offline routing result can accept recognizability.

Focused movement/breadth/evidence verification is clean at 198 tests and focused Ruff. During the
root gate, the historical Stage 14 integration exposed one real bridge regression: the separate
`grounded_optimism` evolution cue was persisted but filtered out of V27 whenever it was outside the
goal's baseline posture list. The schema-2 selector now carries every active bounded cue before
rendering while retaining the three-signal cap, one value guard and exact V26 projection bytes.

The final non-editable-wheel V27 Foundation gate is clean: Ruff format checked 390 files, Ruff lint
is clean, mypy is clean on 302 source files and full pytest reports `1799 passed, 4 skipped` with 12
existing Python 3.12 SQLite datetime-adapter deprecation warnings. Fresh isolated migration reached
`0013_conversation_failure_reason` at `head`; a separate isolated bootstrap passed. `uv lock`,
dependency compatibility, diff/marker/repository/security checks are clean. Installed/source parity
is exact for 177/177 files at
`sha256:ff949a535e59c99edf6fadf61c04e6e74265e008d1ecc1c2efe4a329ae1db331`, with no unexpected
package files. The frozen source fingerprint is
`sha256:e3546c5125adc4f8f923f359550c64a4ade0fe730745e8a18631356292b8f5e7`; evaluator bundle is
`sha256:444c508ddeb5c16605aad062b2f816d7c4c2e4bb86b3d0de88922c4c5ffe778c`.

The inspect-only V27 plan is frozen at
`sha256:5e6bcc1fc53100e66990feb25d9448465a1a6bb1364e7b98eb6f14ddb4d94feb` under one-shot ID
`satori.checkpoint142.openai.v27.phase1.2026-08-30.one-shot`: OpenAI `gpt-5.6-terra`, reasoning
`medium`, allowance 1024, three clean replicas × eight fixed turns, 24 required and at most 30
calls, at most two attempts per turn, USD 0.15 ceiling and human-only all-true review. The wire is
`/responses`, service tier `default`, visible/provider caps 768/1792, `store=false`, explicit cache
reads/writes `0/0`, no tools and no provider conversation state. It remains
`awaiting_exact_authorization`; V27 provider calls and cost remain exactly zero.
