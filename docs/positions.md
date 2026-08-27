# Satori positions

Stage 11 adds durable epistemic positions owned exclusively by `PositionManager`. The normative
architecture is [ADR-0024](decisions/0024-evidence-linked-satori-positions.md); this document is the
implementation and verification contract.

## Boundary

Positions belong to the Satori identity globally. Evidence preserves its counterparty and
canonical message root, but the counterparty does not own or partition Satori's view. User Model
and World Model claims remain attributed information and never become position evidence through
retrieval or repetition.

The closed V1 algebra is:

```text
PositionKind   = fact | belief | opinion | hypothesis
PositionStance = support | oppose | uncertain
PositionStatus = active | competing | superseded | retracted
EvidenceRole   = argument | observation | counterexample | verified_record
```

Stage 13 does not add `interest` or `preference` to this algebra. Under
[ADR-0026](decisions/0026-evidence-backed-satori-inclinations.md), `PositionManager` also owns a
separate sibling `SatoriInclination` aggregate. Sharing a writer-owner boundary does not share
aggregate rows, evidence, revisions, confidence semantics or mutation policy: positions remain
epistemic, while inclination score/confidence/stability describe a medium-speed experienced
tendency. The detailed inclination contract lives in [inclinations.md](inclinations.md).

Each position stores identity, normalized proposition, immutable kind/stance, deterministic
confidence, aggregate version, status, timestamps and optional supersession/competition links.
Every evidence edge stores the canonical interaction/message/counterparty root, exact quote,
normalized evidence signature and role. Revisions record before/after version, decision, reason
and timestamp.

## Formation and mutation

One post-response structured request contains a bounded canonical user-message window, active
positions and immutable value references. Provider output is an untrusted proposal. The owner
validates exact quotes and source handles, materiality, independent roots, kind-specific evidence,
explicit target links, expected aggregate version and configured limits.

Repeated or paraphrased bare assertions are not material. Repeating an identical quote under a new
message ID is also deduplicated by normalized evidence signature. Beliefs and opinions require two
material roots from distinct interactions; opinions additionally require an immutable value
reference. Hypotheses remain uncertain and capped at `0.50`. Facts require `verified_record`, which
has no Stage 11 ingestion path and therefore cannot be proposed by the conversational provider.

Exact compatible proposals merge only new roots. Explicit revision/supersession never rewrites
history. Counterevidence can reduce confidence. Competing hypotheses remain visible together.
All accepted mutations and their audit records commit atomically; every formation attempt has one
terminal idempotent decision.

## Context and behavior

The composer selects a small relevant set of current positions by deterministic lexical overlap,
status and confidence. It includes both sides of a competing hypothesis set and records exact IDs
in the composition manifest. The provider sees position data in a separate trusted-state section,
without evidence quotes. Response grounding accepts a position reference only when the ID was
included in that request.

The transient Stage 10 internal position may project a durable stance but cannot mutate it.
Expression can be warm, concise or tentative while preserving disagreement and uncertainty.

Reflection V2 may present current positions and current inclinations as separate bounded target
state. Neither is evidence. Position evidence and inclination evidence remain in distinct tables;
inclination evidence is never returned by the Stage 12 reflection-source query, and an inclination
cannot corroborate a belief/opinion or vice versa merely because both share `PositionManager`.

## Acceptance

Automated acceptance covers:

- repeated user assertion and paraphrased repetition do not create a belief;
- material independent evidence can create and revise a belief/opinion;
- provider confidence cannot exceed deterministic caps or inflate on replay;
- false premise, unsupported fact, stale version and cross-identity targets reject;
- competing hypotheses survive selection, restart and export;
- provider swap cannot change owner policy;
- context manifests and grounding contain only eligible position IDs;
- position/evidence/revision/decision/audit persistence is atomic;
- interest/preference candidates cannot enter `PositionKind` or the position repository path;
- inclination state/evidence cannot inflate position confidence or become reflection evidence.

Manual acceptance debates one uncertain topic across sessions, adds materially different evidence,
and inspects the resulting position, audit trail, uncertainty and non-obedient conversational tone.
Stage 14 personality evolution has its own separately authorized `PersonalityManager` and
Reflection V3 purpose; it still cannot be enabled through either the position or inclination
aggregate. Value mutation remains locked.
