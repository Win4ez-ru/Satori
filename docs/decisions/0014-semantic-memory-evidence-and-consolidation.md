# ADR 0014: Semantic memory, evidence and consolidation

- Status: Accepted
- Date: 2026-07-30
- Supersedes: none
- Related: ADR 0005, ADR 0007, ADR 0012, ADR 0013

## Context

Stage 4 stores selective episodes and exact user-message evidence. Stage 5 retrieves those
episodes but intentionally does not turn repeated dialogue into durable knowledge. Stage 6 needs
stable user-subject knowledge without equating chat history, model output, inference, opinion,
preference or Satori's own beliefs.

Semantic aggregation increases both privacy impact and memory-poisoning risk. The generation
provider therefore cannot own semantic state, assign trusted confidence, manufacture provenance,
or write to persistence.

## Decision

Create a separate `SemanticMemoryManager` owner and four canonical record families:

1. typed semantic claim aggregates;
2. claim-to-episode-to-root-user-message evidence edges;
3. terminal, versioned formation decisions;
4. append-only claim revisions.

The v1 subject is only `user`. Predicates come from a closed small registry with explicit
single/multi cardinality. Values are typed as text, finite number or boolean; negation is a
separate polarity field. Structured identity is the normalized tuple of subject, predicate,
value kind, value, polarity, claim kind and normalization version. Display wording is never the
identity. A one-off observation remains an episodic memory and is not promoted as a separate
semantic claim kind.

`explicit_fact`, `inferred_fact`, `hypothesis`, and `attributed_statement` remain distinct for the full history
of a claim. An explicit statement does not become a Satori belief. An inferred claim is never
silently relabelled explicit: later explicit evidence creates a distinct aggregate and supersedes
the inference historically.

Formation uses the replaceable `StructuredGenerationPort`. Its bounded typed output is an
untrusted proposal. The application supplies the new source episode plus a small recent evidence
window. Every proposed claim must cite the new source memory. The owner accepts only evidence
that resolves through:

`SemanticClaim -> SemanticClaimEvidence -> EpisodicMemory -> MemoryEvidence -> user Message -> Interaction`.

Assistant messages, retrieved repetitions and semantic claims themselves are never new evidence.
Root evidence is deduplicated by source user message; inference/hypothesis additionally require at least two
root messages from at least two interactions. Reprocessing the same source/version returns one
terminal decision and cannot increase confidence.

## Confidence policy v1

Provider confidence is only an upper input; deterministic evidence caps are authoritative:

- explicit fact: `min(proposal, min(0.90 + 0.02 × (independent roots − 1), 0.96))`;
- attributed statement: `min(proposal, min(0.85 + 0.02 × (independent roots − 1), 0.91))`;
- inferred fact (minimum two independent interactions):
  `min(proposal, min(0.65 + 0.07 × (independent roots − 2), 0.79))`.
- hypothesis (minimum two independent interactions):
  `min(proposal, min(0.50 + 0.05 × (independent roots − 2), 0.65))`.

When genuinely new independent evidence merges into an existing exact identity, confidence is the
maximum of the prior confidence and the newly capped proposal confidence. Duplicate roots are a
no-op, so retries and repetition cannot inflate it.

## Conflict and time policy v1

- Exact active structured identity merges only new root evidence.
- Multiple values may coexist only for registry predicates declared multi-valued.
- A newer explicit/attributed value for a single-valued predicate supersedes incompatible active
  values; direct `corrects_claim_id` is explicit-only and must target the same active predicate.
- Explicit evidence supersedes a compatible inference/hypothesis rather than erasing its origin.
- An inference/hypothesis cannot override a compatible or conflicting explicit/attributed claim;
  an inference may supersede a compatible weaker hypothesis.
- Competing single-valued inference-like claims become disputed and are excluded from active recall.
- `valid_from` defaults to the latest supporting observation. Supersession closes the old interval
  at the new claim's `valid_from`; all old evidence and revisions remain.

## Retrieval and trust

Stage 6 does not add another vector index. Semantic recall is a separate bounded projection of
active claims whose evidence episodes were already selected by Stage 5. It is injected in its own
explicitly untrusted context section. Only claim IDs that were present in that context pass the
existing response grounding gate.

## Processing and failure semantics

The downstream order is history, episode, embedding attempt, semantic formation. Failures never
roll back upstream canonical state. A missing terminal semantic decision remains retryable.
Backfill scans source episodes in deterministic occurred-at/ID order and processes only missing
source/version keys. Provider calls happen outside DB transactions; decision, claim mutations,
evidence, revisions and audit commit atomically.

## Consequences

- Precision is intentionally favoured over recall; zero claims is normal.
- Unknown predicates and insufficient inference evidence are rejected.
- Semantic aggregates expose stable sensitive information, so ordinary logs contain IDs, counts,
  versions and timings only. Values and quotes appear only in explicit local read/inspect output.
- The v1 recent evidence window is bounded but not a general relevance graph. Broader world
  knowledge, user modelling, preferences/beliefs as owned domains, autonomous reflection,
  relationship state and emotions remain out of scope.
