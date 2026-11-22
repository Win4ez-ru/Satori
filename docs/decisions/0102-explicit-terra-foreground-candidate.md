# ADR-0102: Explicit Terra foreground with Luna background

- Status: Accepted for isolated implementation and qualification; no model or release acceptance
- Date: 2026-10-31
- Authority: the user's all-GPT, server-hosted Satori goal and the coordinator's bounded alternate-candidate task after failed Luna semantic samples
- Extends ADR-0100/0101; adds one explicit model profile without replacing the Luna default
- Isolation: `var/artifacts/server-runtime-20261031/work-foreground-terra-r4` only

## Evidence and decision

The coordinator's retained real-provider semantic review of the exact 17-turn production
scenario reports critical Luna failures: missed repetition at turns 2/3 and confabulated
recent dialogue at turns 15/16. Completion of an API request and passing typed parsers did
not establish conversational acceptance. The failed samples and original r3 source/gate
receipts remain historical evidence; no prompt, phrase rewrite or Character tuning is used
to manufacture a pass.

Prepare a separately selected candidate, not an automatic fallback:

| Profile | Conversation | Seven background generative roles | Embedding |
| --- | --- | --- | --- |
| `all_luna` (unchanged default) | `gpt-6-luna` | `gpt-6-luna` | `text-embedding-3-small` |
| `foreground_terra` (explicit opt-in) | `gpt-6.1-terra` | `gpt-6-luna` | `text-embedding-3-small` |

Background means episode formation, semantic formation, affective appraisal, relationship
appraisal, model formation, position formation and reflection. All nine provider kinds remain
OpenAI. A closed `OpenAIModelProfile` is validated with the exact per-role model IDs. An
arbitrary model name, mixed assignment or nondefault profile outside the explicit all-OpenAI
mode is rejected. Existing local defaults and foreground-only legacy configuration are not
silently changed. The explicit factory supplies every Settings field and ignores inherited
environment/`.env` profile values. The private operator CLI exposes `--model-profile`; selecting
Terra without the existing `--openai` mode is rejected rather than ignored.

The coordinator fetched official OpenAI documentation on 2026-10-31. The
[Terra model page](https://developers.openai.com/api/docs/models/gpt-6.1-terra) documents
Responses, reasoning `none`, structured output and natural-writing suitability. These are
capability claims, not quality evidence for Satori. The
[official pricing page](https://developers.openai.com/api/docs/pricing) gives Standard
short-context Terra rates per million tokens: USD 1 input, USD 5 output, USD 0.05 cached input,
and cache-write input factors 1.25 for 30 minutes / 2.5 for 12 hours. This implementation uses
the highest 2.5 input factor conservatively and assumes no cache discount. No new API request,
balance inspection, purchase or provider account action is part of this preparation.

## Output, state and provider boundaries

The ADR-0101 foreground floor stays 128 within the existing global ceiling of 768. The same
typed request is passed to the unchanged conversation adapter with reasoning `none` and zero
reasoning allowance. No prompt, temperature policy, per-turn heuristic, source-admission rule,
canonical state owner, evidence rule, validation reason or retry policy changes here.
Incomplete/refused responses remain invalid. The shared max-one regeneration keeps the same
model and cap; there is no escalation, third attempt, fallback or output rewrite.

The provider remains separate from identity, personality, memory, affect and relationship.
Terra receives only the same previously authorized bounded context required by the requested
conversation operation. This adds no external mail/calendar/Telegram/file/tool-card admission,
whole-profile upload, raw reasoning or new source grant. OpenAI embedding space remains exact
and distinct from historical Ollama vectors.

## Exact role/model cost admission

Each `BudgetedOpenAITransport` is bound to one closed role/model pair before any dispatch.
Terra is permitted only for conversation; seven background roles require Luna, and embedding
requires `text-embedding-3-small`. A wire model differing from that binding is rejected before
reservation and HTTP. Existing omitted-model constructor calls retain the original Luna or
embedding binding; an invalid explicit value never becomes a default.

Reservation and reported usage estimation both use the bound model, actual serialized input
bound and actual wire output cap. Terra's USD 1/5 rates are ten times Luna's USD 0.10/0.50 for
the same nonzero input/output counts, including the same 2.5 cache-write input allowance.
The existing canonical endpoint, Standard/default tier, stateless request, schema/usage bounds,
unknown-outcome liability and no-replay rules remain unchanged. No unpriced model/tier or
unverified cache discount can make a request appear cheaper.

FX and tariff review/expiry remain explicit finite runtime assumptions, not permanent fresh
prices. Existing coordinator-selected FX 130 and its expiry are not reset by this code. An
API estimate is not an invoice, tax calculation or proof that the full monthly service cost
fits 3,000 RUB. Model/voice/backup/telephone allowances remain separate; unknown telephony
charges keep complete service cost unknown.

## Usage v1 reader compatibility

Add closed metadata value `Model.GPT_6_1_TERRA = "gpt_6_1_terra"`. The SQL layout and
`user_version=1` remain unchanged. New code reads existing Luna/embedding v1 documents with
their original values, budgets, states and unknown liabilities. No migration, deletion,
rewriting, rollover, cap increase, reset or new live usage ledger is performed.

This is **not forward-compatible with old r3 readers**: their closed Model enum correctly
rejects a future Terra record, even though the SQL version remains 1. Before any real Terra
attempt against the existing shared 20 RUB test ledger, the coordinator must explicitly
adopt the new reader capability, stop incompatible readers/writers and retain the old code,
reports and an immutable ledger beforeimage. Thereafter ledger reads use r4-capable code.
Do not weaken validation, discard unknown values, reset the ledger or create a substitute
budget to make an old reader work. Rolling code back to r3 is not a supported way to read
Terra-containing accounting history.

## Qualification and remaining gates

First run focused mock-only configuration, role/model cross-rejection, exact cost/reservation,
all-nine-port wiring, CLI, usage-v1 continuity and real synthetic Core tests, including partial
output rejection and the unchanged one-retry path. Preserve all original Luna cost/routing
tests. Verify the original 963 r3 inputs and gate receipts remain byte-identical.

Stop this preparation before paid calls or a fresh gate. Independent review then precedes a
fresh noneditable Foundation/server qualification for the new source. Only subsequent actual
provider, multi-turn semantic, affect, grounding, continuity and replay evidence can qualify
Terra for Satori. No natural-writing catalogue description or synthetic fixture proves it.
Character remains paused, Stage 15 closed. Linux/hosted delivery, authenticated client access,
encrypted backup/restore, reviewed single-writer state handoff and Mac-asleep iPhone acceptance
remain separate gates. Main/runtime source, installed Mac16, candidate17 and iPhone3 are unchanged.
