# ADR 0023: Transient structured cognition pipeline

- Status: Accepted
- Date: 2026-08-22
- Related: ADR 0003, ADR 0004, ADR 0006, ADR 0007, ADR 0012, ADR 0015, ADR 0019, ADR 0021, ADR 0022

## Context

Stages 3–9 incrementally implemented canonical intake/finalize, bounded retrieval, appraisal and
affect, dialogue coherence, relationship projection and evidence-typed current models. Their
orchestration is source-correct but still exposes one large gap: perception, mixed user needs,
retrieval intent, internal position, response intent and response strategy are implicit in
request-building code or in one opaque conversational generation call.

Stage 10 must make that path typed and observable without storing raw chain-of-thought, creating
durable Satori beliefs before Stage 11, multiplying local foreground model calls, or moving
EmotionManager and other domain-owner policy into cognition. The target Mac has already shown
that additional serialized Qwen work directly increases committed-reply latency.

## Decision

### One transient pipeline and no new state owner

Add a `cognition` application boundary that transforms the current canonical user-message handle,
bounded transient dialogue signals and already-approved read projections into immutable,
versioned artifacts:

```text
perception → weighted need mix → retrieval plan
→ existing structured appraisal → EmotionManager handoff
→ concise internal position → intent tags → response strategy
→ generation → existing validation/grounding/finalize
```

Every artifact has a closed schema version, explicit status/owner, bounded values and canonical
source refs. The pipeline has no repository, Unit of Work, mutation API or background lifecycle.
It may emit typed proposals to an existing owner, but only that owner can accept and persist a
change. The pipeline trace is transient request observability and explicit local debug output; it
is not another source of self, user, world, relationship or memory state.

### Deterministic V1 planning with a replaceable port

V1 perception, need-mix classification, retrieval planning, position projection, intent selection
and response strategy are deterministic application policy behind a small provider-neutral planner
port. They use bounded lexical/dialogue signals and authoritative source handles; they do not make
claims about hidden user psychology. A future structured provider may implement the same port only
after separate latency and semantic evidence.

The existing structured affect appraisal remains the only semantic cognition provider call in the
foreground path. Its typed proposal and provider metadata are projected into the Stage 10
appraisal artifact; `EmotionManager` remains the sole writer and still decides the tentative
transition. No extra foreground LLM call is added merely to label already-available information.

Planner invalid output, exception or timeout produces one explicit conservative fallback trace:
current-input retrieval stays available, uncertainty is raised, intent becomes answer/listen with
truthful limits, and strategy forbids unsupported memory or false certainty. Fallback never writes
state and never silently disappears from metadata.

### Internal position and expression boundary

The V1 internal position is a short bounded summary plus stance, confidence, supporting-point
codes, concern codes and evidence refs. It is not a durable belief and cannot be cited later as
evidence. Response strategy carries the exact position stance and uncertainty requirement plus
intent tags, tone, verbosity, softness/humor bounds, point codes and `must_not_claim` codes.

Generation receives a compact trusted strategy instruction separated from untrusted user,
history, memory and model values. Strategy may soften expression or choose brevity, but cannot
reverse disagreement into agreement, remove material uncertainty, promote hypotheses, invent
memory or override safety/grounding. The application validates this invariant before the provider
call.

### Trace, privacy and latency

One `CognitionPipelineTrace` records artifact schema/status/owner/source refs, qualitative codes,
bounded weights, fallback reason and per-step timings. Normal logs expose only schema versions,
statuses, enums, counts, refs and timings. They never contain user text, prompt text, generated
candidate text, internal-position prose or raw chain-of-thought. `satori chat --debug` is the
explicit local viewer for the concise trace and similarly omits raw private reasoning.

The Stage 10 latency budget is application planning median below 10 ms and p90 below 25 ms in the
deterministic corpus, excluding the already-measured retrieval/appraisal/generation calls. A new
provider call, database table, broker or cache is not justified by Stage 10 V1.

## Consequences

- The full response-planning path becomes typed, testable and observable while domain ownership,
  grounding and atomic finalize remain unchanged.
- Mixed needs remain weighted rather than forced into one rigid intent, while the versioned tag
  registry can be extended deliberately.
- Deterministic heuristics are conservative and may miss nuance; uncertainty and fallback are
  explicit, and later provider replacement requires evidence rather than hidden coupling.
- Migration head remains `0008_user_world_models`. Stage 10 adds no durable belief, reflection,
  personality evolution, preference, proactivity or Stage 11 state.
