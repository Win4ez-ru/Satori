# ADR-0053: Parallel foundations and whole-state recovery

- Status: Accepted
- Date: 2026-10-02
- Authority: explicit user request to open a second non-Character workstream, analyze dependencies,
  and begin the unambiguous selected roadmap milestone after presenting the analysis
- Supersedes: ADR-0046 decision 3 only insofar as it makes 14.3 behavioral acceptance a prerequisite
  for starting 14.4. All source/evidence, permission/action, privacy and ownership decisions remain.
- Related: ADR-0001–0008, ADR-0012–0014, ADR-0022–0027, ADR-0045–0052

Continuation 2026-10-03: the user's explicit production-workstream instruction and ADR-0055
extend the scheduling authorization to subsequent bounded 14.4 slices. The historical first-slice
decision below remains preserved. Current boundaries and completed-slice gates are in `progress.md`.

## Context

Stages 0–14 have implemented canonical local owners. Character delivery remains under human
evaluation in Checkpoint 14.3; V10 is the last accepted provider behavioral baseline. V31/V33 and
14.3 are not accepted. The current roadmap serializes recovery behind this behavioral gate even
though consistent storage recovery has no dependency on expression selection or prompts.

There is no whole-state import/restore. Existing aggregate JSON exports are partial inspection,
and personality restore is an owner transition, not restoration of the installation. Early
episodic/semantic memory also lacks the visibility lifecycle needed before real external sources.
The user explicitly requested parallel development through stable interfaces, preserving the
Character workstream. The analysis selected the existing 14.4 milestone and was presented before
implementation.

## Decision

### Scheduling and scope

Maintain two explicitly named active boundaries in `progress.md`: Character at 14.3 Phase A,
and Foundations at 14.4.1. A completed foundation check never accepts Character or opens Stage 15.
14.4.2 and 14.4.3 retain separate opening/acceptance gates; 14.4 is incomplete until all three
slices pass. Other apparent serial dependencies are documented as proposals, not automatically
removed by this ADR. No paid inference, real-account access, external action or new behavioral
evaluation is authorized here.

The first slice delivers consistent local whole-state archives, private artifacts, validated
preview, and explicit offline restore into a **new absent database path**. It does not replace a
live installation, merge identities, physically erase memory, add auth, or resume pending work.

### Stable boundary with Character

Recovery reads a pure stored snapshot through an application-owned storage port. It does not call
status reads that initialize state, materialize decay, form memories, invoke a provider, or select
Character expression. It imports no Character renderer, candidate policy or prompt manifest.
Canonical identity, owner state, versions, evidence lineage and audit remain authoritative.
Transient dialogue/coherence/agency projections are not archive contents.

Future integration consumers must use immutable versioned owner reads and typed proposals.
Permission decisions must have no relationship-trust or Character-policy input. A later Character
implementation change is an adapter/schema compatibility issue, not a new recovery owner.

### Archive and validation contract

Use a bounded, versioned local bundle containing exactly `manifest.json` and a consistent SQLite
snapshot. SQLite's backup API captures committed tables, constraints and audit in one snapshot,
including WAL-resident committed data; filesystem copying of a live database is forbidden.
All stored domain records and optional derived embeddings are retained. The initial format is
SQLite-specific and supports the exact current Alembic/schema fingerprint. Cross-engine and older
schema migration are rejected until separately implemented; versions are never guessed. Exact
schema comparison normalizes only Alembic's nondeterministic order of complete named constraints,
preserving column order and every definition; it never admits additional or weakened constraints.

Validate integrity in a second private offline replica with a writable SQLite connection and
`query_only` enforcement. The local SQLite build's read-only integrity check omitted CHECK
failures; a corruption regression requires the replica to reject them across tables. The schema
allowlist is admitted first, extension loading is disabled, and no arbitrary SQL is applied.
Only already isolated snapshot bytes are copied; the live database is captured exclusively with
the backup API. Replica validation does not alter canonical state or the input snapshot.

The strict manifest binds format/app/schema versions, creation time, identity, schema fingerprint,
table counts, snapshot size and SHA-256 checksum. Preview validates archive bounds/member names,
checksum, SQLite integrity, foreign keys, exact trusted schema shape and identity/count agreement
in private staging before activation. An archive checksum detects corruption; it is **not** a
signature or proof of a trustworthy source. No uploaded SQL, views, triggers, extensions or
arbitrary schema is executed. Credentials/configuration/runtime logs are outside canonical state
and are not bundled. Existing raw conversation/evidence inside SQLite remains sensitive.

Activation requires the SHA-256 of the complete reviewed archive, revalidates the same bytes, and
publishes the validated snapshot only to an absent regular-file target. Existing target databases
and sidecars are rejected. Application engines/jobs are not opened by restore. Pending records
remain historical canonical state; executing processing later requires a separate explicit use
case. Restore does not issue or revive a capability grant.

### Private artifacts and audit

One infrastructure artifact writer publishes archives and existing aggregate file exports.
New directories are 0700 and files 0600. Bind operations to an opened parent directory, reject
final symlink/non-regular targets, stage privately, flush/fsync, and publish atomically. Creation
does not overwrite; overwrite is explicit for export artifacts only. Restore never overwrites.
An export cannot target the source database or its journal/WAL/SHM. Existing aggregate stdout
inspection remains explicit and compatible; its file output is labeled partial.

Recovery produces a separate private content-free operation receipt (operation ID, kind,
timestamp, archive checksum, identity, schema and counts). It is not a new self/evidence event and
must not change canonical owner/audit state merely because backup was read. Publication/audit
failure must be reported honestly; a diagnostic log is not proof of successful recovery.

### Acceptance

Offline tests cover consistent capture including WAL, full relational equality after restart,
private modes, symlink/overwrite refusal, corrupt/truncated/unknown-version/modified-schema/FK
archives, digest-bound activation, failure before publication, target preservation, and no
provider/implicit state initialization. Synthetic manual drill exports a canonical conversation,
previews, restores to another database, reopens identity/history and compares stored state/audit.
The Foundation run rebuilds the wheel first. Behavioral acceptance is not inferred from these
checks, and no real user data is restored or deleted during verification.

Encryption/key custody and sensitive production backup readiness remain 14.4.3 gates. Slice
14.4.1 is a private local development recovery facility, not encrypted backup or multi-user auth.

## Consequences

The project can gain durable recovery while Character evaluation continues. This is a product
capability and shared prerequisite for threads/external sources, not a Character refactor. Exact
schema admission keeps the initial surface small and fail-closed, at the cost of requiring a
future explicit compatibility adapter after schema changes. Whole-database restoration preserves
one canonical installation and does not create a second domain writer. Live replacement, selective
merge, erasure resurrection policy and encryption require their own subsequent gates.
