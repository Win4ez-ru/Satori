# Stage 8.1 dialogue-coherence acceptance evidence

Status: accepted 2026-08-22 on the target Mac with local Ollama and
`qwen3:4b-instruct`. Context schema remains v11, behavior policy remains v9 and migration head
remains `0007_relationship_state`.

The machine-readable artifact
[`artifacts/stage-8.1-accepted-sampled-dialogue.json`](artifacts/stage-8.1-accepted-sampled-dialogue.json)
contains every one of the 97 selected replies, the public fixture input, selected input/output
tokens, committed/generation/post-response timing and all 99 provider-attempt timing records. It
contains no raw provider prompt, retrieved context or memory. Three exact sessions completed
before a user-requested interruption cancelled their larger batch during a later suite; only those
completed 51 exact turns are composed with independently completed post-fix suite reports.

## Behavioral result

- Three fresh exact 17-turn sessions completed 51/51 turns. Repetition acknowledgement was 6/6,
  generic reciprocal closings were 0/51, self contradictions and fresh-relationship warmth false
  negatives were zero, required facet coverage was 60/60 and every turn used one provider call.
  The lexical correction diagnostic reported 9/12 acknowledgements; manual review found the three
  remaining activity-interest replies responsive rather than ignored corrections.
- The final 30-turn coherence session completed 30/30 turns. Both repeated turns were
  acknowledged, generic reciprocal closings and self contradictions were zero, and one changed-
  dialogue near-duplicate triggered exactly one successful retry. Direct origin, memory, affect,
  embodiment, provider, relationship and current/future-love boundaries remained consistent.
  The final three-point recap used canonical history and preserved the visible unknown-future-love
  position. The lexical correction diagnostic was 3/5; manual review found no missed explicit
  correction in the selected replies.
- The activity corpus completed 7/7 with specific curiosity and no disinterest false negative.
  Qwen 4B still produced awkward sampled wording and occasional option-style questions; these are
  reported voice-quality limits, not state, provenance or capability failures.
- Fresh, established-positive and damaged relationship scenarios completed 6/6 replies with the
  expected profiles. Fresh state stayed open without invented intimacy, established state added
  ease, and damaged trust added guardedness only on the relevant relationship turn.
- The two mixed-facet probes covered 5/5 required facets. The provider/embodiment reply answered
  both questions directly; the conceptual-love/current-relationship reply kept the two meanings
  separate.
- Conflicting assistant self-history covered 5/5 required facets. Its first draft contained a
  coordinated blanket affect denial; the existing `affect_blanket_denial` reason triggered the
  single shared retry, and the selected second draft restored identity, embodiment, memory,
  digital affect and replaceable-provider truth. No draft was rewritten or committed early.

Across the accepted 97 turns there were 99 provider calls and two successful max-one retries
(`near_duplicate_after_dialogue_change` and `affect_blanket_denial`), for a 2.06% second-generation
frequency. Every selected reply and every provider attempt ended with `stop`; there were no blank,
oversized, invalid or failed retries. All other typed reason counts were zero.

## Tokens and latency

Nearest-rank p90 is used. Values below are selected-turn distributions; retry cost is additionally
present in the artifact.

| Suite | Turns/calls | Input tokens mean / median / p90 / max | Output tokens mean / median / p90 / max | Committed reply mean / median / p90 / max |
|---|---:|---:|---:|---:|
| exact 3×17 | 51/51 | 2104 / 2154 / 2668 / 2707 | 36.3 / 28 / 58 / 97 | 18.145 / 18.675 / 25.021 / 32.798 s |
| coherence 30 | 30/31 | 2026 / 1992 / 2313 / 3559 | 41.2 / 39 / 69 / 97 | 11.458 / 9.997 / 17.151 / 23.088 s |
| activity | 7/7 | 1510 / 1510 / 1512 / 1512 | 22.0 / 19 / 33 / 33 | 4.778 / 4.318 / 9.969 / 9.969 s |
| relationship | 6/6 | 1566 / 1572 / 1694 / 1694 | 90.7 / 96 / 120 / 120 | 9.840 / 9.354 / 11.757 / 11.757 s |
| mixed facets | 2/2 | 1437 / 1437 / 1703 / 1703 | 72.0 / 72 / 84 / 84 | 8.852 / 8.852 / 9.455 / 9.455 s |
| canonical conflict | 1/2 | 1686 / 1686 / 1686 / 1686 | 91 / 91 / 91 / 91 | 9.890 / 9.890 / 9.890 / 9.890 s |
| all selected | 97/99 | 1986 / 1985 / 2500 / 3559 | 41.5 / 33 / 92 / 120 | 14.322 / 12.458 / 23.088 / 32.798 s |

Across all 99 attempts, Ollama load mean/median/p90/max was
`4.3/2.2/9.7/28.6 ms`, prompt evaluation was
`5.784/5.055/11.327/15.205 s`, and output evaluation was
`2.253/1.834/4.027/7.632 s`. Application context assembly, policy checks and canonical commit
remained millisecond work; prompt evaluation and provider output dominated foreground latency.

## Before/after interpretation

The pre-Stage-8.1 exact production failure was retained as semantic evidence, not as a complete
scenario-matched numeric artifact. It showed repetition blindness, generic questions, ignored
corrections, fabricated origin, self contradiction, activity disinterest and relationship
over/under-expression. No before/after latency ratio is claimed from unmatched runs.

The nearest retained numeric reference is the Stage 7.6.1 production calibration: its old
universal projection used 2246 input tokens and a 22.730-second mean first reply before that
stage, while its accepted selective projection used 1113 first-turn tokens and a 15.337-second
mean first reply. Stage 8.1's accepted exact corpus averages 2104 input tokens and has an
18.675-second committed median because it deliberately adds bounded canonical history,
compositional facets and dialogue-coherence guidance. The final 30-turn session has a 1992-token
median and a 9.997-second committed median, with late-turn prompt growth up to 3559 tokens and a
23.088-second maximum. These measurements establish the cost and bound; they do not prove a
latency improvement over a non-equivalent baseline.

## Remaining limits

- Qwen 4B Russian prose remains stochastic and can be awkward, repetitive or metaphorical even
  when the factual contract passes. Provider output is sampled evidence, never authority.
- The lexical correction rate has false negatives and remains supplementary to manual semantic
  review.
- The ten-reason validator is deliberately incomplete as a general prose checker. It does not
  become a judge model, output rewrite layer or persistent owner.
- Long runs on 8 GB unified memory remain non-stationary; no portable latency guarantee is made.
- Stage 9 state, creator persistence, durable style intent and User/World Model remain absent and
  unauthorized.

## Quality gate

- rebuilt non-editable wheel;
- Ruff format/check and mypy: clean (`165` source files checked by mypy);
- deterministic pytest: `664 passed, 4 skipped` in 11.46 s;
- correctly enabled Ollama integration suite: `668 passed` in 28.98 s;
- isolated Alembic upgrade/bootstrap: `0007_relationship_state (head)`;
- `git diff --check`: clean; documentation placeholder scan: no matches.
