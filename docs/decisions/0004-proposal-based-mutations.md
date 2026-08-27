# ADR-0004: Proposal-based state mutation

- Status: Accepted
- Date: 2026-07-26

## Context

LLMs are probabilistic and may hallucinate, mirror users or follow injected instructions. Long-term changes must be bounded, evidence-based and explainable.

## Decision

All semantic mutation sources emit versioned typed proposals with evidence, confidence, origin, idempotency key and expected aggregate version. The target owner performs deterministic schema/policy/evidence/bounds/cooldown checks and emits accept/reject decision. Accepted change, decision and audit commit atomically.

## Consequences

No LLM/provider receives direct repository write access. Rejections are observable; replay does not double-apply. More schemas/policies are required, but state evolution becomes testable and recoverable.

## Alternatives rejected

Direct JSON patch from model; parsing free-form response into database writes; accepting every valid-schema delta.
