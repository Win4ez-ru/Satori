# Stage 10 structured cognition acceptance evidence

Status: accepted 2026-08-22 on the target Mac. Context schema is v13, behavior policy remains v9
and migration head remains `0008_user_world_models`.

## Deterministic planning latency

The acceptance benchmark ran 5,000 complete
`perception → need mix → retrieval plan → appraisal handoff → position → intent → strategy`
pipelines with the versioned mixed project/analysis/challenge fixture. It excludes retrieval,
provider appraisal and generation, which remain separately timed foreground work.

| Samples | Median | Nearest-rank p90 | Maximum | Gate |
|---:|---:|---:|---:|---:|
| 5,000 | 0.032875 ms | 0.034083 ms | 0.126334 ms | median <10 ms; p90 <25 ms |

The deterministic V1 policy therefore adds no material foreground latency and does not justify a
second provider call, cache, broker or persistence layer.

## Real Ollama manual inspection

One fresh four-turn `satori chat --debug` session used local `qwen3:4b-instruct`. The public
acceptance scenarios exercised direct explanation, presence without advice, requested challenge
and explicit uncertainty. The trace selected, respectively:

| Scenario | Position | Primary intent | Strategy |
|---|---|---|---|
| direct answer | `answer` | `answer_directly` | technical/analytical |
| emotional presence | `listen` | `listen_and_reflect` | `warm_gentle/brief` |
| requested disagreement | `challenge` | `challenge_gently` | `warm_direct/medium` |
| uncertainty | `uncertain` | `clarify_uncertainty` | `concise_neutral/medium` |

Observed foreground cognition totals were sub-millisecond; the retained three visible samples
were `0.276`, `0.412` and `0.356` ms. Every inspected trace had status `applied`, no fallback and
no raw prompt, user text, candidate response, internal-position prose or chain-of-thought. Manual
semantic review confirmed that the presence reply did not offer advice, the challenge reply did
not automatically agree, and the uncertainty reply preserved the evidence boundary.

One background Stage 9 current-model formation call timed out while sharing the local Ollama
scheduler with a later foreground turn. The corresponding foreground conversation and Stage 10
cognition trace still committed successfully, and subsequent background work remained retryable.
This is recorded as target-host contention evidence, not as a cognition-pipeline failure. The
follow-up narrows default current-model formation to two proposals per owner and 512 output tokens,
with the same caps repeated in its provider policy; it requires no migration or foreground call.
The final local-Ollama smoke accepted one world claim under the new defaults with 137 output tokens
and 6.483 s provider total time. The rebuilt-wheel Foundation gate then completed with
`684 passed, 4 skipped`.

## Quality gate

- rebuilt the non-editable `satori-core` wheel;
- Ruff format/check and mypy: clean;
- deterministic pytest: `684 passed, 4 skipped`;
- correctly enabled Ollama integration suite: `688 passed`;
- isolated Alembic upgrade/bootstrap/activation reached `0008_user_world_models`;
- Stage 10 adds no migration or durable state owner.
