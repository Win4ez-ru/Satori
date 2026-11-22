# Terminal Derived Knowledge and Evidence Continuity

Status: 14.4.2g implemented and macOS engineering-verified on 2026-10-07.
Decision: [ADR-0066](decisions/0066-terminal-derived-knowledge-and-evidence-continuity.md).

The three existing owners handle semantic memory, current models and positions/inclinations.
This operation extends the existing mixed body ledger and transaction coordinator. It introduces
no new persistent personality, relationship state, provider dependency or source-policy counter.

## Selection and ownership

Fresh selection follows COMPLETE actual-input dependency receipts, every historical payload
version and accepted physical evidence. Citation strings and equal content are insufficient.
An affected aggregate with multiple sources in the same verified counterparty scope is selected
whole. Independent canonical USER messages are retained. Affected cross-counterparty support,
UNKNOWN ancestry, missing receipts, cycles and capacity overflow block execution.

Deterministic source withdrawal now carries versioned owner provenance in the same transaction:
the successor references the exact prior aggregate version, preserves proof status and legacy
attribution, and remains unavailable for new input. This repairs the shared source-policy writer,
rather than adding guessed ancestry to the erasure planner. Existing historical stores with
missing successor receipts remain UNKNOWN; automatic reconstruction is not authorized.

| Existing owner | Aggregate | Selected SQL NULL fields |
|---|---|---|
| Semantic memory | Semantic claim | `value`, `normalized_value` |
| Current models | User claim | `value`, `normalized_value` |
| Current models | World claim | Values and both subject labels |
| Positions | Position | Proposition and normalized proposition; all evidence quotes and normalized signatures |
| Positions | Inclination | Topic, alternative topic and both normalized copies |

Source-content scalar JSON numbers and booleans are included in this operation. SQL NULL is
required; JSON `null` is not a terminal body. Numeric confidence, scores, timestamps, statuses,
aggregate versions, identifiers and revision history are retained exactly. Evidence and revision
membership are part of each exact approved material digest.

Only the five aggregate tables receive `body_state` and `body_action_id`. Position evidence keeps
its original physical column set and uses paired NULL quote/signature values. Its authority comes
from the exact parent action and complete evidence membership. Existing owners execute all writes
inside the same main transaction, after the independent floor decision is durable.

## Historical continuity

Old episode/dialogue classes, approvals, JSON and hashes remain unchanged. A knowledge action
records an explicit before/after full-row fingerprint for each selected position evidence row.
Its origin is an exact preceding Dialogue witness, an exact current Knowledge witness, or a
literal membership proof captured while the canonical USER is still available. A later Dialogue
operation can preserve attribution to already terminal knowledge through this approved chain.
No component reconstructs a quote from a NULL source or promotes historical attribution into
new learning evidence.

Ordinary startup, dispatch and commit require the complete trusted floor head, exact receipts,
actual terminal owner material and the historical transition chain. A copied ACK or old main
database cannot satisfy that proof. Exclusive operator preview can inspect an incomplete main
projection while proving every actual terminal row; preview never writes an ACK. Fresh review
may supersede stale pending material only through the same exact predecessor/root/fingerprint
contract.

Ordinary proof checks SQL NULL owner material before loading historical evidence rows. Effective
knowledge witnesses also require a NULL-constrained read of quote and normalized signature;
rejection after reading restored content is insufficient. Privileged pending review may recognize
only an exact approved original position material, never a generic historical before/after
fingerprint choice. Changed terminal-origin material without an exact authorized predecessor
requires a separately reviewed repair contract.

## Operator controls

The main schema is `0020_knowledge_bodies`; the independent policy floor explicitly upgrades
to `0005`. Earlier floor actions remain unchanged. Controls are local, scoped, metadata-only
and provider-free:

```bash
satori sources erasure-knowledge-policy --upgrade --reviewed
satori sources erasure-knowledge ROOT --intent-request-id INTENT --preview
satori sources erasure-knowledge ROOT --intent-request-id INTENT --apply \
  --request-id ACTION --expected-generation G --expected-erasure-revision E \
  --expected-body-revision B --sha256 REVIEW_DIGEST
satori sources erasure-knowledge ROOT --intent-request-id INTENT --status --request-id ACTION
satori sources erasure-knowledge ROOT --intent-request-id INTENT --resume --request-id ACTION
```

