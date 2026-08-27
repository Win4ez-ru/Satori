# Stage 7.7 inference performance report

Date: 2026-08-09. Host: MacBook Air M2, 8 CPU cores (4 performance + 4 efficiency), 10 GPU
cores, 8 GB unified memory, macOS, local Ollama/Metal. These numbers are target-machine evidence,
not portable latency guarantees.

## Method

`satori benchmark inference` creates an isolated activated database, one long-lived runtime and a
separate explicit session for every versioned scenario. It runs one warmup/cold observation and
five measured warm turns, then reports min/median/p90/max/mean. With five measured samples p90 is
the nearest-rank maximum. A deterministic grounded provider forms the recall source through the
real `MemoryManager`/Unit of Work/index path; a retrieval probe must select it before any recall
sample is accepted. Recall runs last so its fixture cannot add retrieval work to other scenarios,
and its derived preparation is reported separately.

The report contains scenario/run IDs, configured capability models, phase durations, Ollama load,
prompt-eval and eval durations/counts, and calculated token throughput. It never contains fixture
text, prompts, retrieved context, replies or private state. `satori benchmark appraisal` runs the
versioned ten-case semantic corpus; `satori benchmark contention` measures foreground-only and
episode/semantic overlap with and without the scheduler.

Representative commands:

```bash
uv run --no-sync satori benchmark inference --repetitions 5 --output /tmp/stage77.json
uv run --no-sync satori benchmark appraisal --model qwen3:4b-instruct --repetitions 3
uv run --no-sync satori benchmark contention --repetitions 3
uv run --no-sync satori benchmark contention --scheduled --repetitions 3
```

## Baseline diagnosis

Before Stage 7.7, Qwen 4B appraisal produced about 98 tokens for neutral turns and 177–178 for
meaningful turns. Warm `load_duration` was usually only 0.14–0.43 seconds; prompt processing and
structured output generation dominated. Python request building, grounding and canonical SQLite
commit were milliseconds. A foreground-only control also produced a 44.2-second identity outlier,
so contention was an amplifier rather than the only cause.

| Scenario | Baseline appraisal median/p90/max | Baseline generation median/p90/max | Baseline committed median/p90/max |
|---|---:|---:|---:|
| Social greeting | 10.303 / 14.281 / 14.281 s | 1.838 / 2.110 / 2.110 s | 12.444 / 15.294 / 15.294 s |
| Social check-in | 11.261 / 15.761 / 15.761 s | 2.551 / 3.113 / 3.113 s | 12.441 / 18.910 / 18.910 s |
| Personal identity | 20.550 / 32.663 / 32.663 s | 10.373 / 11.388 / 11.388 s | 30.971 / 44.195 / 44.195 s |
| Distress | 17.964 / 29.425 / 29.425 s | 3.133 / 4.603 / 4.603 s | 21.139 / 32.751 / 32.751 s |
| Positive progress | 20.771 / 27.662 / 27.662 s | 2.708 / 5.147 / 5.147 s | 23.847 / 32.843 / 32.843 s |
| Intellectual freedom | 15.249 / 17.973 / 17.973 s | 9.757 / 12.813 / 12.813 s | 25.498 / 28.103 / 28.103 s |
| Technical identity | 9.477 / 18.381 / 18.381 s | 4.949 / 8.309 / 8.309 s | 15.621 / 26.739 / 26.739 s |

A stopped-model greeting measured 18.325 seconds to committed reply: appraisal was 12.127
seconds (4.249 load, 2.862 prompt evaluation for 591 tokens, 4.938 output evaluation for 99
tokens), generation was 6.151 seconds. The next warm turn was 9.205 seconds: appraisal 7.592 and
generation 1.582 seconds, with only 0.160 seconds of appraisal load.

## Contention experiment

The same controlled foreground call was measured three times. Without scheduling, overlap with
episode inference raised median latency from 1.412 to 5.334 seconds (3.8×); semantic overlap raised
it to 2.423 seconds (1.7×). With serialization, priority and a two-second background grace, the
medians were 1.435 seconds foreground-only, 1.486 seconds with queued episode work and 1.431
seconds with queued semantic work.

