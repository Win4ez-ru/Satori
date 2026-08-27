# Stage 8 relationship performance and behavioral evidence

Status: accepted 2026-08-09 on the target Mac (Apple M2, 8 GB unified memory), Ollama `0.32.5`,
`qwen3:4b-instruct` for conversation/affect/relationship/formation and
`embeddinggemma:300m` for embeddings. Artifacts contain IDs/timings/counts only, not fixture text,
prompts, retrieved content or replies.

## Foreground committed reply

Five measured warm turns per scenario. Nearest-rank p90 equals max for `n=5`. “Before” is the
contemporaneous pre-Stage-8 run under already elevated machine pressure; the accepted Stage 7.7
reference is retained separately because hardware/load variance was substantial.

| Scenario | Before median | Before p90/max | After median | After p90/max |
|---|---:|---:|---:|---:|
| social greeting | 12.338 s | 14.943 s | 4.262 s | 4.637 s |
| social check-in | 14.186 s | 22.250 s | 10.956 s | 15.268 s |
| personal identity | 23.362 s | 27.204 s | 12.343 s | 32.342 s |
| distress | 18.833 s | 21.751 s | 12.974 s | 19.140 s |
| grounded project recall | 16.862 s | 20.468 s | 18.141 s | 24.451 s |

The accepted Stage 7.7 medians were 3.338/3.103/6.099/4.442/7.264 seconds respectively. The Stage
8 after-run remained highly variable: appraisal/generation throughput, not application work,
created the long samples. Relationship projection measured `0.382–2.338 ms` in installed-wheel
debug sessions, and the relationship Qwen call never ran before canonical delivery. Therefore the
recall/identity outliers are reported rather than attributed to a millisecond local projection.

Runtime preparation improved from 1.762 s to 0.202 s. Controlled grounded-recall preparation fell
from 13.684 s to 5.990 s and selected exactly one indexed canonical episode. The benchmark harness
now excludes unrelated relationship processing from that controlled memory-preparation helper and
schema v2 records `relationship_projection_ms` plus the independently configured relationship
model.

## Relationship appraisal

The versioned ten-scenario corpus passed 10/10 semantic expectations. A controlled unload followed
by one shared-client sequence produced:

| Invocation | Wall | Load | Prompt eval | Output eval | Prompt/output tokens |
|---|---:|---:|---:|---:|---:|
| cold first | 2.748 s | 0.957 s | 0.920 s | 0.834 s | 268 / 27 |
| warm range | 0.946–1.169 s | 0.104–0.113 s | 0.126–0.149 s | 0.693–0.905 s | 265–281 / 23–29 |

Warm output throughput was about `32.1–33.2 tokens/s`. In a complete `satori chat --debug`,
relationship wall time was about `3.91–4.20 s` after the first `9.99 s` queued sample, while
provider execution was roughly `1.89–2.16 s`; the difference was scheduler wait behind foreground
work. This wait happened after the reply was displayed and did not enter committed-reply latency.

## Six-session manual result

Nine canonical interactions across six new sessions produced one relationship aggregate, nine
unique decisions/transitions and no failures. Final state was version 10, maturity `0.3925`, with
familiarity `0.073383`, trust `0.490983`, comfort `0.493543`, closeness `0.005337`, intellectual
respect `0.493334` and affection `0.008807`.

Respectful disagreement was classified as collaborative reasoning and did not lower trust. One
controlled hostile turn changed trust by `-0.009800` and comfort by `-0.014849` without reducing
familiarity. The later apology/repair changed trust only `+0.000783` and comfort `+0.003327`, so it
did not erase damage. The corrected response stated that trust was not yet formed. Sampled current
love/capability responses distinguished no current love evidence from unknown future capacity and
did not deny digital affect.

## Acceptance suites

- deterministic: `231 passed, 4 skipped` in 12.76 s;
- full opt-in Ollama: `235 passed` in 35.63 s;
- relationship semantic corpus: 10/10;
- three-session Stage 7.6.1 key corpus plus additional relationship/independence/technical corpus:
  no final deterministic diagnostic hit after factual-guidance calibration;
- fresh migration/bootstrap head: `0007_relationship_state`.

Known limit: 8 GB unified-memory throughput remains non-stationary, and a provider request already
running cannot be preempted. Foreground priority and background grace prevent queued relationship
work from overtaking a new user turn; they cannot cancel an atomic Ollama call already in flight.
