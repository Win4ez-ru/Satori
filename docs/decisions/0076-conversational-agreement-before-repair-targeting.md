# ADR-0076: Conversational Agreement Before Repair Targeting

- Status: Accepted experimental protocol; scoped engineering verified with inherited failures retained; one target sample has 19/21 semantic matches, complete semantic gate failed; Character gate open
- Date: 2026-10-14
- Authority: explicit user instruction to test the preceding draft-agreement hypothesis
- Supersedes: ADR-0075 only for the opt-in operation's interpretation protocol and input presentation order
- Superseded in part by: ADR-0077 for the focal represented-act and recipient clarification; chronology and other boundaries remain authoritative
- Related: ADR-0045, ADR-0053, ADR-0073, ADR-0075

## Evidence and hypothesis

The ADR-0075 target API sample
matched thirteen of fifteen paid classifications. Both explicit and indirect colleague-draft
contexts yielded valid material uncertainty instead of supported supplied-text interpretation.
The effective repair cue was suppressed, but the intended frame was not understood. Full canonical
pairs reached the API; no missing-input or transport failure explains those outcomes.

Hypothesis: interpreting the preceding conversational activity and the current contribution
before assigning the focal repair addressee will distinguish submitted wording from a live
apology without treating every possible alternative interpretation as material uncertainty.
The current v2 wire presents the current message before its preceding pair. That ordering is
an observed representation choice, not a proven cause of the semantic failure.

## Minimal protocol change

Keep the same default-off V35 operation, exact inputs, strict v2 proposal and application-derived
admission. Present the complete optional `prior_pair` before `current_user` within the same
untrusted JSON envelope. The operation protocol first considers the preceding activity and invited
contribution, then whether the current turn supplies, replaces or cancels it or changes topic,
then the focal frame and addressee. An unquoted first-person fragment can supply invited wording;
the first-person form alone does not cancel that context.

An invitation is a proposed conversational move, not a binding user commitment. A question about
possible wording is not automatically an agreement to submit it. A current live apology, explicit
cancellation, mixed reported/live fragment or ordinary topic change remains available. Competing
interpretations must have evidence in the actual exchange; theoretical alternative conversations
alone do not establish material uncertainty. These are generic semantic instructions, not phrase
matchers, forced draft labels, obligations to resume a topic or rules prescribing emotional replies.

The existing `basis=prior_pair` and paired exact quotations represent a history-dependent frame.
Current-independent interpretations retain `current_turn`; genuinely unresolved targeting retains
material uncertainty. No new continuation label, persisted activity, state owner, schema or call
is introduced. The model's internal interpretation sequence is not observable and is never
requested or stored as a reasoning transcript. The hypothesis concerns this protocol as a whole;
a successful sample cannot attribute improvement separately to chronology or instruction order.

## Unchanged boundaries

ADR-0075 joint validation, quote uniqueness, bounds, actual source admission, latest-slot freshness,
counterparty isolation, metadata/object agreement and final canonical matching remain authoritative.
Supported direct repair admits the cue; supported other and valid material uncertainty suppress it;
compound abstention, invalid output, ordinary outage and deterministic skip preserve baseline.
Unrecognized compound-task preservation and invalid/outage false positives remain open limitations.

The foreground model/port, temperature zero, output cap 384, one-call semantic limit, no semantic
retry, shared foreground retry and completed replay paths remain unchanged. Disabled V35 has zero
new calls and identical foreground behavior. EmotionManager, Relationship, Personality and Cognition
retain their owners and precedence; an interpreted apology cannot forgive, clear affect, change
trust, create evidence from assistant output or issue a permission. No raw reasoning or semantic
output is logged. Parallel Foundations proceeds independently; Stage 15 stays closed.

## Verification and decision gates

Preserve all sixteen prior API cases, labels, frozen plans and failures. Add authored contrasts
for an accepted draft in a new domain, identical live words, live override, topic change,
cancelled invitation and an unresolved question about submitting wording. Additional offline
controls cover hypothetical contexts, nested quoted repair and English supplied/live contrasts.
For new history-dependent supported cases require the intended frame/addressee, `prior_pair`
basis and both bound prior spans. Merely withholding the cue or returning uncertainty everywhere
does not pass the semantic gate.

Automated and controlled managed tests verify protocol transport, admission, protected work,
unchanged owners and default-off isolation; scripted proposals are not evidence of model
understanding. Use a fresh isolated copy of the tested compatibility runtime and disclose inherited
failures instead of certifying the parallel moving Foundations head. Do not use a new local-model
sample as a substitute for the user's requested target-API evidence.

A real target test needs a new immutable plan and fresh bounded human grant; the prior one-shot
grant is consumed. No model/cap change, sample refill, label adjustment or automatic repeat is
permitted. Final agency/expression and emotional continuity require separate qualifying diagnostics
and human review. Neither engineering success nor semantic accuracy accepts Character.

Verification on the frozen compatibility runtime completes 318 focused tests, Ruff/mypy, a
twelve-cell controlled managed diagnostic and full 216-module coverage: 5971 passed /13 inherited
failures /5 skipped, zero new failures. The new target plan's immutable preparation readiness
records 21 classifications plus one protected skip, maximum 21 attempts /USD 0.50, with zero real
calls and no fresh grant at that time. A subsequent freshly authorized one-shot invocation
completes all 21 calls and the skip for USD 0.066344, without retries/refill or transport/billing
failures. The claim is consumed. Both former draft failures and the new-domain draft pass, but
only 19/21 paid expectations match: a received message loses represented repair/other target,
and a correct direct repair lacks required prior-pair basis/spans. The complete semantic gate
fails and selector comparison remains unqualified. All previous sixteen API cases and results
remain exact; no labels or source are changed after sampling. The
iteration record preserves checks,
failed harness attempts, source bindings and remaining limitations. Preceding-agreement
understanding receives partial positive evidence; final agency, appraisal and Character prose are
NOT_ASSESSED. Inherited global failures and Character acceptance are not closed by these results.
