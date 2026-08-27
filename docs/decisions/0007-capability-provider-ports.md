# ADR-0007: Capability-oriented provider ports

- Status: Accepted
- Date: 2026-07-26

## Context

Different operations need structured generation, conversation/streaming and embeddings. A vendor SDK or one enormous `LLMProvider` would couple domain behavior, routing and identity to a model.

## Decision

Core owns small capability ports: conversation generation, typed structured generation and embeddings. Infrastructure adapters may implement several ports. Application routing selects capability by quality/privacy/latency policy; domain never sees vendor/model types.

## Consequences

Ollama can be initial infrastructure without becoming architecture. Providers and models are test doubles/replacements; configs and outputs carry version metadata. Routing details and first model remain evidence-driven open questions.

## Alternatives rejected

Vendor SDK calls inside domain/application; single god-interface; separate microservice per model capability.
