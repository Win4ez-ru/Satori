# ADR-0077: Represented Repair Within Shared Material

- Status: Accepted narrow experimental protocol; scoped offline engineering verified with inherited failures; target semantics and Character unaccepted
- Date: 2026-10-14
- Authority: explicit user instruction to distinguish shared material from the apology represented inside it, without an API run
- Supersedes: ADR-0076 only for clarification of the focal act and its recipient in the opt-in interpretation protocol
- Related: ADR-0045, ADR-0053, ADR-0073, ADR-0075, ADR-0076

## Evidence and hypothesis

The preceding-agreement target sample matched 19/21 paid expectations. Both earlier draft
failures and the new-domain draft passed. However, a received message retained its reported
frame while its represented apology became `speech_act=other, addressee=none`. A legacy quoted
letter showed the same loss of detail while passing its weaker existing gate. Both current
quotations reached the inner apology. These observations do not prove an internal reasoning cause.
All previous plans, labels, failures and paid results remain immutable.

Hypothesis: explicitly locating the focal interpersonal act inside supplied material, separately
from the user's act of sharing it, will retain that act and its relative recipient without
mistaking it for the user's present apology to Satori. This concerns interpretation, not an
emotion, sincerity judgment, forgiveness or an accepted Character reply.

## One protocol clarification

Change only the trusted instructions of the existing default-off v2 repair interpretation
operation. `speech_act`, `frame` and `addressee` describe the same focal fragment. Showing or
reporting material does not erase an identifiable repair represented inside it. Its addressee is
the recipient of that repair, rather than the audience receiving the outer shared message.
No extra output fields or reasoning transcript are requested.

The existing categories remain sufficient:

| Focal fragment | Act | Frame | Relative addressee | Live repair cue |
| --- | --- | --- | --- | --- |
| Another person's apology in a received message | repair_offer | supplied_or_reported | other | false |
| A past apology addressed to the actual Satori, now quoted | repair_offer | supplied_or_reported | satori | false |
| The user's distinct present apology to Satori | repair_offer | direct_exchange | satori | true when supported |
| Represented repair with an unresolved recipient | repair_offer | supplied_or_reported | unresolved | false under material uncertainty |
| Material with no interpersonal repair | other | resolved frame | none | false when supported |

`other` distinguishes a recipient from the actual Satori; it does not create a typed identity for
the user, colleague or other person. Canonical IDs and exact spans retain source context, not a
new participant graph. Supplied frame and Satori addressee are orthogonal: a historical quoted
apology to Satori remains represented, not current relationship evidence or live repair.

The existing priority for a distinct current live repair in a mixed turn is unchanged. Current
context may cancel an invitation or change topic. The preceding pair remains before the current
turn; it is used as `basis=prior_pair` with both exact quotations when needed to establish frame
or recipient. Current-independent evidence does not acquire a compulsory history basis. No
recipient is invented to eliminate uncertainty, and no neutral, negated, polite or mechanical
text becomes repair merely because it is quoted.

## Unchanged application and owner boundaries

The strict schema v2, categorical validation, quote uniqueness/bounds, source reader/receipts,
latest-slot/counterparty matching, eligibility and cognition precedence remain byte-identical.
One effective transient cue still reaches Character evidence and cognition intake before agency.
Only supported direct repair toward Satori admits it. Supported represented repair withholds it
while retaining its act/frame/recipient categories. Valid material uncertainty withholds it;
explicit compound abstention, invalid/outage and deterministic skip retain the existing baseline.

No new call, state family, writer, migration, entity resolution, persistent activity, emotional
rule or response rewriter is introduced. EmotionManager, Relationship, Personality and Cognition
retain their ownership. The same temperature, cap, one-call limit, shared foreground retry and
zero-inference replay apply. Disabled V35 remains isolated. Unrecognized compound tasks and
invalid/outage lexical false positives remain residual limitations.

## Verification and decision gate

Use a new isolated copy of the tested agreement runtime; keep the moving Foundations head
independent. Preserve both known supplied-message observations as inherited cases, then add
contrasts for received/live identical words, represented Satori recipient, current live override,
unresolved inner recipient, non-repair material and another language. Require complete dimensions
and canonical source spans, rather than cue suppression alone.

Automated tests and a guarded zero-network managed diagnostic use prescribed semantic proposals,
neutral appraisal and mechanical foreground text. They verify transport, admission, owner neutrality
and off/on controls. They cannot establish that the model understands the revised instructions or
that final replies express persistent emotions. Record inherited full-suite failures separately.

The user explicitly excludes API execution in this iteration. No real model, paid plan, grant,
key read or automatic local-model substitute is needed. Target semantic accuracy and human
Character acceptance remain open. Parallel Foundations continues, and Stage 15 remains closed.

The iteration record records
the final scoped checks, diagnostic evidence and limitations.

Offline verification completes 333 focused tests, Ruff/mypy, isolated 0020 migration/bootstrap,
and 24 controlled managed cells with exact categories/spans, matched initialized owners and
full source receipts. Two live cases change cue/intake/agency; ten other pairs preserve exact
foreground requests. Full frozen coverage is 5986 passed /13 inherited failures /5 skipped,
217 modules and 6004 unique testcase fingerprints; no new failures. All seven material copies
and six rebuilt source/wheel pairs remain exact. Failed helper preparations/initialization are
preserved separately, without source or input changes. Main receives only one production and
two new test files. The overall pytest gate remains red; target/model semantics remain NOT_RUN.
Neither managed transport nor engineering verification accepts Character.
