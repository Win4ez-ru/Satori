# ADR 0019: Local inference priority and categorical appraisal wire

- Status: Accepted
- Date: 2026-08-09
- Related: ADR 0003, ADR 0004, ADR 0007, ADR 0015, ADR 0016, ADR 0018

## Context

Stage 7.6.1 measurements showed that warm model loading was no longer the dominant cost. A user
turn still required two sequential Qwen 4B calls before canonical delivery, and the first call
produced a verbose continuous appraisal object. Controlled overlap also showed that episode or
semantic formation could make the next foreground inference 1.7–3.8 times slower on the shared
8 GB Apple-Silicon inference resource.

The Stage 7 semantic contract must remain unchanged: the current event is appraised before
generation, tentative affect shapes the same reply, and only `EmotionManager` may derive and
commit a bounded transition. A latency optimization may not move the transition after generation,
skip meaningful events without evidence, or make a provider an affect owner.

## Decision

### One provider-aware local inference slot

Each long-lived runtime creates one infrastructure scheduler per Ollama origin. Heavy calls use
four priorities: conversation, appraisal, episode formation and semantic formation. Only one such
call runs at a time on that origin. A short configurable grace period leaves a newly free slot
available for an immediately following foreground turn before derived work becomes eligible.
FIFO ordering applies within a priority; bounded background aging prevents permanent starvation.

The scheduler does not cancel an HTTP request already in flight. It may delay the next derived
call, but it never interrupts an atomic provider operation. Embedding remains outside this gate
because the measured empty-index path performs no embedding and no evidence showed that the small
embedding call required the same heavy-generation policy.

### Compact categorical infrastructure wire

The provider-neutral application response remains the versioned continuous
`AffectiveAppraisalProposal` required by ADR 0015. The Ollama infrastructure adapter now uses a
separately versioned categorical wire object containing one to three typed appraisal categories,
a bounded confidence integer and only supplied provenance handles. The adapter deterministically
maps each category to the canonical semantic signals before the proposal crosses into application.

The vocabulary is closed and the output has no prose, explanation or chain of thought. Dynamic
JSON Schema enums restrict provenance handles to the exact supplied interaction/memory/claim IDs.
`EmotionManager` still validates confidence and provenance and remains the sole owner of
personality modulation, per-event caps, bounds, mood impulse and persistence. The wire mapping is
not a state mutation policy.

Conversation and appraisal model settings remain independent, but the accepted default for both
capabilities stays `qwen3:4b-instruct`. Tested 0.6B and 1.5B candidates did not meet the semantic
corpus threshold even when schema adherence improved. There is no silent fallback to a different
model; the existing appraisal-failure policy continues with pre-event state and no mutation.

### Rejected shortcuts

- A cheap skip gate is not deployed: the available corpus did not justify a sufficiently low
  false-skip policy, while a false skip of distress/conflict/loss is worse than an extra call.
- Combined reply plus post-turn appraisal is rejected for this checkpoint because it would remove
  the current event's authoritative influence from the same response.
- Mid-request preemption and multiple simultaneous heavy generations are rejected because Ollama
  cleanup/preemption semantics were not proven and contention was measured directly.
- Eager startup warmup is not enabled: it moves visible wait to startup and warm load is already a
  small part of steady-state latency.

### Required character-regression correction

The mandatory Stage 7.6.1 rerun found two sampled provider claims outside existing trusted truth:
an unsupported promise to “be near” the user and a technical denial that affect influences the
reply. Context schema v9 narrows only the late current-turn reminder for relationship and technical
disclosure. It states current/future epistemic uncertainty without promises and states that typed
affect changes current expression. The complete self-model, behavior policy v7, output text,
relationship absence and every persistence boundary remain unchanged. Only the two factual
relationship modes use deterministic temperature zero and proportional 48/56-token budgets; other
conversation modes and the configured conversation model are unchanged.

## Consequences

- Derived cognition no longer normally competes with an immediately following user-facing turn;
  already-running provider work remains a bounded non-preemptible limitation.
- Appraisal output falls from roughly 98–178 tokens to about 21–22 tokens in the measured corpus,
  while the application/domain proposal and same-turn affect semantics remain intact.
- Appraisal quality remains stochastic. The versioned semantic corpus, not exact float equality,
  is required for any later model or category-mapping change.
- The scheduler is process-local, contains no durable job state and does not replace Stage 7.5
  post-response idempotency/backfill.
- No migration, relationship state, user model, output rewrite, streaming or external service is
  introduced.
