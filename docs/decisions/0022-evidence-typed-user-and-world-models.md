# ADR 0022: Evidence-typed user and world models

- Status: Accepted
- Date: 2026-08-22
- Related: ADR 0003, ADR 0004, ADR 0005, ADR 0012, ADR 0014, ADR 0020, ADR 0021

## Context

Semantic memory answers what durable statements can be reconstructed from past episodes. It does
not answer which user goal or project is still current, whether an active situation has expired,
or which reported outcome superseded an earlier expectation. Relationship state answers how
Satori's stance toward one counterparty has changed; it must not become a profile of that person.

Stage 9 therefore needs a small revisable current model without duplicating all semantic memory,
treating one user's report as external world truth, or creating a generic entity graph. Its
claims are sensitive plaintext derived from dialogue, so stale context, cross-person leakage and
surveillance-like over-modeling are first-class failure modes.

## Decision

### Separate owners and claim families

Create two independent owners in the `models` boundary:

- `UserModelManager` owns counterparty claims about the counterparty's identity context, goals,
  projects and explicitly important people;
- `WorldModelManager` owns counterparty-relative claims about current projects, situations,
  commitments and pending outcomes reported in that counterparty's canonical dialogue.

Both families are keyed by `(satori_identity_id, counterparty_id)` and have separate repositories,
tables, aggregate versions and mutation methods. A shared application coordinator may evaluate one
bounded formation response and commit both owner decisions in one physical transaction, but it
does not decide either domain mutation. No claim is global world truth in v1, and the configured
opaque counterparty ID remains structural isolation rather than authentication.

V1 uses closed registries rather than arbitrary predicates or a graph:

- user predicates: `display_name`, `occupation`, `residence_city`, `goal`, `project`,
  `important_person`;
- world subject kinds and predicates: `project/status`, `situation/status`,
  `commitment/status`, `outcome/status`.

World status values are closed per subject kind. The project lifecycle includes the required
`planned`, `active`, `paused`, `completed` and `cancelled` values. The model stores only a bounded
normalized label for a subject; it does not create biographies, contact details, demographics,
health classifications or inferred psychological traits.

### Evidence and epistemic kinds

Every accepted edge resolves directly to a canonical completed user message and interaction for
the same identity/counterparty. Assistant output, affect, relationship state, retrieved memory,
semantic claims and provider output are not new evidence. Source roots are deduplicated by user
message, and one source interaction plus formation version has one terminal decision.

`explicit_fact`, `inference` and `hypothesis` never change kind in place. For world claims,
`explicit_fact` means “explicitly reported by this counterparty”; it is not independently verified
external truth. Provider confidence is only an upper input. One explicit root is sufficient with
a `0.90` cap. Inference requires two messages from two interactions and is capped at `0.70`;
hypothesis remains capped at `0.50` and cannot override an explicit claim. Later stronger evidence
creates or supersedes a distinct aggregate while preserving the weaker claim and its provenance.

A replaceable structured formation capability receives only the current canonical user message
plus a bounded same-counterparty window of canonical user messages and opaque source handles. It
may return zero proposals. Values/labels must be supported by cited user text, source handles must
be a subset of the supplied window, and every accepted proposal must cite the current source.
Provider calls happen outside transactions and have no repository capability. Formation runs
post-response, affects future turns only and reuses the existing derived inference scheduling
class rather than changing foreground priority policy.

### Validity, correction, conflict and expiry

Each claim stores `valid_from`, `valid_until`, `last_observed_at`, optional `expires_at`, status and
supersession link. Registry policy assigns deterministic context-freshness windows:

- `display_name` has no time expiry and changes only by explicit correction;
- `occupation`, `residence_city`, `goal`, `project` and `important_person` are reviewable for 180
  days after their last independent supporting observation;
- open project/commitment/outcome states are reviewable for 90 days;
- an active situation is reviewable for 30 days;
- terminal world states remain eligible for 365 days as latest-known history.

These windows control current-model eligibility; they do not physically delete evidence. A read
at or after `expires_at` materializes the claim as stale even if maintenance has not run. The
deterministic owner expiry use case may then persist `expired` plus audit without an LLM. Repeated
independent support of the exact identity extends `last_observed_at` and recomputes expiry.

An explicit `corrects_claim_id` must target a current same-owner, same-counterparty,
same-subject/predicate claim. New explicit single-valued or world-status evidence supersedes the
old value and closes its validity interval. Compatible exact evidence merges without changing
kind. Inference/hypothesis cannot override explicit evidence; competing inference-like values
become disputed. Superseded, disputed, retracted and expired rows remain available through
history/inspect/export.

### Projection, grounding, privacy and retention

Conversation receives a separate bounded untrusted user/world context envelope. It contains claim
IDs, subject kind/label, predicate, typed value/status, epistemic kind, confidence and freshness,
never evidence text. Only current, non-expired same-counterparty claims are eligible. Selection is
deterministic and topic-relevant, with a small fallback only when one unambiguous active project or
situation matches the user's category-level question. Epistemic labels are preserved verbatim.

Normal logs expose IDs, counts, versions, reason codes and timings only. Local inspect/export may
show values and provenance. Canonical message foreign keys use restrictive retention in v1: a
source cannot be physically deleted while a live or historical model edge references it. A future
erasure workflow must retract/remove dependent claims and retain only non-content tombstone
integrity before deleting roots; Stage 9 does not invent that production policy.

Export is counterparty-partitioned, includes current and historical claims plus source IDs and
policy/schema versions, and rejects orphan provenance on import/round-trip validation. It is a
read artifact, not a second source of truth.

## Consequences

- Current user/world understanding is explicitly separate from episodic/semantic memory and from
  relationship stance.
- A changing project becomes a non-destructive sequence such as
  `planned -> active -> completed`; old states remain inspectable and cannot re-enter current
  context.
- Time passage can make a claim stale deterministically but cannot fabricate a replacement fact.
- Precision and privacy are favoured over breadth: unknown predicates, unsupported labels and
  over-detailed person profiles are rejected, and zero claims is normal.
- V1 still has no authenticated multi-user routing, external verification, web truth, tools,
  Satori beliefs or unfinished-thread initiative.
