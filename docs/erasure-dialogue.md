# Terminal Dialogue Bodies & Historical Provenance

Checkpoint 14.4.2f was explicitly opened on 2026-10-06 under
[ADR-0063](decisions/0063-terminal-dialogue-bodies-and-historical-provenance.md).
The macOS engineering gate is complete: 4207 passed / 5 skipped, strict rebuilt package parity and
fifteen synthetic CLI/restart/recovery checks. See the Foundation report.
Whole 14.4 acceptance and Linux process verification remain open.

## Selected removal and retained meaning

InteractionLog removes the actual selected `conversation_messages.content` values by writing SQL
NULL with terminal `body_state` and an approved application reference. Selection includes the
excluded canonical USER, its exact paired assistant and all assistant replies with COMPLETE proved
actual-input dependence on that root. The dependency graph includes uncited inputs. Independent
later USER messages and unrelated complete roots, including equal text from another counterparty,
remain. Matching words never establish ownership or dependence.

The plan blocks on UNKNOWN or missing assistant ancestry anywhere in the admitted identity,
unproved episode ancestry, incomplete interaction pairs and affected cross-counterparty assistant
descendants. It does not truncate dependencies to make a plan executable. Selected episodes must
already have trusted terminal proof under [14.4.2e](erasure-bodies.md); a root without episodes
does not need a fabricated episode action.

Identity, scope, message/interaction IDs, roles, sequence, timestamps, numerical history, audit and
provider receipts remain. No numerical rollback, spent-budget refund or new personality,
affect/relationship state is introduced. No Character tuning or provider invocation is needed.

## Historical proof after the canonical body is gone

Normal `HistoricalMessage` and `ConversationInteraction` still require nonblank content. Separate
metadata-only headers represent terminal records. History/recent queries exclude terminal pairs
before limits and normal aggregate mapping; USER-only model/position/relationship extraction can
still use an independent available USER whose dependent assistant was removed. Post-response
processing skips terminal interactions through a header check before loading content, with no
provider call, retry or degraded learning result. Old client-request replay is explicitly denied;
it cannot generate replacement prose for the removed reply.

Before NULL, the reviewed action captures `HistoricalEvidenceWitness` values for the exact retained
evidence rows in a closed set of five tables: memory, user-model, world-model, position and
inclination evidence. Each private witness binds table, evidence ID and full-row fingerprint.
Literal nonblank quotation membership is checked only for memory and position evidence, whose
tables actually store quotes. Quote-free model and inclination edges require their exact canonical
or reflection/upstream chain, without inventing a quotation claim. Already-erased episode evidence
uses its trusted episode proof.

The complete approved action and actual terminal canonical skeleton must still verify after
removal. Modified or newly inserted evidence does not inherit an old witness. Malformed support,
unbounded input or corrupt receipt JSON fails with a closed content-free reason; evidence is not
silently omitted from the proof. Historical witnesses support retained attribution, inventory and
audit only. They never authorize fresh learning, provider disclosure or technical permission.
Live evidence continues to require available content and current source eligibility.

## One floor head and one write path

Explicit `policy_0004` admits a closed `dialogue_body_erasure_v1` action in the existing body stream.
The single `body_revision` and `body_head_digest` cover both episode and dialogue actions. Existing
episode dataclass fields, JSON envelopes, approval hashes and episode-only head digests remain
unchanged. Owner kind separates effective target keys. An episode or intent approval cannot
authorize dialogue removal; a fresh dialogue-specific approval is required.

The preview binds exact targets and witnesses, original/terminal digests, source generation,
erasure revision, body revision, schema and complete reviewed-store freshness. Source repair and
intent acknowledgment must already be current. The durable floor action is committed first.
Under one main transaction the shared coordinator verifies every selected preimage, records the
mixed receipts, invokes existing owner writers, verifies actual terminal bodies and retained
witnesses, then acknowledges the whole body head.

Failures before main commit roll back all main changes atomically while the floor decision remains
pending. An uncertain commit outcome or later verification failure is also reported as pending;
that result does not certify that bodies remain available. Fresh status/resume must prove actual
completion. Ordinary reads and provider admission require their own fresh complete-head proof.
Explicit resume materializes the current effective approved whole head, including later approved
actions or supersession; it never expands selection beyond that head. Old backups, stale bindings
and forged receipt/ACK metadata cannot substitute for actual body proof. Source/intent repair cannot
acknowledge a body head.

Operator resolution/preview use the full floor-approved action stream under the exclusive lease,
including pending actions. Contiguous revisions, binding and digest must match the trusted head;
actual terminal skeletons/witnesses still supply the historical proof. This lets an earlier NULL
root coexist with a later pending action without blocking review/resume. Incomplete main receipts
are never authority. Ordinary admission still requires complete main materialization. An empty
historical context when every canonical USER is available grants nothing and preserves existing
2e changed-target reapproval. Corrupt ORM JSON decoding is also mapped to a closed typed denial.

