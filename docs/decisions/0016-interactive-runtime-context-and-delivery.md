# ADR 0016: Interactive runtime, bounded recent context and post-response processing

- Status: Accepted
- Date: 2026-08-01
- Supersedes: none
- Related: ADR 0007, ADR 0008, ADR 0011, ADR 0012, ADR 0013, ADR 0015

## Context

After Stage 7, one `satori talk` invocation reconstructed the application and provider adapters,
performed every derived-memory step, and only then returned the reply. A short social turn took
about 23 seconds cold and a following one-shot turn about 39 seconds on the target Mac. An
explicit session persisted membership but did not place recent canonical turns in the provider
request. Unsafe token streaming would reduce perceived latency by displaying text before the
canonical interaction/affect transaction was known to have committed.

Stage 7.5 is an engineering checkpoint between product Stages 7 and 8. It may improve runtime and
interaction UX, but it must not add relationship state or weaken the Stage 4/7 delivery contract.

## Decision

### Long-lived runtime

`satori chat` composes one application runtime, holds one explicit session ID and reuses provider
adapters plus bounded shared HTTP/1.1 connection pools for the process lifetime. Ollama chat-based
capabilities receive configurable `keep_alive`; the documented default remains finite. Conversation,
appraisal, episode formation and semantic formation have independent configured model fields even
when they currently select the same installed model. The embedding adapter shares transport but
does not send an undocumented `keep_alive` field.

### Bounded immediate continuity

Recent conversation is a read projection of canonical `completed` user/assistant pairs in the
current explicit session. It is bounded independently by whole-turn count and character budget,
ordered chronologically, and inserted in user/assistant roles before the current user message.
Pending/failed turns, hidden provider requests and system/developer messages never enter it.

This projection is present-session continuity, not an episodic/semantic memory aggregate, user
model or new persistence type. Canonical history remains complete while the provider request drops
oldest recent pairs deterministically. Only canonical user-message IDs are eligible grounding
handles.

### Delivery and derived work

`TalkToSatori` stops at the canonical assistant/completed-interaction/affect commit. Only then is
the full reply eligible for display. Episode formation, episode embedding and semantic
consolidation run through a small retryable/idempotent post-response processor. Interactive chat
uses an in-process serial queue and drains it during graceful shutdown; no broker or service is
introduced. A failure returns phase metadata and cannot invalidate canonical history.

Completed request replay returns the stored reply before appraisal/generation and does not
implicitly enqueue derived work. Missing derived work is retried only by the explicit post-response
processor/backfill path, so replay remains side-effect free.

### Streaming

Token streaming is not implemented. Displaying provider fragments before canonical finalize would
allow a user-visible answer that has no durable completed interaction or matching affect state.
Safe streaming requires a separately accepted durable draft/outbox protocol with cancellation and
retry semantics. Until then, chat shows an immediate progress indicator and emits the full reply
after commit.

### Observability

Monotonic phase timings cover startup/bootstrap, recent projection, retrieval embedding and
search/rank, affect materialization, appraisal, context assembly, generation, grounding, canonical
commit, committed-reply eligibility and post-response phases. Ollama duration/count fields are
retained as metadata only. Normal chat is human-readable and quiet while structured logs go to a
configured file sink; `--debug` exposes metadata-only phase diagnostics, never prompts or raw
memory/context.

## Consequences

- Interactive turns reuse one process/session/transport and immediate dialogue no longer depends
  on episode or semantic completion.
- Canonical history and affect atomicity remain unchanged; cancellation cannot create a fake
  completed assistant turn and replay cannot apply emotion twice.
- Recent continuity works even if derived memory is missing or still processing, without
  pretending that chat history is long-term memory.
- Warm latency is still dominated by structured appraisal and model token generation on the
  current 4B model. Separate model configuration permits later benchmarking of a faster appraisal
  capability without changing `EmotionManager`.
- Stage 8 relationship, trust, attachment, affection, closeness and rapport state remain absent.
