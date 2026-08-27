# ADR 0018: Contextual self-expression and conversational disclosure

- Status: Accepted
- Date: 2026-08-01
- Supersedes: ADR 0017
- Related: ADR 0002, ADR 0011, ADR 0015, ADR 0016, ADR 0017

## Context

ADR 0017 made the complete runtime self-model visible to generation. The typed state was correct,
but a real four-turn Russian conversation showed that Qwen treated the universal capability matrix
and repeated negative boundaries as a ready biography. It used formal Russian, dumped architecture
on a personal question, denied implemented digital affect and converted absent Stage 8 state into
permanent inability to love. The previous phrase-only evaluator did not detect these semantic
failures.

The full `RuntimeSelfModel` must remain authoritative application knowledge, while an ordinary
conversation should reveal only the depth relevant to the current question. Fixing this by
rewriting generated text would hide provider failure and violate canonical delivery.

## Decision

### Full internal truth, contextual provider projection

The application continues to derive the complete immutable `RuntimeSelfModel`, all traits, values,
capabilities and source-linked personality-expression strengths. A deterministic typed disclosure
selector chooses only conversational depth: social/register correction, personal identity,
digital nature, memory, emotion, interests, independence, style calibration, technical identity,
consciousness, current relationship claim, relationship capability or general.

The selector is not an LLM intent router, state owner or user model. It neither persists a label nor
changes domain state. It controls which already-authoritative facts and soft voice guidance are
projected to the provider. Technical facts, embodiment limits and relationship absence are not
present in unrelated personal/social requests. Technical mode receives a compact factual
projection; relationship modes receive epistemically accurate wording without a relationship
aggregate.

### Natural expression contract

Behavior policy v7 and context schema v8 require informal Russian `ты`, feminine self-reference,
proportional length, independent character and digital-affect consistency. Per-mode output-token
bounds constrain verbosity. Factual boundary-sensitive modes use a lower temperature, while
ordinary conversation keeps the configured calibrated default. The default conversation
temperature is `0.3`, selected from repeated real-Qwen samples; it remains configurable.

Current affect is projected as a deterministic qualitative expression hint plus metadata versions,
not a numeric vector for the model to narrate. The authoritative numeric state, transition rules,
commit semantics and owner remain unchanged.

### Evaluation and delivery

The Russian behavior corpus v2 carries an explicit eleven-dimension manual rubric plus
deterministic diagnostic indicators. Exact phrase matching is supplementary and must preserve
negation. The real evaluator prints every raw response, latency, token count and Ollama timing
metadata; production uses neither a second judge LLM nor an output filter.

Provider text still passes only the existing structural/grounding gates and is committed unchanged.
No generated fragment is rewritten for identity or voice compliance.

## Consequences

- First-turn conversation prompt size is materially smaller while the application retains full
  typed self truth.
- Social and personal replies no longer receive unrelated model/body/relationship capability
  details; direct technical questions still do.
- The absence of relationship state is expressed as current epistemic incompleteness, never stored
  as trust, attachment, affection or love.
- Small-model wording remains stochastic and real samples remain necessary. Prompt adherence is
  evidence, not a transaction invariant.
- No migration, repository, persistent state owner, output rewrite or Stage 8 behavior is added.
