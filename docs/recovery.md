# Whole-state recovery — Checkpoint 14.4.1

Status: implemented and engineering-verified in the parallel Foundations workstream, ready for
slice review. ADR-0053 opens this slice without accepting Character 14.3. Whole Checkpoint 14.4
remains incomplete. Final Foundation passed 3326 tests / 4 skipped; 66 recovery checks and the
synthetic real-CLI restore/restart drill passed. Exact environment/results are in `progress.md`.

## Product behavior

An explicit local export captures the same stored Satori: identity, activation seed provenance,
traits/baselines/values, raw conversations, memories/evidence, semantic claims/revisions,
affect/relationships, User/World Model, positions/inclinations, reflection/personality history and
canonical audit. Optional stored embeddings are included. This is independent of current
Character selection/rendering, provider configuration and transient session projections.

```bash
uv run --no-sync satori state export --output ./private/satori.state
uv run --no-sync satori state import ./private/satori.state --preview
uv run --no-sync satori state import ./private/satori.state --activate \
  --target /absolute/new-location/satori.db --sha256 HASH_FROM_PREVIEW
```

`state backup` is an alias of `state export`. Export overwriting requires `--overwrite`.
Restore only creates a new absent path; it never replaces the configured canonical database.
The complete target family (database, WAL, SHM and journal) is checked before validation and
again immediately before publication; an intervening sidecar aborts without deleting it.
After reviewing the result, explicitly select the restored database with `SATORI_DATABASE_URL`
for normal commands. Stop applications using the restored location before any manual file move.
No provider, migration of the live database, source fetch, job resume, or domain mutation is
performed by export/preview/restore. Failed/pending records are preserved, not automatically run.

Existing `models export`, `positions export`, `positions inclination-export` and `personality
export` are **partial inspection**, not recovery. Their JSON/stdout schemas remain compatible.
File output uses the same private artifact writer, refuses an existing file by default and permits
explicit `--overwrite`. No export may overwrite the configured DB or SQLite sidecars.

## Boundary and format

`application.recovery.StoredSnapshotPort` is a pure stored-state port, independent of Character.
`SQLiteStoredSnapshot` implements consistent capture with SQLite backup, including committed WAL
pages. No `EnsureAffectiveState`, relationship initialization, decay, expression projection or
provider call occurs. This is recovery transport, not a second aggregate writer.

The initial bounded archive is a ZIP_STORED bundle with exactly two members:

- `manifest.json`: required format/app versions, creation time, identity, Alembic revision,
  exact trusted schema fingerprint, table counts, snapshot size and SHA-256;
- `snapshot.sqlite`: complete consistent stored database, at most 256 MiB.

The manifest is at most 64 KiB. Unknown members, encryption/compression, extra fields, missing
versions, unsupported schema/revision, checksum mismatch and invalid references fail closed.
Supported revisions are exactly **0013_conversation_failure_reason**, **0014_memory_scope**,
**0015_memory_lifecycle**, **0016_source_policy**, **0017_erasure_intent**,
**0018_erasure_bodies**, **0019_dialogue_bodies** and **0020_knowledge_bodies** under ADR-0055/0056/0057/0060/0061/0063/0066.
Each is compared against its own clean migrated schema reference, including exact constraints
and claimed revision; unknown schemas/revisions fail closed. Offline restore preserves
the archived database bytes/revision and runs no migration. Selecting a predecessor DB for ordinary
application use explicitly invokes normal migration, including legacy scope quarantine.
Cross-engine logical export, selective merge and live replacement remain unimplemented.
Current archives preserve episodic archival status/version and owner decision history. Restoring
an older snapshot restores its earlier reversible lifecycle; source exclusion survives through the independently trusted installation policy floor.
The main bundle contains only its derived mirror/receipts; operational binding rejects an older
mirror until deterministic reconciliation. Missing/mismatched floor never creates a new allow
policy. Physical erasure and cross-machine/key/freshness recovery remain separate gates.
See [source policy](source-policy.md).

