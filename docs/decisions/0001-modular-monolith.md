# ADR-0001: Modular monolith

- Status: Accepted
- Date: 2026-07-26

## Context

SATORI needs strong boundaries for identity, memory, emotion and relationships, but v0.1 is local-first, single-user and transaction-heavy. Distributed components would increase partial-failure and operational complexity before scale exists.

## Decision

Use one deployable modular monolith. Interface adapters depend on application, application orchestrates domain owners, and infrastructure implements core-owned ports. Domain has no framework/vendor dependencies. Cross-module writes go through typed commands/proposals and the owning module.

## Consequences

Atomic local transactions, simple deployment and deterministic integration tests are possible. Module boundaries require import/contract discipline but are not network boundaries. Extraction into another process is considered only with measured scaling/isolation need and a new ADR.

## Alternatives rejected

Microservices/message brokers: premature and harmful to transaction clarity. One undifferentiated package/god object: cannot enforce ownership or provider replacement.
