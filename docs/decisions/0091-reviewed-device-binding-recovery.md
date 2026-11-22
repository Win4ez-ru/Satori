# ADR-0091: Reviewed adoption after device-only installation-binding drift

- Status: **Accepted for explicit owner-reviewed recovery**
- Date: 2026-10-26
- Authority: bounded engineering follow-up and the user's explicit approval to adopt the reviewed current files on 2026-10-26
- Related: ADR-0057; existing source-policy, private-artifact and whole-state-recovery contracts
- Runtime default: unchanged; ordinary admission never invokes this recovery path
- Actual installed-profile adoption: **ADOPTED / normal binding valid**, after owner approval and the complete bundle 10 gate; installed Core startup subsequently reports READY with zero processing errors

## Problem and trust boundary

The installation descriptor persists `(st_dev, st_ino)` for the independent policy database and
its fixed lock. Read-only diagnosis found device-component disagreement with unchanged inode
numbers. Ordinary `validate_binding()` correctly rejects the full-pair mismatch. The existing
worker reports only `source_policy_rejected`; later commands can consequently report
`runtime_not_open`. This observation does not establish the cause of the device change or the
identity of the previous volume.

Matching inode numbers, current hashes, identity and lineage metadata do **not** prove historical
volume continuity or latest-floor freshness. This decision therefore requires a new, explicit
**owner adoption of the reviewed current files**, with `original_volume_proven=false`. It is
neither automatic repair nor recovery of proven original-volume identity. Approval flags record
an operator decision; they are not authentication, a relationship-derived permission, or a new
access-grant subsystem.

ADR-0057 already distinguishes explicit installation recovery from fresh enrollment and ordinary
owner-projection repair. This decision adds only a bounded adoption command through the existing
ledger owner. It does not weaken normal full-pair validation, introduce a fallback, or authorize
the real operation merely because engineering tests pass. Physical erasure, cross-machine
recovery, nonzero policy generations and authenticated/latest-floor recovery remain separate.

## Accepted boundary

The typed application contracts are in `application/source_policy/binding_recovery.py`.
`LedgerDeviceBindingRecovery` inspects explicit absolute paths. Its adoption command delegates
to `InstallationPolicyLedger.adopt_reviewed_device_binding`; the ledger's single descriptor
publisher is shared with initialization. No other component directly patches the descriptor.

The first supported slice accepts only:

- current-UID directories at 0700 and regular, current-UID, single-link files at 0600, without
  symlink traversal;
- the exact existing descriptor version and keys, one current device for descriptor/main/floor/
  lock, one distinct stored device for both stored targets, and unchanged exact floor/lock inode
  numbers; the lock is empty;
- exact pinned main `0024_checkpoint_reasons` and policy `policy_0002` schema shapes, SQLite
  integrity and foreign-key checks, and a single agreeing canonical identity, installation,
  floor, lineage and generation;
- source and erasure generations zero, no exclusions, policy receipts, erasure intents, main
  exclusion/withdrawal/body/preparation state, and all five exact owner-repair watermarks at zero;
- no source-input/payload foreign identity or nonzero generation, and DELETE journal mode with
  no database sidecars, including dangling links or unexpected SQLite suffixes.

Unknown schema, inode replacement, only one changed device component, inconsistent stored/current
devices, missing metadata, WAL mode, nonzero history or any failed bound is refused. Current
hashes do not upgrade UNKNOWN memory evidence, change canonical source eligibility, or provide
historical freshness authority.

## Review and one-use adoption

Preview opens read-only SQLite transactions, performs only schema/metadata/integrity queries and
opaque byte hashes, and creates no files or migrations. It does not query conversation, memory,
mail or calendar bodies, secrets, credentials or Keychain. The deterministic review binds exact
descriptor, main, floor and lock hashes/stat metadata, current parent inodes, paths, schema pins,
identity/installation/floor/lineage, and the old/new device components. No provider is composed.

Adoption requires the complete plan, its exact independently reviewed digest, a bounded request
ID, explicit `owner_adopts_current_files=true`, and `runtime_confirmed_offline=true`. The operator
must first quiesce all users of these files. The policy lease alone does not protect against CLI
bootstrap/migration paths that run before policy admission; this command does not claim safety
against hostile same-UID writers.