| Case | Before scheduler median/p90/max | After scheduler median/p90/max |
|---|---:|---:|
| Foreground only | 1.412 / 1.422 / 1.422 s | 1.435 / 1.446 / 1.446 s |
| Episode overlap | 5.334 / 5.908 / 5.908 s | 1.486 / 1.504 / 1.504 s |
| Semantic overlap | 2.423 / 2.886 / 2.886 s | 1.431 / 1.433 / 1.433 s |

The scheduler cannot preempt a provider request that has already passed its grace period and begun;
the next foreground turn waits for that one atomic call. It does prevent subsequent derived calls
from overtaking queued foreground work, and 30-second aging prevents permanent background
starvation.

## Appraisal experiments

The accepted categorical wire emits one to three typed categories, confidence `0..100` and
bounded provenance handles. Infrastructure maps this to the unchanged continuous application
proposal; the domain still applies all mutation policy. The final isolated 4B corpus produced a
median 21 output tokens instead of 98–178.

| Model | Local size | Warm samples | Schema valid | Semantic pass | Median/p90/max wall | Decision |
|---|---:|---:|---:|---:|---:|---|
| `qwen3:4b-instruct` | 2.5 GB local / 3.2 GB resident | 30 | 100% | 80% | 0.814 / 0.835 / 0.858 s | Accepted default |
| `qwen3:0.6b` | 522 MB download | 20 | 90% | 20% | 0.401 / 0.420 / 0.453 s | Rejected |
| `qwen2.5:1.5b-instruct` | 986 MB download | 20 | 100% | 50% | 0.509 / 0.524 / 0.625 s | Rejected |

The 4B semantic misses were the joke and explicit-uncertainty fixtures, each mapped to curiosity in
three repetitions. Loss, distress, conflict, positive progress, neutral social, intellectual and
praise/farewell directions passed. This is a known calibration limitation, not hidden behind exact
float matching. The smaller candidates were not accepted despite lower memory footprint because
schema validity alone is insufficient.

For the isolated accepted-model run, median wall was 0.814 seconds: load metadata 0.106 seconds,
prompt evaluation 0.040 seconds for 611 tokens, and output evaluation 0.651 seconds for 21 tokens.
In the full application the request had 605–821 prompt tokens and typically took 1.9–4.0 seconds
to evaluate under the long-run memory pressure described below.

No appraisal gate is deployed. Therefore false-skip rate is exactly zero and full-appraisal rate
is 100%; a skip-latency saving is not claimed. Combined inference and post-turn appraisal were
also rejected because they would change same-turn affect semantics.

## Final warm benchmark

The accepted final run used one runtime, explicit bounded session continuity and five measured
warm turns per scenario. Runtime preparation was 74.279 ms. Recall preparation completed
separately in 6.514 seconds with one grounded episode, one indexed vector, one successful probe and
no failed phase. Every recall sample reported `retrieved` and one memory; warm retrieval embedding
and search/ranking medians were 158.819 ms and 3.936 ms. Derived processing was deliberately
excluded from committed-reply timings because Stage 7.5 delivers immediately after canonical
reply/affect commit.

| Scenario | Appraisal median/p90/max | Generation median/p90/max | Committed median/p90/max |
|---|---:|---:|---:|
| Social greeting | 2.118 / 2.167 / 2.167 s | 1.177 / 1.653 / 1.653 s | 3.338 / 3.780 / 3.780 s |
| Social check-in | 1.984 / 2.031 / 2.031 s | 1.131 / 1.166 / 1.166 s | 3.103 / 3.189 / 3.189 s |
| Personal identity | 2.212 / 2.287 / 2.287 s | 3.880 / 4.505 / 4.505 s | 6.099 / 6.833 / 6.833 s |
| Distress | 2.317 / 2.398 / 2.398 s | 2.075 / 2.153 / 2.153 s | 4.442 / 4.504 / 4.504 s |
| Positive progress | 2.517 / 2.583 / 2.583 s | 1.878 / 2.266 / 2.266 s | 4.410 / 4.808 / 4.808 s |
| Project recall | 3.736 / 3.958 / 3.958 s | 3.293 / 3.779 / 3.779 s | 7.264 / 7.935 / 7.935 s |
| Intellectual freedom | 2.639 / 2.809 / 2.809 s | 4.692 / 5.620 / 5.620 s | 7.358 / 8.212 / 8.212 s |
| Technical identity | 2.857 / 3.050 / 3.050 s | 6.496 / 7.669 / 7.669 s | 9.349 / 10.731 / 10.731 s |

