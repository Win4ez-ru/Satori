# ADR-0008: Local-first transactional canonical state

- Status: Accepted
- Date: 2026-07-26

## Context

Identity, relationships and memories are sensitive and mutually consistent. v0.1 requires restart survival, atomic state/audit changes and simple export. Cloud-first or multiple stores would expand privacy and partial-failure risk.

## Decision

Canonical state is local and transactional, with SQLite as baseline persistence for the modular monolith. Cloud receives only operation-scoped minimum context. Derived embeddings/indexes may use another local mechanism later but are rebuildable. Interaction finalization commits records, accepted mutations and audit atomically before non-streaming response delivery.

## Consequences

Offline ownership, backup/export and integrity tests are straightforward. Multi-device sync and horizontal writes are deferred. SQLite vector strategy, encryption/key management and retention require later measured decisions.

## Alternatives rejected

Provider-hosted conversation as truth; cloud database by default; unrelated polyglot stores before scale.
