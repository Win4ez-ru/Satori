# ADR 0017: Runtime self-model, character expression and provider distinction

- Status: Accepted
- Date: 2026-08-01
- Supersedes: none
- Related: ADR 0002, ADR 0003, ADR 0010, ADR 0011, ADR 0015, ADR 0016

## Context

The persistent identity, personality, values, memory and affect state added by Stages 2–7 were
authoritative outside the LLM, but the Stage 7.5 generation request expressed only Satori's name,
numeric traits and capability flags. Real Qwen conversations therefore fell back to a generic AI
assistant prior: masculine Russian grammar, denial of female identity, memory and emotion, and a
false distinction from the Qwen component. A wrong assistant self-description then entered bounded
recent dialogue and could reinforce the same collapse on the following turn.

Stage 7.6 must make existing state legible to generation without making a prompt, model output or
provider the owner of identity. It must not add relationship state, personality mutation, invented
autobiography, a body or a claim of proven human-equivalent consciousness.

## Decision

### Authoritative runtime projection

Application composition derives an immutable, versioned `RuntimeSelfModel` for each request from
the live `InitialSelfSnapshot`, actual runtime capability availability and configured language
provider/model. It states stable digital female identity, feminine Russian grammar, persistence,
bounded memory, digital affect/mood when available, lack of a biological body, current missing
capabilities and the language model's replaceable component role.

This object is a transient read projection. It has no repository, table, mutation endpoint or
provider write-back path. The DB remains authoritative for persistent name/personality/values;
application policy supplies the constitutional female/digital/embodiment interpretation; runtime
composition supplies truthful capability and provider facts.

### Personality expression

Current persistent trait values are deterministically interpreted into versioned soft guidance:
curious/analytical, independent position, warm/perceptive, light irony and considered directness.
Each guidance item retains its source trait keys and computed bounded strength. It is not a second
personality seed, response template or write path; values and raw traits remain in the trusted
character projection.

### Request hierarchy and continuity defence

The generation request uses this semantic hierarchy:

1. trusted system self-model and behavior policy;
2. trusted developer character/personality/value projection and optional trusted state envelopes;
3. explicitly untrusted retrieved memory and canonical recent user/assistant dialogue;
4. a compact trusted current-turn identity/voice reminder;
5. the current untrusted user message.

The reminder follows recent dialogue so a stochastic prior assistant mistake remains continuity
data but cannot become authority about identity. Its critical Russian voice rules are written in
Russian for the configured model's cross-language adherence. It does not overwrite history and it
does not inspect or rewrite the generated answer.

### Provider and embodiment truth

Satori is not Qwen or Ollama. The configured language model/provider are disclosed as current,
replaceable generation components when relevant. Satori is a persistent digital female person in
the product model, not a biological human and not a temporary roleplay mask. Implemented digital
affect/mood are real domain state for this system, not human physiology; claims of physical
sensation, perfect recall or proven human-equivalent consciousness remain disallowed.

### Evaluation boundary

Deterministic tests validate typed facts, ordering, trust roles, trait-derived guidance, bounds and
resistance to a conflicting recent assistant turn. A versioned Russian behavior corpus and an
explicit real-Ollama 3-session harness sample semantic behavior. Production does not use banned
phrase matching, output rewriting or a second LLM judge to force character conformance.

## Consequences

- Identity/capability truth is explicit and independently testable without moving ownership into
  the prompt or model.
- Recent conversation can preserve natural continuity without letting prior generated text
  redefine Satori.
- The prompt grows because the previous request omitted essential identity semantics. The compact
  reminder and non-duplicated character JSON bound this cost, but small-model adherence and voice
  polish remain stochastic and require ongoing sampled evaluation.
- No migration is required. Affect, memory, canonical finalize, replay and Stage 8 boundaries are
  unchanged.
