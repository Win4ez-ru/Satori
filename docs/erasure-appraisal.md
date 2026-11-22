# Appraisal explanation disposition

Status: Foundations 14.4.2h macOS engineering gate passed. Architecture:
[ADR-0070](decisions/0070-appraisal-explanation-bodies-and-exact-input-provenance.md).

This owner slice logically removes the exact explanation pair from an excluded
canonical source's complete actual input closure. It does not roll back emotion,
mood, relationship, personality or inclination numerical history. It does not
claim physical or forensic erasure. Every result retains `physical_erasure=false`.

## Ownership and retained state

EmotionManager remains the sole transition writer. Prospective commits persist
the already prepared COMPLETE actual `source_input_id` and exact `audit_event_id`
atomically with the transition, numerical state and canonical reply. Citation
subsets cannot replace actual input receipts. Legacy rows keep NULL links and
are UNKNOWN for destructive completeness; no chronological or JSON-key inference
repairs them.

One owner operation sets `affective_transitions.reason_codes` to actual SQL NULL,
marks the explanation `erased`, binds the main action FK, and removes only the
`appraisal_reason_codes` key from the exact owned audit event. Its other details
and headers are retained. The eleven appraisal scores, confidence, deltas,
snapshots, versions, provider metadata, source references and committed time stay
exact. Existing Reflection/Inclination numerical signal hashes are unchanged.

`explanation_state` describes retained storage representation, not disclosure permission;
`available` may still be withheld by source eligibility. Contentful `AffectiveTransition`
still requires a nonempty explanation. Managed
reads withhold terminal, unlinked or historically ineligible explanations before
mapping a proposal. `AffectiveTransitionHistory` is a separate immutable numerical
interface with no proposal or reasons. Status and duplicate-interaction detection
use retained headers; missing explanations never permit a second state mutation.

## Review, admission and recovery

Main schema `0022_appraisal_bodies` retains existing tables and adds explicit
source-input/audit FKs and terminal guards. Floor `policy_0007` explicitly admits
the fifth closed action, `AppraisalBodyAction`, in the same mixed body stream.
Earlier Episode, Dialogue, Knowledge and Reflection action bytes and head hashes
are preserved. Commands never migrate an operational installation implicitly.

A reviewed target binds the exact three retained rows and an immutable input
closure fingerprint. The fingerprint covers every reachable structural payload
receipt and canonical root header, excluding content and mutable eligibility,
policy-generation, lifecycle and body-marker mirrors. Selection proves current
canonical ownership and complete causal scope. A mixed-support explanation is
removed as a whole. Unknown lineage, cross-counterparty ancestry, malformed raw
data, duplicate audit events and bounds violations block readiness.

Ordinary admission checks the actual SQL NULL, state/action markers, absent audit
key, duplicate census and exact retained material. It proves the immutable closure
fingerprint before loading protected explanation content or historical upstream
body material. A copied acknowledgement cannot replace these checks.

The existing exclusive installation barrier commits the floor action first.
One main transaction then preflights the entire mixed head, records receipts,
invokes the existing owners and acknowledges the complete head. A fault between
paired writes rolls back all main changes; the pending floor action fences normal
startup until explicit resume. Resume accepts only approved original or terminal
material and unchanged complete ancestry. An old archive retains its original
bytes and cannot operate until it reconciles the current trusted floor head.

## Operator commands

Use the configured scoped counterparty and existing excluded canonical root and
durable erasure intent. Main migration is an explicit administrative prerequisite.

```bash
satori sources erasure-appraisal-policy --upgrade --reviewed
satori sources erasure-appraisal MESSAGE_ID --intent-request-id INTENT_ID --preview
satori sources erasure-appraisal MESSAGE_ID --intent-request-id INTENT_ID \
  --apply --request-id REQUEST_ID \
  --expected-generation GENERATION --expected-erasure-revision ERASURE_REVISION \
  --expected-body-revision BODY_REVISION --sha256 APPROVAL_DIGEST
satori sources erasure-appraisal MESSAGE_ID --intent-request-id INTENT_ID \
  --status --request-id REQUEST_ID
satori sources erasure-appraisal MESSAGE_ID --intent-request-id INTENT_ID \
  --resume --request-id REQUEST_ID
```

Preview and status emit metadata only. Controls construct no cognitive providers.
Approval includes the full current store and schema digests, scope, generations,
intent and mixed head. Stale approval requires a new preview.

## Bounds and remaining work

Existing bounds remain: 100,000 rows, 64 MiB logical assessment input, 4,096 mixed
actions, one million references/work, 256 targets per action and 131,072 serialized
action bytes. Shared indexes and metadata read budgets avoid repeatedly scanning
the store for each target. No protected explanation cache or provider call is added.

Retained numbers may carry historical numerical influence. Other freeform audit
or decision reasons are separate owner routes. WAL, free pages, archives, exported
copies, provider and operating-system copies, IDs and hashes remain. Whole-floor
rollback, access/encryption/key recovery, Linux acceptance and full Stage 14.4
remain open. The next vertical must be chosen explicitly after the engineering
gate, using the remaining owner-route inventory; do not certify full erasure from
this pair operation.

The proposed next vertical is closed retained-audit metadata admission. The current
root inventory treats all remaining audit JSON as opaque. Existing owner-specific
event shapes must receive supported versions, closed keys and code vocabularies,
bounded numbers and exact owner/header/receipt bindings before they can be certified
as retained accountability metadata. Unknown versions, extra prose, duplicates and
unproved legacy links remain UNKNOWN and unchanged. This is a prerequisite for
whole-root completeness, not authorization for generic JSON deletion.

Checkpoint approval/restore operator reasons remain separate freeform bodies.
Their current identity/checkpoint references do not establish attribution to an
erased canonical source or counterparty. They require a later explicit owner-scoped
contract. The proposed audit vertical is not opened by this completion record.

## Verification

The isolated Foundation gate passes
**4571 tests / 5 skipped / 16 fixture warnings**, Ruff format/lint, mypy,
fresh migration and bootstrap. Its immutable stable Character compatibility snapshot
retains 786 manifest files and exact 266-file installed noneditable parity. Moving
Character candidates are preserved and explicitly outside this scope. The actual
physical/full-route schema pin has 59 tables, 82 guards, integrity `ok` and zero
foreign-key violations.

The initial failed gate
records one stale corruption fixture: the new SQL guard rejected the write before
the old attachment reader check. A test-only repair proves both defenses. The
production source is unchanged and the repeat collects the same 4576 cases.

The actual private synthetic CLI scenario
passes 20/20 checks, including the uncited root, both-copy atomic rollback,
restart/floor fencing, explicit resume, old archive reconciliation, numerical and
signal-hash continuity, byte-preserving retries and clean restarted retrieval/input/reply.
All real-provider calls and paid cost are zero; no live installation is erased.

The local cost record retains all
50 warm samples per operation on 190 synthetic rows: shared admission median/p95 **9.499/9.621 ms**, status **20.109/20.421 ms**, no-op resume **45.496/51.545 ms**.
Fresh schema proof and actual mirror callbacks are instrumented; main rows and
floor/binding/lock bytes remain unchanged. Host load is unknown. The uncollected
cost helper has a separate static/parity record
for its strict seven-command results admission and 22 positive/negative checks.
This supplement preserves the immutable full gate and identical production source.
No foreground, scaling, Character behavior, Linux or full 14.4 acceptance is implied.
