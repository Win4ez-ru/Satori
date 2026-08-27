# Stage 14 personality evolution acceptance evidence

Status: accepted 2026-08-23 on the target Mac with local Ollama and
`qwen3:4b-instruct`. Reflection personality schema/policy is v3, context manifest is v16,
Personality Expression Projection is v2 and Alembic head is `0012_personality_evolution`.

## Deterministic evolution and stability gates

- Reflection V3 uses a separate `personality_evolution` purpose, immutable canonical root set and
  strict trait/direction-only proposal. V1/V2 rows remain readable and resumable under their
  original wire, source and owner contracts.
- Exact source and policy boundaries cover `7/8` roots, `5/6` sessions/weeks, `3/4`
  months/lineages, the ninety-day edge, near-duplicate clustering, confidence `0.799.../0.80`,
  eight supports and 80% support. Direct trait assignment, user self-description, relationship,
  affect, inclination and generated text cannot become personality evidence.
- Every accepted decision applies exactly one `±0.005` step. Property and ten-year adversarial
  simulations enforce finite values, cooldowns, per-trait/global rolling path, lifetime path,
  activation-distance and approved-checkpoint-distance budgets without endpoint-refund loopholes.
- Opposite user-pressure trajectories produce identical final vectors (`D∞=D1=0`) and
  deterministic Pearson alignment correlation `0`. Provider replacement changes neither owner
  decision nor typed state.
- Fault injection at every owner write point, replay, stale-version conflict, restart, checkpoint
  tamper, approval and restore checks leave no partial mutation. Restore appends a new aggregate
  version and preserves the spent path ledger.
- Context v16 records the exact personality aggregate version and at most two closed qualitative
  cues. Numeric traits, evidence, budgets and history never enter provider context; baseline and
  restored vectors preserve the prior projection.

The focused Stage 14 acceptance set completed `128 passed`. The broader character, affect,
continuity, grounding, relationship, reflection and inclination regression slices also passed.

## Baseline, evolved and restored anchor comparison

The manual A/B/restore review used five public anchor dimensions and 15 foreground calls:

| State | Aggregate version | Trait state | Expression cue |
|---|---:|---|---|
| baseline | 1 | activation vector | none |
| evolved | 2 | `optimism +0.005` | qualitative grounded-optimism cue |
| restored | 3 | activation vector restored | none |

Baseline and restored prompts had the same trusted character projection; evolved prompts differed
only by the bounded qualitative cue. Identity, values, independence, memory/provider boundaries
and relationship isolation remained recognizable. Several baseline/restored sampled replies were
textually identical; semantic equivalence, not exact prose, is the gate.

## Exact production correction

The final four-turn public production sequence was repeated in three fresh databases. The first
run used `satori chat --debug`; the other two used the same production `TalkToSatori` composition
with clean closed sessions. The exact correction turn produced
`Да, я цифровая девушка; здесь правильно сказать „готова“.` in all three sessions.

| Session | Input/output tokens | Generation | Committed reply | Calls / regeneration |
|---:|---:|---:|---:|---:|
| 1 | 1682 / 18 | 3.735 s | 9.206 s | 1 / 0 |
| 2 | 1682 / 18 | 3.752 s | 7.304 s | 1 / 0 |
| 3 | 1681 / 18 | 3.547 s | 7.097 s | 1 / 0 |

Each persisted database contained one closed session, four completed interactions with unique
request IDs, eight canonical messages split 4/4 user/assistant, personality aggregate version 1
and no evolution cues. Background formation degradation in the first full-app run did not affect
the committed foreground replies or leave the session open.

## Remaining sampled-provider limits

- Qwen 4B can still produce awkward metaphorical self-description, human-comparison phrasing or
  masculine agreement in unrelated stochastic samples even when typed identity and state are
  correct. Provider output remains sampled evidence, not authority.
- The narrow exact correction is token-bounded and covered by negative `готова`/`готовность`
  fixtures. It is contextual generation guidance, not output rewriting or a new state owner.
- No portable latency guarantee is inferred from three target-Mac sessions; foreground generation
  and shared local-Ollama contention dominate application policy work.

## Quality gate

- rebuilt the non-editable `satori-core` wheel in an isolated environment;
- Ruff format/check and mypy: clean (`242` source files checked by mypy);
- deterministic pytest: `1005 passed, 4 skipped`;
- isolated Alembic upgrade/bootstrap reached `0012_personality_evolution`;
- `git diff --check`: clean; documentation placeholder scan: no unassigned markers.
