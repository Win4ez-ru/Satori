# User and world models

Status: Stage 9 implementation accepted; contract fixed by
[ADR-0022](decisions/0022-evidence-typed-user-and-world-models.md).

## Purpose and boundaries

The User Model and World Model are small current, revisable projections over canonical user
evidence. They answer “what is currently reported or cautiously inferred about this counterparty
and their active situations?” They are not chat history, semantic memory, relationship state,
external truth or Satori's own beliefs.

```text
canonical same-counterparty user messages
-> bounded typed formation proposal
-> UserModelManager and WorldModelManager decisions
-> atomic claim/evidence/revision/audit commit
-> current/history/export read models
-> bounded untrusted future-turn context
```

Every claim is partitioned by Satori identity and opaque counterparty ID. The local default is not
authentication and does not make multi-user deployment safe.

## Closed v1 vocabulary

User claims have subject `counterparty` and one of:

| Predicate | Cardinality | Value | Freshness |
|---|---|---|---|
| `display_name` | single | bounded text | no automatic expiry |
| `occupation` | single | bounded text | 180 days |
| `residence_city` | single | bounded text | 180 days |
| `goal` | multi | bounded text | 180 days |
| `project` | multi | bounded text | 180 days |
| `important_person` | multi | bounded text label only | 180 days |

World claims use a bounded subject label and a closed subject/predicate/status combination:

| Subject | Predicate | Allowed values | Freshness |
|---|---|---|---|
| `project` | `status` | planned, active, paused, completed, cancelled | open 90 days; terminal 365 days |
| `situation` | `status` | active, resolved, cancelled | active 30 days; terminal 365 days |
| `commitment` | `status` | planned, in_progress, fulfilled, broken, cancelled | open 90 days; terminal 365 days |
| `outcome` | `status` | pending, occurred, not_occurred, cancelled | pending 90 days; terminal 365 days |

V1 does not store arbitrary person attributes, contact details, demographics, medical/mental
labels, inferred vulnerabilities, schedules, locations or a generic relation graph. An important
person is only the bounded label explicitly used by the counterparty; it is not permission to
profile that third party.

## Claim and evidence contract

```text
ModelClaim
  claim_id, schema_version, aggregate_version
  identity_id, counterparty_id, owner=user|world
  subject_kind, normalized subject label, predicate
  typed value, epistemic_kind=explicit_fact|inference|hypothesis
  confidence, status=current|superseded|disputed|retracted|expired
  valid_from, valid_until, last_observed_at, expires_at
  superseded_by_claim_id, formation/policy/normalization versions

ModelClaimEvidence
  evidence_id, owner, claim_id
  source_message_id, source_interaction_id, observed_at

ModelClaimRevision
  revision_id, owner, claim/version/decision IDs
  created|strengthened|superseded|disputed|retracted|expired
  before/after status/confidence/expiry, reason, occurred_at
```

One terminal formation decision is unique per source interaction and formation version. Every
accepted proposal cites the current message; inference additionally needs two independent
messages and interactions. Provider confidence can only lower deterministic caps. Assistant
output, retrieved data and existing claims may inform generation but never count as new roots.

## Temporal semantics

`expires_at` is computed by owner policy from predicate/status and the newest accepted evidence;
the provider cannot choose a TTL. At `as_of >= expires_at`, a current read treats the claim as
stale and excludes it from generation even before a maintenance commit. Expiry maintenance is
deterministic and appends an `expired` revision/audit. It does not call a model and does not delete
the old claim.

Explicit correction or a newer explicit single-valued/status claim supersedes rather than
rewrites. Exact compatible re-observation adds a new root, updates confidence within its cap and
extends freshness. Inference/hypothesis never silently becomes explicit. Conflicting
inference-like values stay disputed and out of current context.

## Context and inspection

User and world claims are serialized in their own explicitly untrusted data envelope after
trusted character/policy sections. Current same-counterparty claims only are eligible. Selection
is deterministic, bounded and relevance-aware; a category-level fallback may select the only
unambiguous active project/situation. The envelope preserves epistemic kind and freshness and does
not contain source text.

Local list/inspect/export surfaces may reveal sensitive values and source IDs, so they are explicit
debug/user actions. Their optional `--counterparty COUNTERPARTY_ID` selects one opaque partition;
without it they use `SATORI_DEFAULT_COUNTERPARTY_ID`. Normal logs contain metadata only. Export is
partitioned by counterparty and contains enough policy/schema/provenance data for restart/round-trip
equality; it is never a mutable second store.

## Required evaluation

- explicit/inference/hypothesis kinds and confidence caps;
- exact replay and independent-root deduplication;
- correction, incompatible inference conflict and non-destructive history;
- deterministic current/stale/expired projection with clock boundaries;
- restrictive source retention and orphan-provenance rejection;
- two-counterparty isolation in storage, context and export;
- restart and export round-trip equality;
- bounded relevant context and no Stage 6/relationship feedback;
- manual project lifecycle `planned -> active -> completed` with state lineage and replies.
