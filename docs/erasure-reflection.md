# 14.4.2h — Terminal Reflection Payloads & Retained Continuity

The explicit continuation opens the first Retained Payload Disposition vertical under
[ADR-0068](decisions/0068-terminal-reflection-payloads-and-retained-continuity.md).
The existing Reflection owner clears selected `reflection_proposals.payload` to actual SQL NULL.
This is a logical content transition; every public result retains `physical_erasure=false`.

## Scope and retained state

The complete candidate JSON is content, including propositions, observations, topics and numeric
candidate fields. It is removed as one body. Proposal headers, the entire parent run, fixed source
descriptors, attempts and outcomes remain exact. Existing numerical effects and downstream
references remain; retry caps, consumed roots and policy versions are not refunded or rewritten.
Proposal payload references retain their immutable logical version 1.

An executable target requires a completed or exhausted parent after ordinary source-owner
reconciliation. Erasure never completes routing and creates no rejection outcome. An exhausted
proposal with an existing empty outcome set retains that exact set. Once any child is erased,
SQL guards freeze retained run/source/attempt/outcome data and membership and proposal headers.
An independently approved terminal transition of another existing child is still possible.
Conflicting `INSERT OR REPLACE`, including across parents, is guarded explicitly.

Affected ancestry is established through canonical support, the entire fixed source window and
COMPLETE actual-input receipts, including projected upstream payloads. Candidate citations are a
subset of these inputs. An uncited source may therefore select every proposal in a run. Unknown,
malformed, overflowed, missing-version or affected cross-counterparty ancestry blocks destructive
approval. Surviving support does not authorize regeneration or partial rewriting of a candidate.

## Interfaces and availability

Normal `ReflectionProposal` requires a contentful dictionary. Available reads filter owner state,
action marker and SQL NULL before loading or mapping payloads. Terminal inspection separately
returns `ReflectionProposalHeader` without a payload. Complete metadata-only proposal IDs preserve
cardinality even when source admission withholds an otherwise available proposal.

Completed apply replay returns run metadata before reading payloads or live source quotes.
Exhausted replay remains non-routable. Routing compares all retained proposal IDs with outcomes;
a missing unrouted body is a closed failure. Dispatch and final commit recheck availability at
the same logical version 1. A previously prepared result cannot become fresh learning after NULL.
Personality adapters perform the same check before payload conversion; numerical policy is unchanged.

No new Character contract is required. Character continues to receive existing available-context
interfaces. This vertical changes neither personality/emotion/relationship algorithms nor prompts,
models, expression or behavioral acceptance.

## Shared ledger and recovery

Main `0021_reflection_bodies` changes only the Reflection proposal representation and installs
15 SQL guards. Independent `policy_0006` adds the fourth closed action to the existing mixed
`body_revision` stream. Episode, Dialogue and Knowledge action bytes and old hashes remain exact.
There is no new source eligibility generation or independent erasure ledger.

Each `ReflectionBodyTarget` binds proposal ID, run ID/version, payload version 1, original and
terminal digests and an exact reviewed metadata-reference count. Material includes the raw
selected proposal, whole run, all its sources and attempts, and selected proposal outcomes.
Overlap in counts is intentional bounded accounting; batched storage reads load shared parents once.

Commit is floor-first under the exclusive barrier. The main coordinator preflights the whole
effective mixed head before writing any body or application receipt. Reflection's sole
`erase_payload_bodies` writer validates exact targets and performs no independent commit.
The one main transaction then proves actual SQL NULL and complete frozen material before ACK.
Strict upstream source proof is checked again during pending resume, not only during preview.

Ordinary admission checks actual Reflection terminal bodies and parent material before historical
Dialogue witnesses can load protected rows. Copied receipts or ACKs do not admit an old main
backup containing original payloads. Resume accepts only an exact approved original or terminal
target; changed material requires a new review. Uncertain completion stays withheld.

Approved terminal proofs permit historical attribution only. They never reconstruct an old quote,
infer a missing source from JSON field names, or authorize fresh provider input. The five existing
historical evidence witness formats remain unchanged. Reflection removal does not rewrite them.

Recovery transports exact supported snapshots through revision 0021 without migration or privacy
acceptance. Exact older schemas use closed metadata projections for administrative reconciliation;
they cannot impersonate a damaged current schema. A downgrade requires a separate offline owner
policy. Operational commands do not migrate the main or floor databases implicitly.

## Reviewed commands

After main migration, reviewed source exclusion and a durable intent, upgrade the independent
floor explicitly:

