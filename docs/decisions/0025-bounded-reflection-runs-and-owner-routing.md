# ADR 0025: Bounded reflection runs and owner routing

- Status: Accepted
- Date: 2026-08-22
- Related: ADR 0003, ADR 0004, ADR 0005, ADR 0006, ADR 0007, ADR 0008, ADR 0014,
  ADR 0016, ADR 0024

## Context

Stage 11 gives Satori durable evidence-linked positions, but every formation pass remains tied to
one completed interaction. Stage 12 must synthesize patterns across time without creating a
continuously running inner monologue, treating generated text as evidence, bypassing target owners
or allowing a retry to see a different source set.

Reflection increases feedback-loop and confirmation-bias risk: a model can restate existing Satori
state, cite its own restatement and make the next run look independently corroborated. A generic
coordinator that can write every repository would also become the forbidden god owner. Personality
and value mutation are not enabled until their later dedicated stages.

## Decision

### Reflection owns records, not domain state

Add an `application.reflection` boundary. `ReflectionCoordinator` is the sole lifecycle writer for
reflection run, source, attempt, proposal and outcome records. It has no generic domain repository
and never approves another aggregate's mutation. A replaceable structured provider proposes; a
target-specific adapter invokes the existing domain owner and returns its typed decision.

Stage 12 V1 enables one mutation target, `satori_positions`, through `PositionManager`. The strict
wire also permits bounded `personality` and `values` change candidates solely so the coordinator
can record `target_owner_not_enabled_stage_12`. Those candidates contain a short observation and
evidence handles, not a trait/value delta contract. Stage 14 or another dedicated ADR must define
actual personality evolution. Unknown target owners or payload fields invalidate the provider
attempt before any proposal is persisted.

### Deterministic trigger and cost policy V1

The model never decides whether reflection runs. Eligibility is deterministic and evaluated only:

1. opportunistically at the end of the existing serial post-response processor; or
2. by an explicit local `reflection process` command.

There is no timer daemon, broker, 24/7 loop or foreground reflection call. Automatic eligibility
requires all of:

- no non-terminal run for the identity;
- at least eight previously unconsumed eligible roots from at least six interactions;
- an observation span of at least seven days;
- at least seven days since the last completed automatic or explicit run;
- no completed new run in the preceding rolling 24 hours.

Explicit local eligibility is a diagnostic/manual surface, not a force flag. It still requires at
least four previously unconsumed eligible roots from at least three interactions, no non-terminal
run and the rolling 24-hour cap; it may waive the seven-day observation span and cooldown. The same
selected source set always resolves to the existing run instead of spending a second call.

One run is bounded to twelve source items, 4,800 source characters, twelve current-position target
references, three proposals, one provider call per attempt, two attempts total and 768 output
tokens per attempt. Failed attempts are never retried in a tight automatic loop. Provider timeout
and model remain operation-specific configuration, while these semantic caps are policy V1.

Completed zero-proposal runs consume their inputs and satisfy cooldown. Failed/exhausted runs do
not establish state or evidence. A later run needs a different selected source set or a new policy
version; repair of an exhausted run is explicit operational work, not hidden automatic retry.

### Immutable source set and run identity

Selection reads only canonical evidence edges already owned by other domains:

- evidence of current or historical Satori positions; and
- evidence of active episodic memories with importance at least `0.65`.

Each selected item resolves to an immutable exact quote inside one canonical user message and
records the evidence-edge kind/ID, root interaction/message/counterparty IDs, observed time and
content hash. Multiple edges sharing one root message are deduplicated deterministically, with
position evidence preferred and then stable ID order. Semantic/user/world claims, relationship
state, assistant messages, provider output, transient cognition and reflection artifacts are not
V1 evidence sources.

The coordinator persists the run and ordered source rows before inference. The source-set hash is
computed from the ordered source kind/ID/version, root IDs and content hashes. The run idempotency
key includes identity ID, reflection policy/schema versions and this hash; trigger kind is metadata
and cannot create a duplicate run. Retry/restart reloads these rows and verifies their hashes. New
evidence arriving during inference belongs only to a later run.

Current positions and immutable value references are a separate bounded target-state projection.
They help avoid duplicates and construct valid opinion proposals, but neither is evidence. The
provider cites only opaque reflection source IDs. The application maps those handles back to exact
canonical citations before the owner evaluates them.

### Run and attempt lifecycle

`ReflectionRun` is a versioned current projection with append-only attempts, proposals, outcomes
and audit transitions. Its states are `pending_generation`, `proposals_ready`, `applying`,
`completed`, `retryable_failure` and `exhausted`.

