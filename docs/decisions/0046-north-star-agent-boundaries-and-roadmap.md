# ADR-0046: North Star agent boundaries and dependency roadmap

- Status: Accepted (documentation/planning boundary only)
- Superseded in part: ADR-0053 removes only decision 3's 14.3-before-14.4.1 scheduling gate;
  its original rationale and all other decisions remain unchanged.
- Date: 2026-09-04
- Authority: explicit user request for North Star audit, architecture review, roadmap and Notion update
- Supersedes: no existing runtime ADR; replaces only the future scheduling assumptions in roadmap/vision
- Related: ADR-0001–0008, ADR-0013–0014, ADR-0020, ADR-0022–0027, ADR-0045

## Context

Notion's 2026-09-04 vision adds a personal digital agent to the long-lived original character.
The audited HEAD `0b32dcaace50d8e31778549e96cec33e9e249b28` has strong local domain owners,
but no external event ingestion, technical permission owner or external action executor.
Checkpoint 14.3 is a transient conversational agency candidate, not external agency.
Stage 9 World Model represents counterparty-reported claims; it is not a calendar/email event graph.
Old ordering delayed tools until after embodiment and left recovery/privacy without a concrete
pre-integration delivery gate. Local constitution and Notion status had drifted.

## Decision

1. Extend the product constitution with personal digital agency without weakening continuity,
   independence, no raw CoT, one-owner mutation or healthy-relationship invariants.
2. Keep all completed Stage/checkpoint history and every existing Stage 15–25 identifier.
   Publish an explicit dependency order and inserted bounded checkpoints in
   `../roadmap-north-star.md`; optional Stage 16 is not a prerequisite for agent infrastructure.
3. Finish the existing 14.3 acceptance decision first. A new planned 14.4 gate addresses whole-state
   recovery, secure exports, memory visibility/retention and access-scope boundaries before adding
   external sources. Nothing here authorizes a paid sample, opens Stage 15 or changes active policy.
4. Introduce external evidence through versioned source/event envelopes, not fake user messages.
   Model uncertainty, event/observed/received time, correction/retraction, source/account/device,
   access scope, consent, root/correlation and retention explicitly. Each receiving owner requires
   its own approved eligibility policy; existing user-rooted policies stay closed.
5. Separate authenticated principal, subject/counterparty, canonical identity, data visibility,
   relationship state, and capability grant. Relationship warmth/trust cannot grant, renew or
   broaden permissions. A read may disclose sensitive data even without external mutation.
6. Technical permission grants bind operation, account/resource/device, expiry and constraints.
   Risk/confirmation are deterministic; confirmation binds the exact action payload/version and
   does not survive changes. Recheck grants immediately before execution and after retry/recovery.
   Secret references, not credentials, may be stored in action/configuration records.
7. Model action lifecycle separately from conversational reply commit. Prepare/persist intent,
   admit, confirm when necessary, execute outside DB transaction, record receipt/failure/unknown,
   reconcile and propose owner updates. Never promise distributed exactly-once execution.
   Correlated action receipts do not independently reinforce self beliefs or relationships.
8. Establish event/world/permission/action gates with synthetic adapters before real connectors;
   enable read-only integrations before reversible writes, and each send capability separately.
   Use the same core for voice/avatar/devices. Platform capabilities must be validated at each gate.
9. Preserve the modular monolith and local canonical writer. Multi-device sync, product auth,
   credential vault/encryption choices and streaming require dedicated implementation ADRs.

## Consequences and alternatives

The plan adds practical source, security and action boundaries, not an all-purpose security
framework. It rejects direct LLM-to-API/OS invocation, permission-by-closeness, universal event
eligibility and shadow per-device selves. Existing typed ports/UoWs/audit/recovery patterns can be
reused, but they are not already an action framework.

Keeping Stage IDs avoids rewriting historical ADR references; execution order therefore follows
dependencies rather than numeric sort alone. Future contracts in the extended roadmap are design
deliverables and must be revalidated when opened. New evidence schemas, migration DDL and owners
need stage-specific ADR approval before code. No runtime schema, owner, provider call or behavior
changes in this audit. See `../north-star-audit.md` for evidence and limitations.
