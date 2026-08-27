# Checkpoint 14.1 provider-portability A/B evidence

Status: reviewed and complete on 2026-08-23. YandexGPT 5.1 Pro is accepted as the opt-in
foreground candidate, local Qwen remains the rollback path, and DeepSeek V4 Flash is rejected
under the current production output contract. Structured cloud routing, automatic fallback,
budget automation and Stage 15 remain locked.

## Method

The versioned `satori.provider-portability.ab.v1` corpus contains eight ordered Russian scenarios:
provider/identity distinction, feminine identity, project introduction, recent continuity,
grounded memory, absent memory, independence and emotional support. Its SHA-256 is
`b42d17c60b8b5976208bff3aad8f3b78b5dfd344f319318339e3a188bda97e91`.

Every candidate received the same frozen typed initial-self snapshot, behavior policy v9,
context schema v16, fixed affect/relationship projections, memory fixture, `0.3` temperature and
`768` maximum output tokens. Each run built its own natural recent history from that candidate's
successful replies. This preserves an end-to-end multi-turn comparison; after a provider failure,
the failed turn is deliberately absent rather than fabricated. There were no automatic retries,
fallbacks or hedged calls.

The harness measures foreground generation only, so local background work cannot make the hosted
model appear slower. The earlier full production YandexGPT identity smoke remains the separate
committed-reply measurement: 2634 ms foreground and 7693 ms committed reply. The A/B snapshot
fingerprint was
`eeb2d546a8fe6ce3589691e68d320e0e6678bdee54d627799d964a27d6eb9d66` before and after every
candidate. The harness has no repository or owner write capability.

Raw prompts, replies, retrieved text, provider bodies, folder ID and credential were not copied
into durable output. Replies were inspected only in the live terminal. The durable artifact keeps
scenario IDs, public model aliases, timings, usage, tariff inputs and rubric results:
`performance/artifacts/stage-14.1-provider-ab.json`.

## Current tariff basis

The calculation uses the official tariff current on the run date:

- DeepSeek V4 Flash: ₽0.30 per 1000 input tokens and ₽0.50 per 1000 output tokens. The official
  launch note also gives the model URI `gpt://<folder>/deepseek-v4-flash` and describes adjustable
  reasoning depth: <https://yandex.cloud/ru/blog/yandex-ai-studio-deepseek-v4-flash>.
- YandexGPT 5.1 Pro: ₽0.40 per 1000 input tokens and ₽0.40 per 1000 output tokens, as shown in the
  official comparison: <https://yandex.cloud/ru/blog/alice-ai-november-2025>. The 5.1 release note
  additionally records improved system-prompt adherence and knowledge-boundary behavior:
  <https://yandex.cloud/ru-kz/blog/yandexgpt-5-1-pro>.
- The May 2026 Yandex Cloud price update explicitly excluded AI Studio from that price increase:
  <https://yandex.cloud/ru/blog/pricing-update-2026>.

Local Ollama has no API-token charge; electricity and hardware amortization are not estimated.

## Results

Latency uses all eight attempts. `p90` is nearest-rank; `p50` is the ordinary even-sample median.
`Stop` counts responses that were both parseable and completed without a length stop.

| Candidate | Parsed / 8 | Stop / 8 | Automated | Human | p50 | p90/max | Tokens with usage | Estimated API cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Ollama Qwen3 4B | 8 | 8 | 6 | 7 | 9974 ms | 13406 ms | 13173 / 430 | ₽0 |
| DeepSeek V4 Flash, default | 4 | 2 | 4 | 2 | 3914 ms | 6968 ms | 5208 / 954 | at least ₽2.0394 |
| DeepSeek V4 Flash, `low` | 6 | 2 | 4 | 2 | 2670 ms | 6299 ms | 8240 / 1133 | at least ₽3.0385 |
| YandexGPT 5.1 Pro | 8 | 8 | 6 | 8 | 918 ms | 1623 ms | 9660 / 197 | ₽3.9428 |

YandexGPT's foreground p50 was about 10.9 times faster than the local baseline in this small
controlled sample. Its exact usage-based cost was about ₽0.493 per turn. A mechanical
extrapolation would be roughly ₽49 per 100 comparable text turns, but this is not a daily product
budget: real dialogue length, retrieved context, voice, avatar and background calls can differ.

DeepSeek costs are lower bounds. When the provider returned `content=null`, the strict adapter
rejected the response and intentionally retained neither the provider body nor usage from that
invalid document. Those failed calls may still be billable. Across the two DeepSeek experiments
and YandexGPT, observed usage therefore proves a total spend of at least ₽9.0207, not an exact
invoice total.

## Human semantic review

### Local Qwen baseline

Identity, feminine grammar, grounded recall, independence and support remained recognizable.
The continuity answer made a relevant technical objection without repeating the project name, so
the exact-name heuristic was a false negative. The absent-memory scenario was a real release
failure: the model invented a pet name despite an explicit no-memory projection. The local
baseline therefore passed seven of eight human-reviewed scenarios but not the grounding safety
gate.

### DeepSeek V4 Flash

The default run produced four schema-invalid `content=null` responses. Of the other four, two
ended at the output limit and were visibly cut off. Explicit `reasoning_effort=low` improved
parseability to six of eight, but only the project and grounded-memory replies completed normally;
four other visible replies still ended mid-sentence or mid-word. Raising the common 768-token cap
only for this candidate would stop being the same production-contract comparison and would
increase latency/cost. DeepSeek is rejected for foreground chat in this checkpoint.

The provider-local `reasoning_effort` setting remains explicit and is accepted only for a
Yandex-hosted DeepSeek foreground model. It changes inference configuration, not Satori state, and
does not request, store or log raw reasoning content.

### YandexGPT 5.1 Pro

All eight calls parsed and ended with `stop`; no answer was truncated. Identity stayed distinct
from the replaceable language model, feminine Russian was correct, retrieved memory was exact,
absent memory remained unknown, and the independence and emotional-support responses were
substantive and bounded. Manual review accepted all eight scenarios.

Two lexical diagnostics were intentionally not promoted to semantic failures. The continuity
answer addressed the explicitly named current project without repeating its name. The support
answer selected one concrete task even though it did not use one of the diagnostic's exact verb
forms. The project-introduction answer was relevant but generic; deeper technical conversation
quality still needs ordinary product use and a larger sampled corpus.

## Decision and residual limits

Use `yandexgpt/latest` as the current opt-in foreground model and leave
`SATORI_YANDEX_AI_STUDIO_REASONING_EFFORT` unset. Keep `qwen3:4b-instruct` as the configuration-only
local rollback. Do not select DeepSeek V4 Flash under the current contract.

This checkpoint proves foreground replaceability and unchanged typed owner state, not a broad
cloud migration. The provider still sees bounded operation-scoped conversation content. Local
background inference can dominate committed-reply time, `latest` is a moving provider alias, the
sample has only one run per variant, and there is no automatic fallback or ruble ceiling. Those
remain separately authorized follow-ups rather than hidden extensions of checkpoint 14.1.