```bash
satori sources erasure-reflection-policy --upgrade --reviewed
satori sources erasure-reflection ROOT_MESSAGE_ID --intent-request-id INTENT_ID --preview
```

Apply requires the fresh preview's generation, erasure revision, body revision and digest:

```bash
satori sources erasure-reflection ROOT_MESSAGE_ID --intent-request-id INTENT_ID --apply \
  --request-id ACTION_ID --expected-generation GENERATION \
  --expected-erasure-revision ERASURE_REVISION --expected-body-revision BODY_REVISION \
  --sha256 APPROVAL_DIGEST
```

`--status` and `--resume` use only root, intent and action ID. Status remains read-only; a proved
no-op resume preserves main rows and floor/binding bytes. Root/counterparty scope is proved before
intent or action lookup. Control commands construct no providers and disclose no payload text.

Bounds remain 256 targets per action, 131,072 serialized bytes, 4,096 mixed actions, one million
combined references/assessment work, 100,000 scanned main rows and 64 MiB logical material.
Existing three-proposal, twelve-source and two-attempt Reflection caps remain unchanged.

## Verification and remaining work

The first vertical's macOS engineering gate is complete on 2026-10-10. The actual synthetic
CLI/restart/recovery scenario passes 20/20 checks,
including fault rollback, restart, actual old archive restoration, clean retrieval/provider input
and later historical Dialogue continuity. Controls use zero real providers and USD 0.

The rebuilt isolated Foundation gate passes
**4448 passed / 5 skipped / 14 warnings**, Ruff format/lint, mypy, fresh migration and bootstrap.
Actual schema admission proves revision 0021, 59 tables and 73 SQL guards, exact pin, integrity
`ok` and zero FK violations. All 759 frozen manifest files and 257 installed noneditable Python
files remain exact. The moving Character tree is excluded; this is not its behavioral acceptance.
The initial failed full gate remains
visible: four stale fixtures caused 24 failures; the same 4453 collected cases then passed without
production changes or test removal.

The local cost record contains 50 samples per
operation on a 181-row warm synthetic store, after two excluded warmups: shared admission
median/p95 7.454/7.792 ms, status 17.680/18.806 ms and no-op resume 40.132/41.343 ms.
Every sample includes fresh actual schema proof, with zero provider calls and unchanged
main rows and floor/binding/lock bytes. The uncollected measurement helper has a separate
rebuilt static/parity record after fixing
its imported-alias counter and two missing synthetic embedding vectors. The immutable full gate
is unchanged. Host load is unknown; these timings do not establish scaling or foreground latency.

Remaining retained content includes affect/appraisal/audit JSON and free-form checkpoint reasons.
Each needs its own content-versus-accountability contract and owner, rather than generic JSON
deletion. Numerical trajectories, identifiers and hashes remain. SQLite free pages, WAL/sidecars,
old archives/staging, OS caches, external/provider copies and full-installation/floor rollback
are outside this logical guarantee. Authenticated access, encryption/key recovery, forensic cleanup,
Linux process verification and full 14.4 acceptance remain open.

The next proposed 14.4.2h vertical is owner-controlled appraisal explanation disposition. Preserve
the strictly validated eleven numeric appraisal scores, deltas, snapshots, versions and historical
signal hashes. Treat provider-neutral `reason_codes` and their exact
`emotion.transition_applied` audit duplicate as content through one event-specific owner contract.
Do not remove a whole numeric appraisal envelope or substitute an empty appraisal proposal.

A prerequisite is exact transition-to-`source_input_id` persistence at prospective commit:
current source IDs and provider citations do not record the full uncited retrieval/previous-exchange
input footprint. Historical transitions without that receipt remain UNKNOWN; no guessed backfill
or familiar-field-name classification can certify them. Separate numerical-history/header reads,
strict schema/type classification, floor-first proof and atomic removal of both explanation copies
must precede acceptance. Numerical emotion/mood/personality influence remains a declared residual.
Character behavior, appraisal prompts/formulas and emotion tuning remain a separate workstream.
Completion of this vertical does not open the proposed next vertical.

The historical next-vertical proposal above was subsequently implemented under
[ADR-0070](decisions/0070-appraisal-explanation-bodies-and-exact-input-provenance.md).
See [paired Appraisal handling](erasure-appraisal.md) and [progress](progress.md)
for its separate engineering gate, remaining audit/decision contracts and the next
proposed vertical. Reflection's original verification record remains unchanged.