Run creation plus sources commits first. Provider inference happens outside a transaction. A
successful strict response stores immutable proposals and a successful attempt atomically. Invalid
output, outage or timeout appends only a failed attempt and moves the run to retryable/exhausted;
it stores no partial proposals and changes no target state. Zero proposals move directly to
completed.

Proposal IDs are coordinator-generated deterministic hashes of run ID, ordinal and typed payload.
An outcome is unique per proposal ID and target policy version. After proposals exist, provider is
never called again for that run. Concurrent trigger/process calls converge through unique run and
outcome keys.

### Per-proposal owner transactions

Proposals are applied in stable ordinal order. Each enabled target adapter opens a target-specific
Unit of Work, asks the domain owner to evaluate and atomically commits:

- the target mutation, if any;
- the terminal reflection proposal outcome;
- target revision/history; and
- one audit event.

Unauthorized target outcomes commit only the reflection outcome and audit, with no target
repository in scope. Run completion is a separate resumable projection step after every proposal
has a terminal outcome. A crash may leave earlier proposals terminal and later ones pending, but
cannot leave a target mutation without its outcome or double-apply on resume.

This deliberately rejects one combined cross-owner transaction. Per-proposal atomicity preserves
owner boundaries, scales to future owners without a god Unit of Work and provides explicit partial
progress. The run is not all-or-nothing; its auditable outcomes are.

### Position-owner origin policy

`PositionManager` remains the only position writer. Its Stage 11 interaction origin keeps the exact
current-message participation and existing evidence thresholds. Reflection uses a separate typed
origin and may cite only messages in the fixed source set. To make synthesis more conservative:

| Operation | Interaction minimum | Reflection minimum |
|---|---:|---:|
| New belief/opinion or explicit revision | 2 roots/interactions/signatures | 3 roots/interactions/signatures |
| New hypothesis | 1 root | 2 roots/interactions/signatures |
| Exact merge or counterevidence challenge | 1 new eligible root | 2 eligible roots including at least 1 new root |

All Stage 11 kind/value/stance/materiality/cap/stale-target rules remain unchanged. Reflection does
not create facts, lower confidence thresholds, relabel kinds or count current position text as a
root. The position mutation and reflection outcome share one transaction and audit event.

### Structural cycle prevention

V1 breaks cycles by construction:

- every reflection source must terminate at a canonical role=`user` message;
- every intermediate edge kind is allowlisted and independently owned;
- reflection run/proposal/outcome IDs, assistant messages, provider results, current positions and
  generated summaries are forbidden source kinds;
- proposal citations must be a subset of the persisted run sources;
- a future source adapter must prove acyclic root reachability before joining the allowlist.

The persisted source set records the terminal root and intermediate evidence handle. Resolution
failure, a repeated path node, forbidden origin, hash mismatch or non-user root rejects the
candidate/run with a reason code before provider inference. A position created by reflection may
appear as later target state, but only its original canonical user roots can ever be selected as
evidence. Completed-run consumption and target-owner evidence dedup prevent self-confirming
confidence growth.

### Observability and local inspection

Normal logs contain only run/proposal IDs, trigger/status/reason codes, source/proposal/outcome
counts, policy/schema/provider/model handles, token counts and timings. They contain no quotes,
propositions, observation summaries, prompt, response or chain-of-thought.

Local `reflection list`, `reflection inspect` and `reflection process` expose lifecycle, fixed
source handles, proposals and owner outcomes. Exact source quotes require an explicit local
`--show-sources` flag. Export includes IDs, hashes, provenance, proposals and outcomes but not a
second copy of canonical raw messages.

## Consequences

- Reflection can synthesize long-period evidence without becoming a writer-owner or always-on
  simulation.
- Provider failure and multi-proposal crash recovery are retryable and idempotent without changing
  the selected evidence.
- V1 is intentionally conservative: only positions can change, while personality/value candidates
  demonstrate unauthorized routing and remain rejected.
- Automatic reflection may wait weeks in a low-activity installation; explicit local processing is
  available for acceptance but cannot bypass minimum evidence or daily spend.
- Position/episode evidence selection omits other potentially useful state families until a later
  ADR proves their root semantics and feedback-loop safety.
- Stage 12 adds no preference/interest, personality/value mutation, 24/7 process, proactivity,
  tools or Stage 13 state.

## Alternatives rejected

- A free-running reflection daemon or repeated hidden model loop.
- A generic JSON patch or coordinator with write access to every repository.
- Mutable/reselected evidence on retry.
- Existing positions, semantic claims, assistant responses or earlier reflection output as fresh
  evidence.
- One cross-owner all-or-nothing transaction.
- Enabling personality mutation early merely to satisfy the manual rejection scenario.