Main migration `0019_dialogue_bodies` preserves FKs and attribution and installs five additional
SQL guards, for thirteen total. Initial NULL requires an exact completed USER-sequence-1 /
ASSISTANT-sequence-2 pair in one session. Terminal messages and interactions cannot be resurrected,
re-paired, deleted or have retained attribution changed. The InteractionLog owner also verifies
original and terminal digests before any mutation. Downgrade is refused. Exact schema recovery
supports revisions 0016/0017/0018/0019; legacy enrollment cannot manufacture canonical quotation
proof from NULL, and damaged 0019 cannot fall back to a legacy admission path.

## Bounds and failure policy

Each dialogue action admits at most 256 message targets and 512 witnesses. The unchanged target
JSON cap is 131072 bytes. The floor permits at most 4096 actions, one million references and
64 MiB; references include every historical target and witness across the mixed head. Existing
main assessment bounds remain 100000 rows, 64 MiB of logical data and one million work units.
Projected capacity is checked before approval/append. Overflow denies the entire plan.

Private witness/fingerprint metadata is not public payload. Preview/status neither create stores
nor enroll, migrate, repair or call providers. Corrupt or stale data causes a typed denial;
interrupted approved work has a durable pending state and explicit recovery command.

## Operator commands

Use the existing configured local main store, installation binding and independent policy floor.
First upgrade the floor explicitly; this adds support but does not authorize removal:

```text
sources erasure-dialogue-policy --upgrade --reviewed
sources erasure-dialogue ROOT --intent-request-id INTENT --preview
sources erasure-dialogue ROOT --intent-request-id INTENT --apply --request-id ACTION \
  --expected-generation G --expected-erasure-revision E --expected-body-revision B \
  --sha256 APPROVAL
sources erasure-dialogue ROOT --intent-request-id INTENT --status --request-id ACTION
sources erasure-dialogue ROOT --intent-request-id INTENT --resume --request-id ACTION
```

The executable entry point is `uv run --no-sync satori`. The digest must come from a fresh
operation-specific preview. Changed bodies require status, a new preview/review and a new action;
an old digest is not permission to remove a different target. All controls are local and scoped.

## Verification and practical limits

The synthetic scenario performs actual CLI subprocess
operations, a mid-owner failure after two NULL writes, rollback, fresh-process withholding/resume,
restart retrieval and one capturing fake foreground call. It checks fifteen conditions: three
actual dialogue bodies removed, independent USER retained, exact replay/scope, zero control provider
calls, clean retrieval/provider projection, pre-dialogue archive fencing and explicit resume,
and historical inventory. Real providers and paid cost are zero; live private data is untouched.

The final rebuilt frozen compatibility gate passes Ruff format on 618 files, lint, mypy on
475 files, 4207 passed / 5 skipped (14 SQLite datetime-adapter deprecation warnings),
fresh migration/bootstrap and exact 0019 schema/integrity/FK admission. Installed Python files
match frozen sources exactly; 521 non-pinned code/test/migration paths match the working tree.
Moving Character candidates are excluded and their files remain unchanged by verification.
The 105-case repair run is included in the full suite, not an additional unique test total.

Automated coverage additionally checks old episode byte/hash compatibility, single mixed head,
all three freshness counters, quota/lease enforcement, owner-kind/request collision, UNKNOWN and
cross-scope blockers, equal-text isolation, SQL guards, malformed pairs, original/terminal digest,
literal witness capture and mutation rejection, forged ACK, old client replay, deep JSON corruption,
legacy enrollment, recovery and post-response boundaries.

Local cost evidence records fifty warm samples per
phase on 123/124 main rows after both known Foundation pytest jobs completed. Other host workloads
were not checked; whole-host idle is not claimed. Shared admission median/p95 is 4.991/25.852 ms
at episode-only head and 5.212/6.503 ms at mixed head. Dialogue status is
14.273/15.435 ms; no-op resume is 30.507/31.869 ms.
The concurrent-test measurement is
preserved separately; these observations do not establish a causal code-performance comparison.
The episode-only series retains a 121.694-ms maximum outlier; no slow sample is discarded.
Main SQL statement counts per sample are 24/33 for shared admission, 175 for status and 518 for
no-op resume; fresh schema query counts are 1/1, 3 and 6. Actual shared admission invokes the
mirror verifier on every sample. This tiny-store measurement is not a production-scale or
Character benchmark; no-op floor bytes/main rows remain unchanged.

## Remaining work and next proposed vertical

`physical_erasure` is always false. Claims, semantic/model/position/inclination wording and some
retained quotes, reflection/appraisal/audit bodies, identifiers and numerical effects remain.
SQLite free pages, WAL/journals, archives/staging, OS snapshots, logs/caches and external/provider
copies are not cleaned or certified. Authenticated local access, encryption/key recovery,
whole-installation floor freshness and Linux process verification remain separate gates.

The next proposed vertical is **14.4.2g — derived knowledge body handling**, with fresh owner
contracts for semantic/current-model/position/inclination content and its provenance. It must
honor already-terminal canonical roots and exact historical witnesses without guessing missing
quotes or introducing a generic cascade. Mutating retained evidence requires an explicit policy
for historical witness continuity. This proposal does not open the vertical automatically.
