# ADR-0069: Prior Dialogue Relation Before Agency

- Status: Accepted experimental design; implementation and scoped compatibility checks complete, behavioral acceptance open
- Date: 2026-10-09
- Authority: explicit user instruction to test the next Character hypothesis
- Supersedes: ADR-0045 only for an optional pre-agency semantic interpretation input
- Related: ADR-0062, ADR-0067, ADR-0053

## Evidence and hypothesis

The frozen current-position review still reports paraphrase and abrupt topic changes. The prior
candidate forms content after agency and cognition are complete. Ordinary V35 agency already
uses a fused lead; assigning that lead again would not test the reported failure.

Hypothesis: interpreting the relation of the current USER turn to the immediately preceding own
move before final reaction selection can preserve a still relevant stance across a topic change.
This does not establish that every topic change deserves a callback, or that a question creates
an obligation. Recap, affect appraisal sensitivity and persistent emotional inertia remain separate
open issues.

## Isolated decision

Add `conversation_prior_dialogue_relation_enabled=false`, restricted to V35 with both
current-position and previous-exchange appraisal candidates disabled. Existing policies and
disabled requests retain their behavior. The experiment has no new persistent state or writer.

```text
existing canonical owner reads and tentative affect
→ deterministic baseline eligibility preview (no mutation or final reaction)
→ complete actual-input receipt, prepared once
→ at most one bounded semantic relation operation
→ exact quote/source validation and fresh input admission
→ final agency selection, then completed cognition compatibility
→ foreground expression, existing max-one retry, atomic canonical finalization
```

Only an ordinary applied `none/respond/current_exchange/none/fused` baseline without typed
current perception signals, repeated-input/repetition/frustration correction is eligible. Tasks,
stop/listening, safety, repair, disclosure, canonical positions and already selected own initiatives
retain their existing route. Unrecognized tasks remain a known intake limitation; instructions
must allow abstention and preserve requested work.

The operation reads exact current USER, the immediately preceding completed pair and current
canonical personality/affect/relationship projections. A content-free scoped latest-completed
header distinguishes that slot from an older eligible history pair. Missing, excluded, erased or
oversized latest history skips interpretation; no older question is substituted or truncated.

Strict JSON returns one of `abstain`, `continues`, `shift_from_prior_move`, `closes` or
`prior_already_closed`, with `supported` or `material` uncertainty and bounded exact excerpts
from prior ASSISTANT and current USER. The application locates unique excerpts and computes
offsets, binding canonical IDs itself. Duplicate keys, unknown fields, ambiguous or absent quotes,
invalid bounds and non-JSON output are invalid. No response draft, explanation, chain-of-thought
or provider-assigned evidence identity is requested. Shape and quote reachability do not prove
semantic accuracy.

Only supported `shift_from_prior_move` can change the ordinary lead to `owned_move_first`, with
a typed relation reason. Act, subject, drive and initiative remain unchanged. This is permission
to express a grounded own transition before the new topic, not a required complaint, question,
second sentence or emotional reaction. Continues, closes, already-closed, abstain and material
uncertainty preserve baseline. Prior ASSISTANT remains conversational material, never authority
about self or fresh USER/relationship evidence.

## Ownership, trust and failures

Personality, emotion, mood, relationship and current-turn interpretation retain distinct owners
and lifetimes. The transient relation neither updates nor substitutes for any owner. Grounded
relation data is marked untrusted in a bounded USER envelope before the existing final
DEVELOPER/USER tail; final prose is generated once and is not rewritten.

Both operations use the same full actual-input receipt, including uncited sources. Prepare it
once, admit freshly at dispatch after provider queue admission and before consuming the proposal,
and retain existing commit admission. Quote subsets never shrink dependencies. Source-policy
errors are fatal, including after abstention or a typed provider outage. Unexpected contract
failures mark the interaction failed. Invalid, abstain, material uncertainty, ordinary provider
failure and known context refusal retain baseline. No semantic format retry, larger context cap,
new provider adapter or model judge is added. Foreground retry reuses the frozen interpretation;
completed replay performs no operation and omits transient relation metadata.

The semantic operation uses the same foreground port/model with a separately visible ceiling
of 384 output tokens. Native foreground generation settings are unchanged. Existing trusted
context accounting and independent history/current-input bounds remain. Numeric operation
usage/timings and closed outcomes are reported separately; raw semantic output is not stored.

## Verification and gate

Use actual Talk fixtures to contrast still-relevant and resolved history, question and statement
prior moves, ordinary unrelated change, direct task, explicit stop, answer/decline, missing and
oversized history. Verify final agency precedes cognition completion, strict validation, source
withdrawal before dispatch/commit, full-footprint fallback, retry/replay and counterparty isolation.
Contrast canonical affect and relationship inputs without prescribing an offended phrase.

Freeze disabled V35 compatibility and run the rebuilt Foundation checks in an isolated snapshot.
Deterministic proposals prove causal transport and policy bounds only. Native sessions are runtime
evidence; target API quality requires a new frozen matched-history sample and fresh bounded paid
authorization. Previous claims are consumed and immutable. Freeze human feedback before mapping
disclosure; do not accept Character or declare a winner from a preferred pair. Parallel Foundations
continues independently under ADR-0053.