Import reads bounded regular-file bytes once, binds activation to their whole-archive digest,
stages privately, verifies schema against a clean locally migrated reference, SQLite integrity,
foreign keys, initial identity/trait/value shape and manifest inventory, then publishes to the
new target. Integrity validation runs on an additional private offline replica opened writable
with query-only enforcement. The observed local SQLite build omitted CHECK failures during
read-only integrity checks, so tests require the replica path to detect corruption in all stored
tables. No live database is filesystem-copied; capture still uses SQLite backup. Input archive,
staged snapshot and canonical source remain unchanged. Schema comparison normalizes only
harmless ordering and surrounding separator whitespace of complete named table
constraints produced by Alembic; column order and every declaration's contents remain exact.
Arbitrary imported SQL/views/triggers/virtual tables are never admitted as schema authority;
only the exact trusted revision's known objects are accepted. Checksums detect corruption;
they do not authenticate the sender or prove semantic truth of every stored
claim. Use a trusted local archive. Canonical evidence eligibility remains with existing owners.

## Privacy and failure semantics

Created directories are 0700 and files 0600. File access rejects final symlink/non-regular targets;
publication uses a private flushed stage and a parent-directory descriptor. Explicit overwrite
atomically replaces only permitted regular export files, never restore databases.

Each archive/restore has a separate private `TARGET.receipt.json` containing operation ID,
timestamp, prepared/published/failed status, archive checksum, identity/schema and table counts.
It contains no conversation, evidence quote, prompt, SQL or credential. Recovery reads do not add
fresh self/relationship evidence or alter canonical audit; existing audit is restored verbatim.
Interrupted prepared receipts are unresolved operations, not proof of publication. Filesystem
failure after publication may leave an artifact present with incomplete durability/receipt; the
command reports failure and does not claim a successful completed operation. Inspect the target
and receipt before retrying. A restore target that exists is always refused. Even when database
publication failed before creation, its retained receipt prevents a blind same-path retry: choose
a fresh target. Receipt reconciliation/cleanup is not implemented in this initial slice.

Archives contain sensitive existing raw conversation/evidence. Configuration, environment,
credential stores, runtime logs and provider prompts are outside this snapshot. 0600 access is
not encryption or user authentication. Encryption/key recovery and sensitive production backup
readiness remain 14.4.3; physical retention/erasure remains a later 14.4.2 slice. Source eligibility
now governs operational use through ADR-0057 without changing offline bytes.
Real user data is not deleted, imported or moved by this task's synthetic verification.

The implemented [ADR-0057](decisions/0057-canonical-source-exclusion-and-trusted-policy-floor.md)
requires an independently trusted installation policy floor before operational source admission.
Its ledger/binding/mirror/barrier and owner reconciliation live outside byte transport. A
pre-exclusion main backup remains byte-preserving offline; operational use reconciles with the
surviving trusted floor before providers, context or jobs. Missing enrolled floor cannot
be replaced by an empty policy, and restoring the whole installation/floor needs a separate
trusted freshness/key recovery contract. Encryption/signatures alone cannot prove latest policy.
Full Foundation and macOS process/scenario gates passed; actual Linux process execution remains
open. See [source policy](source-policy.md) and [progress](progress.md) for exact limits/results.

ADR-0059 adds a separate [read-only erasure preview/check](erasure.md). It does not sanitize
archives, modify restored records, migrate a selected database or auto-reconcile its mirror.
An old restored main DB must first pass ordinary explicit policy reconciliation before protected
planning/use. Future physical content mutation additionally needs a durable erasure intent in
the independent trusted floor; that intent is not created by preview or offline transport.

ADR-0060 opens [durable erasure intent and metadata-only owner preparation](erasure-intent.md).
Policy_0002 is an explicit reviewed independent-floor upgrade; policy_0001 stays compatible for
existing exclusions. Source generation and erasure revision are separate. Main 0017 contains
derived whole-head acknowledgment and append-only owner preparation, not a second policy floor.
Ordinary protected runtime denies a stale erasure acknowledgment after old-main restore until
explicit resume privately reconciles all floor intents. Existing source repair does not acknowledge
the erasure head. Blocked physical readiness remains visible while unrelated runtime can proceed
after current acknowledgment and source admission.