The owner then:

1. Acquires an exclusive lease on the **existing current** lock pair, never a newly created lock.
2. Repeats the complete fresh inspection under that lease. Read-only SQLite transactions hold
   shared database locks throughout publication; byte/stat/no-sidecar guards repeat immediately
   before and after the descriptor change.
3. Publishes a private durable one-use claim containing the exact reviewed metadata, then an
   exact backup of the old descriptor. Both must precede descriptor publication. Existing claim,
   backup or receipt paths consume/refuse that review rather than authorizing a blind retry.
4. Uses the existing ledger publisher to update only the descriptor's two device components,
   with an expected old-descriptor hash. It does not write the canonical database, floor database
   or lock contents; identity, lineage, versions, generations, receipts and owners stay intact.
5. Checks unchanged main/floor/lock material, the exact expected new descriptor hash, and normal
   full-pair `validate_binding()`. Only after SQLite contexts and the lease's final lock check
   finish successfully does it write a separate content-free operation receipt and return success.

Backup, claim and receipt are private artifacts under `source-policy/binding-recovery/`; they are
not canonical self, new evidence, a capability grant, or an erasure/freshness receipt. Only the
small descriptor is backed up by this operation. No protected body archive is created.

The descriptor mutation consumes its input review because the old device mismatch and descriptor
hash cease to exist. The durable claim also consumes unsuccessful attempts after claim creation.
Same request IDs or new request IDs cannot silently replay a consumed plan. These local artifacts
are not a monotonic anchor against a hostile full-installation rollback.

## Failures and rollback limits

Before claim creation, refusal has no intended persistent effect. Claim publication failure is
reported as possibly consumed and requires inspection. After claim creation, failure before
descriptor publication returns `not_applied_review_consumed` only when the old descriptor and
all other reviewed material still match.

Only a successfully returned, fsynced publisher establishes durable descriptor publication.
After that boundary, an exact new descriptor with preserved database/lock material but failed
receipt publication returns `adopted_audit_pending`; it is never reported as "nothing changed".
A publisher exception, including post-rename parent-fsync failure, remains `outcome_unknown`
even when current readback matches: readback is not a durability proof. Unproven postconditions
also return `outcome_unknown`. Evidence is preserved. No automatic rollback or automatic retry
is implemented.
Context-finalization or final lock-guard failure after a durable claim also returns
`outcome_unknown`; a successful receipt is never written before those finalizers complete.

A future explicit rollback would require the exact newly published descriptor hash and unchanged
main/floor/lock identity and bytes under the same current lock, and would restore the old
descriptor only through the same ledger publisher. That restores a known denied binding; it
does not prove original-volume continuity. No rollback command is authorized or delivered here.
If those preconditions cannot be established, a separate operator recovery decision is required.

## Reviewable interface and acceptance

`python -m satori.binding_recovery_cli --policy-directory ABSOLUTE --canonical-database ABSOLUTE
--preview` prints the content-free plan and does not open the ordinary runtime. A separate
`--adopt` invocation requires `--plan`, `--expected-review-sha256`, `--request-id`,
`--owner-adopts-current-files` and `--runtime-confirmed-offline`. The plan must be reviewed before
that final operation. Engineering tests alone never authorize actual adoption. On 2026-10-26,
the owner approved the exact prepared plan. The command returned `adopted`, with unchanged
canonical/floor/lock bytes and valid ordinary binding. The private operation receipt SHA-256 is
`270b8653473f3fcf513fba52e30b42148ca9897196b8c08958c03f0282c34e89`.

Synthetic tests must cover readonly preview, exact two-scalar publication and unchanged owner
bytes, required owner decision, stale hashes/stat material, private files/parents, links, schema,
identity/generation, WAL/sidecars, lease rechecks, one-use behavior and partial publication/audit
failures. Existing normal replacement-inode rejection tests remain unchanged. Focused checks
provide engineering evidence only. The complete frozen gate and the separately requested
actual-owner decision remain independent acceptance boundaries; the dated result is recorded
in the bundle 10 delivery report.