An existing durable intent and applied source exclusion are prerequisites. Fresh approval is
specific to this operation and exact current store/schema/head. A failure before main commit
leaves the floor action pending and the main transaction unchanged. Resume executes and proves
the entire mixed head. An uncertain commit is considered complete only after fresh proof.
Source/intent repair does not authorize or acknowledge body execution.

Normal owner queries filter available bodies before mapping or LIMIT. Terminal records expose
only typed unavailable headers. Decay, merge, upsert and reactivation cannot recreate their
content or refund retained keys/caps. Current-input admission checks availability independently
of the unchanged aggregate version.

## Bounds and remaining gates

Each action permits 256 targets and 512 witness transitions, with 131072 bytes of serialized
target material. The retained mixed stream permits 4096 actions, one million combined historical
references and 64 MiB. Main census remains limited to 100000 rows, 64 MiB and one million units
of census/reference assessment work. Census indexes are built once; ordinary exact-owner proofs
read bounded chunks of 500 aggregate IDs.

Every public result keeps `physical_erasure=false`. Opaque reflection/appraisal/audit content,
numeric history, hashes, free pages, SQLite sidecars, archives, staging, OS snapshots, provider
caches/logs and external copies remain outside this body operation. Authenticated access,
encryption/key recovery, Linux verification and acceptance of the whole 14.4 are separate gates.
Character tuning and Character acceptance are outside this workstream.

Removing selected knowledge bodies does not undo previously committed numerical personality,
relationship or affect trajectories. Those retained states are not fresh source evidence and
must not reconstruct the removed content; this operation does not certify that all historical
influence of the source has disappeared. Retention and disposition require their own owner
contracts, rather than an erasure-specific Character mutation.

## Verification and next dependency

The actual synthetic CLI/restart/recovery scenario
passes all eighteen checks. It exercises selected content in all five kinds, normalized copies,
position quotes, number/boolean values, atomic rollback after partial owner writes, a surviving
pending floor action, fresh-process resume, foreign-scope denial, old-archive withholding and
explicit recovery. Restart retrieval is empty for the removed knowledge; the restarted fake
foreground request and reply do not contain the synthetic secret. A later Dialogue B operation
uses approved terminal position evidence and proves the complete new mixed head. Real provider
calls and paid cost are zero. These controls do not evaluate sampled Character output.

The rebuilt isolated Foundation gate passes
Ruff format/lint on 646 files, mypy on 500 files, **4330 passed / 5 skipped / 14 warnings**, and
fresh migration/bootstrap. Actual main admission verifies `0020_knowledge_bodies`, 59 tables,
58 SQL guards, integrity `ok` and no foreign-key violations. All 736 frozen files remain exact;
249 installed noneditable package files and all 65 owned Python overlay files match.
Moving Character candidates are excluded through the stable compatibility snapshot; their
current implementation and behavioral acceptance are not evaluated here.

The local cost record contains fifty warm
samples per phase, two excluded warmups and all outliers, after both supplied Foundation jobs
finished. The synthetic store contains 171 rows:

| Direct control | Median / p95, ms | Main SQL queries | Fresh schema queries |
|---|---|---|---|
| Shared admission at body head 3 | 49.596 / 93.988 | 121 | 1 |
| Knowledge status | 61.650 / 95.142 | 263 | 3 |
| No-op resume | 115.751 / 153.023 | 696 | 6 |

Every shared-admission sample performs actual mirror proof. No-op main rows, floor bytes and
binding bytes remain unchanged. Other host workloads are unknown; this is small-store warm
control evidence, not a foreground/Character or large-store performance acceptance. The main
SQL counts do not include the independent floor's direct SQLite queries.

Next proposed slice is **14.4.2h — Retained Payload Disposition**, starting with terminal
`reflection_proposals.payload` through the existing Reflection owner and mixed head. Run/outcome,
numerical, source/replay metadata and spent budgets need explicit retained representations.
Affect/appraisal and audit bodies need their own classification/retention contracts; deletion
must not alter replay hashes or invent causal scope. This proposal does not open 2h or authorize
Character changes. Subsequent slices require a separate user continuation and architecture.