Offline administrative export/restore still preserves the supported archive bytes, including
pending-intent text and historical bodies. It does not sanitize content, claim physical deletion,
omit retained records or auto-prepare owners. A surviving floor intent cannot be downgraded by
these main bytes. Implementation/verification status is recorded separately in progress.md.

## Acceptance evidence

Relevant tests cover whole relational equality and pure stored reads, WAL committed/uncommitted
capture, restart identity/history/evidence, schema/FK/corruption/version/digest rejection,
private modes, symlink/race/overwrite refusal, bounded input, publication failures and receipt
truthfulness. An offline synthetic Session A/export/preview/new-target restore/Session B drill
must preserve source and canonical records. Foundation verification starts by rebuilding the
noneditable wheel. Exact final results are recorded in `progress.md`; a passing recovery check
does not establish Character behavioral acceptance or open the next slice.


## Verified body materialization — ADR-0061

An after-intent/before-body archive has the same intent head but lacks the later independent
body action head. Ordinary runtime now denies that restored store until explicit approved body
replay/reconciliation. Source repair and intent preparation do not acknowledge the body head.
The exact 0018 snapshot preserves terminal fields, action projections and eight SQL guards.
Receipt presence alone never proves current deletion: effective targets must retain exact
headers/numerical fields, NULL bodies and no vectors, matching the trusted action-head digest.
Changed/expanded restored representations need fresh approval rather than guessed selectors.

Offline export/restore remains byte-preserving. Old archives retain the bodies they captured;
new exports of terminal rows contain NULL episode bodies but can retain canonical/other-owner
content and forensic page bytes. No archive, free-page, OS/provider/external-copy sanitization
or full-root erasure is implied. The surviving trusted floor is still required.

## Terminal dialogue recovery — 14.4.2f / ADR-0063

[ADR-0063](decisions/0063-terminal-dialogue-bodies-and-historical-provenance.md) implementation
and the macOS 2f engineering gate are complete (4207 passed / 5 skipped); see [progress.md](progress.md). Exact offline
admission now also supports `0019_dialogue_bodies`: 59 tables, 13 triggers and schema digest
`69c87475bb8ec301ed0d723aa0902f359c14f6d0def5b1c13c340a659f2c3611`.
Exact older 0016/0017/0018 snapshots remain supported without rewriting their schema or bytes;
the earlier 0013/0014/0015 recovery contracts remain supported as listed above.

An explicit independent-floor `policy_0004` upgrade extends the existing single mixed body head.
Old episode action JSON, approval hashes and episode-only head digests retain exact compatibility.
Source generation, erasure revision and body revision remain separate. Restore transports main
bytes only and cannot replace or downgrade the surviving floor, auto-execute an intent/action,
prepare owners or acknowledge any new head.

After approved dialogue materialization, selected canonical USER, paired assistant and proved
COMPLETE-dependent assistant bodies are actual SQL NULL. Independent USER text remains retained.
Metadata-only headers and exact approved retained-row witnesses preserve historical attribution,
not available quotations, live evidence or provider input. A changed/new evidence row cannot
inherit an old witness, and old-client replay cannot regenerate a terminal reply.

An older main archive after intent but before dialogue removal may have current source/intent
ACKs while lacking the current mixed body projection. Wrong/forged body ACK or receipt-only
claims fail actual terminal/witness/head proof before operational history, providers or jobs.
Explicit approved body resume revalidates original or effective terminal representations and
applies the current effective approved whole head atomically, including later actions or
supersession; it never expands selection beyond that head. Pre-commit failures roll back main
writes. Uncertain commit or subsequent verification failure reports pending without proving
that bodies remain available; fresh actual status/resume proof resolves completion.
Changed/expanded bodies require a fresh approval.
Source repair and intent preparation cannot acknowledge that materialization.

New archives preserve terminal NULL rows and exact action projections but can retain other
knowledge bodies, numerical/audit history and forensic page bytes. Old archives retain their
captured dialogue text. Coverage stays partial logical removal with `physical_erasure=false`;
free pages/WAL/sidecars, external copies, archive sanitization, authenticated access,
encryption/key recovery, whole-floor freshness and actual Linux process verification remain open.
Character and provider policies are unchanged; ordinary clients receive only already-admitted
available history through existing typed interfaces. See [terminal dialogue](erasure-dialogue.md).

