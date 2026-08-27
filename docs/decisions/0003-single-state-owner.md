# ADR-0003: Single writer-owner per state family

- Status: Accepted
- Date: 2026-07-26

## Context

If cognition, reflection, memory extraction and HTTP handlers can all write personality or relationship state, policies become bypassable and causality cannot be explained.

## Decision

Each persistent aggregate has exactly one domain owner that alone may approve writes. Other components read immutable views and submit typed proposals. The application unit of work performs physical commit but cannot override owner policy.

## Consequences

Mutation rules, tests and audit are centralized without creating one global god owner. Cross-domain workflows require explicit orchestration and expected aggregate versions. Ownership matrix in `../state-model.md` is normative.

## Alternatives rejected

Shared repositories with write access for all services; database triggers as domain policy; one universal StateManager.
