# ADR 0041: Typed v25 social disclosure and privacy-safe failure observability

- Status: Accepted
- Date: 2026-08-28
- Supersedes: ADR 0040 (current-candidate selection and provider projection only)
- Related: ADR 0016, ADR 0020, ADR 0021, ADR 0023, ADR 0031, ADR 0032, ADR 0040

## Context

The separately authorized v24 `core_emotional` OpenAI module completed three clean sessions with
three public turns each: 9/9 first-attempt foreground calls, 12,517 input tokens, 502 output tokens
and a repository standard-rate estimate of USD 0.031058. The historical artifact did not retain
cache-detail usage, so this is not a cache-verified exact invoice. No retry, incomplete response or
provider error occurred. Direct review rejected
the sample. The replies repeatedly imposed the same ordered reaction/recovery scaffold, echoed the
reported event or state and introduced causal psychology that was not established by the current
input or trusted context. This is useful rejection evidence for v24, not a phrase bank and not
evidence that the transport failed.

A separate exact manual chat exposed three deterministic routing failures before another paid
sample was justified:

- `приветик, как ты?` reached a precise-answer topology instead of a social current-affect
  disclosure, producing a polite state inventory;
- `и я тебя рад видеть` was classified as a general owned response instead of reciprocal warmth,
  which licensed a detached aphorism;
- the broad request `расскажи о себе, кто ты, чем увлекаешься, как себя чувствуешь`
  activated user-emotion/listening logic, did not carry an interests facet through the typed
  disclosure boundary and then failed before a reply was committed.

For that historical failed interaction, the durable record can establish only the exception class
`InvalidProviderResponse`. The previous schema did not persist a closed reason or safe provider
identity on failed rows. The exact underlying reason therefore cannot be reconstructed and must not
be guessed from the user-facing fallback message.

The required correction is not another personality paragraph. The current-turn classifier,
cognition strategy and delivery decision need one typed path for questions about Satori. At the
same time, future provider failures need enough content-free observability to distinguish a token
budget issue from malformed output, refusal or transport failure without storing vendor bodies,
partial replies or credentials.

## Decision

### Candidate v25 and historical reproducibility

Behavior policy v25 becomes the current offline architecture candidate. Policy v10 remains the last
provider-accepted Checkpoint 14.2 baseline. V24, its schema-1 `CharacterDeliveryDecision`, cognition
template registry V2, fixtures and paid `core_emotional` artifact remain immutable historical
evidence. V25 does not relabel or rewrite any v24 result.

V25 replaces three principles only within its versioned behavior policy:

- grounded claims explicitly exclude psychological plausibility as evidence for a particular
  user's hidden cause, motive, consequence or state;
- independent character must be expressed through Satori's selected reaction, position, practical
  move or contextual dry edge rather than generic service prose;
- natural brevity permits only a few words of acknowledgement before one independent conversational
  move and does not require a question, advice or topic change.

These are provider-facing constraints, not persistent personality state and not a provider output
rewrite.

### One typed Satori self-disclosure path

The disclosure vocabulary moves to a dedicated request-local contract shared by classification,
cognition and character delivery. It remains a projection with no repository, owner, mutation or
provider write-back path. `DisclosureFacet.INTERESTS` is added to the existing closed disclosure
vocabulary.

V25 deterministically separates bounded cases that ask about Satori from those that describe the
user. `DisclosureRequestKind.SATORI_SELF` covers every active direct Satori-self mode: a social
check-in that requests current affect; personal identity, memory, affect or interests; digital
nature, embodiment, technical identity or consciousness; relationship capability; and a question
about the current relationship. An ordinary reciprocal reply and a user declaration such as
`Я тебя люблю` remain `DisclosureRequestKind.NONE`, even though relationship context may still be
relevant to the response. Subject scope is shared by primary mode, facets and cognition signal,
rejects quoted/hypothetical/dismissed references and keeps user-side contrasts such as
`у меня/со мной что-то случилось` as user-state evidence. A broad request may carry several required
facets simultaneously regardless of list order. Missing stable interests stay missing: supplied
owner-approved inclinations may be expressed, while their absence permits only general current
curiosity, not an invented hobby or biography. A complaint that the previous reply showed no
interest in the user's current activity remains dialogue-relevance feedback and does not manufacture
a persistent-interest facet.

The classifier supplies `PerceptionSignal.SELF_DISCLOSURE_REQUEST` only for
`DisclosureRequestKind.SATORI_SELF`. The Stage 10 cognition owner then suppresses the false user-
emotional-presence inference for that signal, keeps explicit listen/high-distress/safety precedence
and otherwise selects an `ANSWER` position grounded in trusted self state. Character selection
cannot manufacture the signal or reverse that stance.

V25 keeps cognition intent registry V2 but requires cognition template registry V3 with the same
template ID `satori.cognition.response-substance` and schema version 3. V3 changes the rendering of
already typed substance rather than adding an intent: `listen_and_reflect` must not paraphrase,
explain or diagnose the stated experience; `presence_before_advice` is conditional when advice is
actually present; and `hidden_user_state` explicitly includes causal psychological explanations.
Historical V2 rendering remains reproducible for v24.

### Character-delivery decision schema 2

V25 requires `CharacterDeliveryDecision` schema 2. It preserves every v24 cognition-owned field and
adds only the request-local `required_disclosure_facets` plus closed goals for `social_connect` and
`self_disclose`.

`social_connect` answers the current social gesture as Satori: a check-in may express supplied
current affect naturally in first person, while reciprocal warmth needs only a live reaction.
State inventories, generic-assistant greetings and detached aphorisms are explicitly outside this
goal. `self_disclose` must cover every directly requested supplied facet once in one cohesive
personal arc, not enumerate traits, internal fields or system architecture. Relationship maturity
may modulate ease and openness but cannot invent familiarity or state.

