# ADR-0012: Durable conversation history and derived episodic formation

- Status: Accepted
- Date: 2026-07-28

## Context

Stage 4 must retain real user text for the first time, distinguish raw history from memory,
make non-streaming reply delivery transactionally honest, and allow semantic extraction to
fail without erasing a conversation that actually happened. Stage 5 retrieval is not yet
available, so persistence cannot be treated as permission to recall. The earlier target
transaction in ADR-0008 requires canonical interaction state to commit before delivery but
does not settle whether a rebuildable episode belongs to that same transaction.

The raw-retention gate also needs a concrete development posture. Automatic redaction would
silently change evidence and cannot yet be made reliable; retaining provider prompts would
create unnecessary sensitive copies; discarding failed input would hide attempted interaction
history needed for recovery.

## Decision

Store exact accepted user and committed assistant text as plaintext in the local SQLite
canonical store. Store no system/developer prompt, serialized character context, full provider
request, or raw chain-of-thought as conversation messages. Normal logs contain IDs, counts,
versions, provider metadata and reason/error types only—never message, reply, episode summary,
or evidence quote. A user message is retained when a pending interaction is begun even if
generation later fails; an assistant message exists only for a completed interaction.

Stage 4 does not implement automatic redaction, expiry, physical deletion, encryption, or
export. Records remain until the local database is explicitly removed or a future approved
privacy workflow changes the policy. This is a development posture, not production readiness:
real-user deployment remains gated on encryption/key management, retention, erasure and export
decisions already tracked in `open-questions.md`.

Each `talk` has a caller-owned `client_request_id`. Without an explicit session it creates and
closes one implicit short session; callers may explicitly start a session and use it for
multiple turns. Session membership is persisted but no session history is placed into model
context at this stage.

The interaction lifecycle is split into three boundaries:

1. begin transaction: idempotently create session if needed, pending interaction and exact user
   message;
2. provider inference outside a database transaction, then canonical finalize transaction:
   append assistant message, generation metadata, completed status and implicit-session close
   atomically; only a successful finalize permits reply delivery;
3. derived episode formation after canonical finalize: call a replaceable
   `StructuredGenerationPort` outside a transaction, then atomically commit terminal
   create/skip/reject decision, optional episode/evidence and audit.

If finalize fails, no reply is delivered and no completed half-pair exists. Replay of a
completed request returns the stored reply. A provider/validation failure leaves a retryable
failed interaction with its user message. Episode extraction or commit failure leaves completed
history intact and no partial projection; replay retries formation without regenerating the
conversation reply.

Formation v1 permits at most one episode per source interaction and formation version. The
provider returns a typed create-or-skip proposal with summary, importance, confidence and exact
source quotes. `MemoryManager` validates schema, completed source ownership, non-empty bounded
summary, unit-interval scores, a versioned minimum importance of `0.5`, and exact quote presence.
Only user-authored messages are accepted as event evidence in v1; generated assistant output
cannot prove an external event. A terminal decision key and database uniqueness on
`source_interaction_id + formation_version` make replay duplicate-safe. Semantic cross-source
deduplication, consolidation and user facts remain Stage 6 work.

Ollama episode extraction uses its documented `/api/chat` JSON-schema `format` capability with
temperature zero and strict Pydantic validation. The adapter is infrastructure only; provider
output has no repository reference and is untrusted until owner policy accepts it.

`ResponseGroundingGate` validates provider-declared shared-past evidence references against IDs
actually available in the generation manifest. Stage 4 provides no prior evidence to generation,
so any declared past claim is rejected. Plain-text models can still fail to declare a claim; the
gate is an enforceable contract seam, not a complete semantic classifier, and sampled false-memory
evaluation remains required.

## Consequences

Raw dialogue survives restart independently of provider threads; an episode is selective,
versioned, source-reachable and rebuildable. Trivial interactions can have durable history and a
durable skip decision without becoming memory. Formation outage cannot falsify history or block a
committed reply, and a retry cannot duplicate conversation or memory.

The local database now contains sensitive plaintext and exact evidence spans, so filesystem
access is a privacy boundary. No production retention claim is made. Current grounding cannot
detect an undeclared natural-language past claim, and exact quote verification proves source
reachability rather than complete semantic entailment. One-episode-per-source dedup does not merge
the same event reported in different interactions. These limitations are explicit gates for later
evaluation, semantic consolidation and privacy work—not hidden Stage 5/6 behavior.

## Alternatives rejected

Treat every message as memory: destroys selectivity and epistemic distinctions. Put episode
formation inside canonical finalize: makes a secondary model outage erase or withhold a real
conversation. Return the reply before canonical commit: permits user-visible history that Satori
denies ever happened. Store provider/system prompts with dialogue: duplicates sensitive data and
confuses trusted runtime policy with human speech. Automatic redaction now: changes canonical
evidence without an approved policy. Use assistant output as event evidence: creates a
self-confirming hallucination loop. Add recent history, embeddings or semantic retrieval: starts
Stage 5 outside scope.
