# ADR-0075: Repair Frame and Addressee Before Admission

- Status: Accepted experimental design; scoped verification complete, inherited/native failures retained; target semantic gate failed; behavioral gate open
- Date: 2026-10-14
- Authority: explicit user instruction to implement the next narrow Character hypothesis
- Supersedes: ADR-0073 only for the opt-in semantic proposal version and valid-material-uncertainty disposition
- Related: ADR-0045, ADR-0053, ADR-0058, ADR-0073

## Evidence and hypothesis

The separately authorized ADR-0073 API diagnostic completed eleven classifications and one
protected no-call case. Nine classifications matched frozen expectations. The same apology was
appropriated as repair toward Satori after a colleague-draft invitation, and a materially unresolved
addressee was classified as supported repair. Both full canonical pairs reached the actual API;
quote reachability did not establish semantic targeting. Paid evidence remains immutable.

Hypothesis: resolving the communicative frame and addressee of the focal speech act before
admitting a repair cue will distinguish a live exchange with Satori from supplied/reported
material and unresolved targeting. This concerns interpretation, not whether Satori is hurt,
whether an apology is sincere, or whether she forgives it.

There is also an admission gap: v1 material uncertainty preserves the lexical baseline. A baseline
repair cue can therefore survive a valid uncertain interpretation. In v2, valid material uncertainty
withholds repair admission. This is different from an invalid or unavailable operation.

## Narrow decision

Reuse the existing default-off `conversation_repair_interpretation_enabled` operation and its
V35 isolation, eligibility, provider port, input bounds and one-call limit. No new cognitive call,
state family, persistent schema, source reader or state owner is introduced.

Strict proposal schema v2 contains these categorical fields and three bounded exact quotations:

| Field | Closed values |
| --- | --- |
| `speech_act` | `repair_offer`, `other`, `abstain` |
| `frame` | `direct_exchange`, `supplied_or_reported`, `unresolved` |
| `addressee` | `satori`, `other`, `unresolved`, `none` |
| `basis` | `current_turn`, `prior_pair`, `unresolved` |
| `uncertainty` | `supported`, `material` |

The frame applies to the focal repair-bearing act, not the outer USER transport envelope or the
whole turn as an exclusive category. A quoted third-party apology can coexist with a live apology
to Satori; the focal current quotation must then identify the live act. No fixed names, trigger
phrases, desired emotional response or example answer are added to the operation instructions.

The application derives the existing outward classification kind from the joint proposal.
Only supported `repair_offer + direct_exchange + satori` admits repair. Supported remaining
classifications yield `other`. Material uncertainty and an abstaining operation derive `abstain`,
with distinct outcome statuses. Categorical coherence is deterministic; semantic correctness
still requires model evidence.

For non-abstaining acts the current quotation must be unique, reachable and nonblank. A
`prior_pair` basis requires both prior quotations and the exact supplied latest pair. A
`current_turn` basis has no prior quotations. An unresolved basis requires material uncertainty
and no prior quotations; a current quotation still remains required. An explicit abstaining act
has unresolved frame/addressee/basis, material uncertainty and empty quotations. An `other` act
uses `none` as its repair addressee. Supported proposals cannot claim unresolved frame, addressee
or basis. Schema v1 is not silently upgraded; malformed/legacy proposals take existing invalid
fallback.

## Admission and state boundary

| Validated outcome | Effective transient repair cue |
| --- | --- |
| Supported direct repair toward Satori | `true` |
| Supported other | `false` |
| Valid material uncertainty | `false`, admission withheld |
| Explicit abstention for an out-of-scope compound turn | Existing lexical baseline |
| Invalid output, ordinary typed outage or deterministic skip | Existing lexical baseline |

Withholding repair does not assert that no apology exists. It prevents unresolved interpretation
from selecting a repair-specific reaction. The same effective cue enters Character evidence and
rebuilt cognition intake; retrieval scope must remain identical. EmotionManager and Relationship
retain their independent proposal/policy/commit paths. Existing emotion is not evidence of who
the user is addressing. Interpretation does not reset emotion or relationship state.

Default-disabled requests, cognition precedence, protected tasks/closure and the foreground
generation settings remain exact. Compound arbitration remains outside this iteration; semantic
abstention is not a deterministic guarantee against an unrecognized task losing coverage.
Invalid/outage fallback can still retain a lexical false positive. These are explicit residuals.

## Source and diagnostics

Keep all ADR-0073 full-source receipts, actual dispatch/post-operation/final commit admission,
exact latest-slot handling, counterparty scope, no truncation, shared frozen foreground retry
and zero-inference completed replay. Uncited input remains in the source footprint.

Both supported and valid-material effective outcomes retain a bounded interpreted object with
canonical IDs/spans. Prepared metadata must match it exactly, and Talk rechecks it against the
same current/latest context before applying either true admission or uncertain withholding.
Explicit abstain/invalid/outage has no interpreted object and cannot claim this proof.

Content-free transient outcome/trace adds the four categorical interpretation fields. The
existing outward kind, status/reason and baseline/effective cue remain available; uncertain
withheld outcomes require effective `false`. Numeric usage, latency and provider metrics retain
their existing paths. No raw semantic output, quotation or chain-of-thought is logged or persisted.
No persistent context-manifest version or migration changes.

## Verification and acceptance

Retain the two exact API failures and compound counterexample. Test strict schema/coherence,
source reachability, valid-uncertainty withholding even with a true lexical cue, baseline fallback,
protected routes, same-current/different-frame managed controls, mixed quoted/live repair,
counterparty/latest-source isolation, withdrawal, retry/replay and owner neutrality. Hold out
indirect draft invitations, supplied messages without quotation marks, nested quotations and
unresolved pronouns. Scripted proposals test transport and admission, not model understanding.

Use a separate frozen runtime from the current dirty Foundations checkout. Record inherited
failures and source/installed parity; do not label focused tests as a complete Foundation gate.
Real target-API verification needs a new immutable plan and fresh exact paid grant. Prior claims
stay consumed. Emotional expression and longitudinal human Character acceptance remain separate
gates after interpretation qualifies. No global winner, Character completion or Stage 15 advance
follows from technical verification. Parallel Foundations 14.4.2i continues independently.

The separately authorized one-shot target API sample completes on 2026-10-14 with fifteen
classifications and one protected skip, no retries or transport/billing failure, USD 0.03814 by
usage. Thirteen paid classifications match frozen expectations. Both draft cases yield valid
material withholding instead of supported supplied-text/other; preventing repair appropriation
does not establish frame understanding. The semantic gate therefore remains failed and selector
comparison unqualified. The default-off design, authored criteria and source remain unchanged;
this grant is consumed. Full evidence and remaining hypotheses are in the
iteration record.
