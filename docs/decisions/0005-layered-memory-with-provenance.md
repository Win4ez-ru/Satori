# ADR-0005: Layered memory with provenance

- Status: Accepted
- Date: 2026-07-26

## Context

Raw chat alone cannot provide selective recall, consolidation, forgetting or epistemic honesty. A single vector collection would erase distinctions between events, knowledge, relationships and self narrative.

## Decision

Separate raw interaction log, episodic, semantic, relationship, self and autobiographical memory plus belief/preference evidence. Every durable claim retains provenance, confidence and source references. Retrieval is budgeted ranking over canonical records; embeddings/indexes are derived and replaceable.

## Consequences

False memories, contradictions and duplicates can be detected and evaluated. Storage/schema design is richer; consolidation and forgetting must preserve lineage. Exact vector technology remains open until Stage 5 measurements.

## Alternatives rejected

Whole chat in prompt; one undifferentiated vector store as truth; model-generated summaries without sources.