V25 also removes the compulsory v24 achievement/depletion scaffolds. Achievement permits a brief
recognition without restating scale or difficulty, followed by at most one independent movement.
Ordinary depletion may choose care, one grounded low-cost action or a complete personal response;
it must not normalize, diagnose, infer a cause or build a recovery program. The new deterministic
`depletion_follow_through` signal applies only when the immediately preceding canonical user turn
explicitly states depletion and the current user directly chooses to stop or defer. It selects
practical care with no pressure and forbids assigning a second plan after the user already made
one. Quoted, hypothetical and non-adjacent wording does not establish this signal.

All v25 delivery metadata remains transient manifest observability. Provider text is still
canonical and unrewritten. The existing closed ten-reason self-consistency validator and its
maximum-one same-interaction retry are unchanged; v25 does not add a judge model or another retry.

### Closed privacy-safe provider failure reasons

Migration `0013_conversation_failure_reason` adds nullable `failure_reason` to failed canonical
interactions and permits safe provider/model identifiers only when that closed reason is present.
Legacy and non-provider failures keep all three fields null. New typed provider failures must carry
exactly one of:

- transport/configuration: `transport_unavailable`, `temporarily_unavailable`,
  `rate_or_quota_limited`, `credentials_rejected`, `resource_not_found`, `request_rejected`;
- terminal generation state: `output_token_limit`, `incomplete_unknown`, `generation_failed`,
  `generation_cancelled`, `response_refused`;
- response/adapter contract: `response_too_large`, `response_malformed`,
  `missing_assistant_text`, `usage_metadata_invalid`, `visible_output_limit_exceeded`,
  `response_character_limit_exceeded`, `adapter_contract_violation`.

Adapters map only information they can establish to this provider-neutral enum. No raw exception
message, HTTP body, prompt, user text, partial output, arbitrary vendor reason, credential or private
provider context is persisted. Unknown incomplete state remains `incomplete_unknown`; it is never
promoted to a more specific diagnosis by inference.

For OpenAI Responses, `incomplete_details.reason=max_output_tokens` maps to
`output_token_limit`; other absent or unsupported incomplete details map to `incomplete_unknown`.
This follows the official OpenAI contract that [`max_output_tokens` bounds reasoning, visible
output and other generated output tokens](https://developers.openai.com/api/docs/guides/reasoning#allocating-space-for-reasoning),
and that exhausting it can return `status=incomplete` before visible output exists. The application
continues to fail closed and never commits partial text.

Provider failure diagnosis does not authorize automatic retry or fallback. A normal turn still
uses one provider call; only the existing typed self-consistency violation may authorize its one
shared regeneration path after a complete candidate reply.

### Explicit oldest-first relationship recovery

The Stage 8 owner and evidence policy are unchanged. The operational command
`satori relationship process --limit N` explicitly selects at most `N` eligible completed sources
without terminal relationship decisions for the chosen identity/counterparty, ordered by canonical
`(started_at, interaction_id)`. It processes them sequentially through the existing
`ProcessRelationshipForInteraction` owner path and stops on the first failure. Replays remain
idempotent; the result reports only content-free considered/attempted/applied/skipped/rejected/
replayed/failed counts.

This is operator-triggered backlog recovery, not automatic historical inference, a migration side
effect or a second relationship writer. It may invoke the configured relationship appraisal
provider, so it remains an explicit operation. The real local backlog has not been run as part of
v25 and no relationship state change is claimed by this ADR.

### Evidence and acceptance boundary

V25 deterministic acceptance covers the exact failed manual three-turn chat, broad multi-facet
self-disclosure, social/relationship contrasts, depletion follow-through and historical v24
reproducibility. Provider-error coverage must prove every adapter branch maps to the closed enum,
persistence never retains sensitive error content, legacy failed rows remain readable, migration
reaches head and no provider failure triggers an automatic retry. Relationship recovery coverage
must prove oldest-first bounded ordering, idempotency and stop-on-first-failure.

No v25 provider or paid call had been performed when this decision was accepted. Offline
correctness alone therefore could not accept v25 character quality, OpenAI fit or employer-demo
readiness. The later separately authorized digest-bound exact-manual gate completed three fresh
sessions × three turns with 9/9 first-attempt calls. Its recorded token totals give a repository
standard-rate estimate of USD 0.036292; cache-detail usage was not retained, so this is not an
exact cache-verified invoice. It is sampled evidence, not decision authority: direct user review
is still required and v25 remains unaccepted. Stage 15 remains locked.

## Consequences

- Social check-ins, reciprocal warmth and multi-facet questions about Satori no longer rely on a
  generic answer/listen route.
- First-person user-state wording cannot enter that route, and the v25-only routing rules receive
  the behavior-policy version explicitly so historical v24 classification and budgets remain
  reproducible.
- Interests reach generation through the same typed disclosure boundary without creating a second
  preference source or inventing hobbies.
- V25 has one schema-2 delivery decision and cognition template V3; v24 stays byte-for-byte
  reproducible as rejected historical evidence.
- Explicit stop/defer after depletion can be acknowledged without prescribing a new recovery plan.
- Future provider failures are diagnosable through closed content-free metadata, while the exact
  cause of the historical `InvalidProviderResponse` remains unknowable.
- Relationship backlog recovery is bounded and explicit, not automatic; the real backlog remains
  untouched.
- No persistent personality owner, relationship policy, output rewrite, judge model, automatic
  retry/fallback, paid provider call or Stage 15 capability is introduced.
