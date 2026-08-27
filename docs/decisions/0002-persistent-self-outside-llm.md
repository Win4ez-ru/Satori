# ADR-0002: Persistent self outside LLM

- Status: Accepted
- Date: 2026-07-26

## Context

Provider prompts and chat histories are ephemeral, vendor-specific and prone to drift. They cannot be the identity of a character expected to survive restarts and model replacement.

## Decision

Identity, personality, values, positions, relationships, emotion, self model and memory are canonical typed persistent state. Prompts are temporary, budgeted projections produced by Context Composer. LLM output is never canonical merely because it was generated.

## Consequences

Restart/export/provider replacement preserve Satori. Context assembly and schemas become explicit engineering responsibilities. State can be inspected, migrated, evaluated and audited independently of prose.

## Alternatives rejected

One large system prompt or provider conversation thread as personality: no reliable continuity, ownership, auditability or portability.