The separate greeting after explicitly stopping Qwen took 7.510 seconds committed: appraisal
3.553 seconds and generation 3.933 seconds. Appraisal metadata was 0.962 seconds load, 1.892
seconds prompt evaluation (605 tokens) and 0.679 seconds output evaluation (22 tokens). Because
appraisal loads the shared 4B model first, generation then reported only 0.106 seconds load;
generation prompt evaluation was 3.579 seconds (1109 tokens) and output evaluation 0.224 seconds
(8 tokens). The subsequent five greeting turns had a 3.058-second median in that dedicated run.

Compared with baseline, committed median improved 73% for greeting, 75% for check-in, 80% for
identity, 79% for distress, 82% for positive progress, 71% for the intellectual turn and 40% for
technical identity. Appraisal median improved 70–89%. A valid before/after recall ratio is not
claimed because the baseline artifact did not prove that memory had actually been retrieved.
There were no unexplained 40–70-second warm outliers; the largest measured committed reply was
10.731 seconds.

## Runtime and hardware interpretation

`ollama ps` reported both Qwen 4B (about 3.2 GB) and `embeddinggemma:300m` (about 673 MB) at 100%
GPU with the Metal backend and 4096 context for Qwen. No CPU fallback was observed. The host had
about 5.2–5.6 GB swap in use and compressed memory during the long runs, so the worsening prompt
throughput later in the benchmark is consistent with unified-memory pressure. This is an
inference from system and Ollama diagnostics, not a proof of a single causal mechanism.

Warm full-application appraisal output throughput was approximately 24–32 tokens/s. Prompt
throughput ranged roughly 303–540 tokens/s in the slower late scenarios, while short early
generation prompts reached over 2000 tokens/s. Technical generation fell to about 22–24 output
tokens/s in the earlier run and 26–30 tokens/s in the final run, with 353–553 prompt tokens/s.
Application appraisal request building was 0.04–0.19 ms and response parsing was normally below
16 ms; HTTP roundtrip covered nearly the entire appraisal wall time.

macOS exposed no reliable one-shot thermal-throttling flag through the available safe diagnostics;
`pmset thermlog` is a streaming diagnostic and was not used as evidence. No thermal claim is made.
Ordering and warmups are recorded, but a long 8 GB run still combines cache, pressure and possible
temperature effects. No OS, kernel, GPU-offload or persistent system tuning was performed.

## Remaining limits

- Appraisal remains a separate 4B inference so the current event still affects the same reply.
- Complex intellectual/technical generation, not appraisal, is now the main foreground cost.
- Qwen 4B conversation throughput degrades under long-run 8 GB memory pressure; the technical
  median is within the 15-second goal but above the 8-second simple target by design category.
- Grounded recall adds about 0.16 seconds of embedding plus ranking and also increases appraisal/
  generation prompt work; its 7.264-second median is valid retrieval evidence, unlike the earlier
  empty-retrieval timing.
- The accepted appraisal corpus is 80%, not perfect; humor/uncertainty calibration needs more
  evidence before a mapping or model change.
- A background call already in flight cannot be safely preempted. Process-kill recovery still uses
  Stage 7.5 idempotent backfill rather than durable queue infrastructure.
- A future conversation-model or quantization comparison must repeat identity, memory, grounding
  and Russian behavior evaluation. Stage 7.7 does not change that default on ambiguous evidence.

The mandatory character rerun initially found unsupported closeness wording and one false claim
that affect did not enter answers. Context schema v9 refined only the existing late reminder and
kept behavior policy v7 and provider output unchanged; only the two relationship modes gained
48/56-token bounds at temperature zero. Final performance evidence uses schema v9 for the affected
technical scenario; relationship wording is outside the inference
timing corpus but passed the final three-session semantic regression. The final installed-wheel
five-turn chat produced committed latencies of 3.336–6.520 seconds and demonstrated immediate
canonical recent-role recall with no derived memory.