## Terminal knowledge recovery — 14.4.2g / ADR-0066

The opened slice admits exact `0020_knowledge_bodies`: 59 tables, 58 SQL guards and schema digest
`df549886416fd74cc4eed9d5eb7f8c255ffb1ef592e3d027d5874ef9a8b7a4ba`.
Earlier declared snapshots retain byte-exact offline transport. Upgrade remains an explicit normal
migration path; preview/export/restore never runs owner erasure or upgrades the independent floor.
The explicit `policy_0005` upgrade preserves all earlier actions and extends one mixed body head.

A pre-knowledge main archive cannot use a copied completion receipt to regain runtime authority.
Resume executes existing owners atomically and proves actual NULL content, retained trajectories,
all evidence/revision membership and effective historical witnesses. Source or intent repair cannot
acknowledge this head. A stale pending target requires fresh review, never guessed selectors.
Opaque owner payloads, forensic storage and external/archive copies remain separate policy gates.
See [erasure knowledge](erasure-knowledge.md) for scoped controls and verification status.

## Terminal Reflection recovery — 14.4.2h / ADR-0068

Exact `0021_reflection_bodies` is an additional supported snapshot: 59 tables, 73 SQL guards and
schema digest `c00d86726a92cfef93c2235f8a181b38666c453ea11bb30e4be913618f2e451e`.
The explicit `policy_0006` floor upgrade preserves preceding action bytes and head hashes.
Earlier supported schemas keep exact administrative transport; a malformed current schema has
no legacy runtime fallback. Preview/export/restore neither migrates nor accepts a privacy action.

A restored pre-Reflection main archive retains original proposal payloads and cannot become
operational merely through source/intent repair or copied completion receipts. Only explicit
whole-head resume, after exact original/terminal target and strict upstream causal validation,
may clear approved bodies and acknowledge their actual terminal projection. Frozen complete
parent run/source/attempt/outcome metadata and numerical histories remain exact. Restart and
no-op replay cannot reconstruct proposal content or repeat learning. Old archive bytes remain.
See [retained Reflection handling](erasure-reflection.md); forensic erasure, full-floor rollback,
Linux and access/encryption/key recovery remain separate gates.

## Paired appraisal explanation recovery — 14.4.2h / ADR-0070

Exact `0022_appraisal_bodies` is a supported snapshot schema: 59 tables and 82 SQL guards.
Administrative transport preserves snapshot bytes and does not migrate or grant operational
admission. Explicit `policy_0007` adds a fifth action to the same trusted mixed body stream,
preserving previous action bytes and head hashes.

Ordinary admission proves actual SQL NULL reasons, absent exact audit key, immutable complete
input ancestry and retained paired material before protected reads. Floor-first interruption
leaves all main rows unchanged and startup fenced. Explicit resume repeats whole-head and
ancestry proof; changed original material needs fresh review. An old archive cannot operate
against a newer floor until exact reconciliation succeeds. No numerical rollback, archive
rewriting, forensic erasure or key/access recovery is implied.
See [paired explanation handling](erasure-appraisal.md).

## Retained audit metadata recovery — 14.4.2h / ADR-0071

Exact `0023_audit_metadata` is supported by snapshot transport and operational schema admission:
60 tables and 88 SQL guards. The companion binding table leaves old audit columns/material,
old mixed-action bytes and the independent `policy_0007` head unchanged. Migration creates no
legacy binding and performs no body update.

Coverage is recomputed from the actual restored owner/event/binding material. A copied digest,
ACK or familiar event name cannot authorize metadata retention. Missing bindings remain UNKNOWN.
Old archives keep their captured bytes; ordinary use and scoped status remain fenced across a
pending body head until explicit existing resume proves the complete actual state. Full-floor
rollback, physical cleanup and key/access recovery remain outside this contract.
See [retained audit metadata](retained-audit-metadata.md).
