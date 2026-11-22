# ADR-0099: Isolated preparation for a single headless Satori service

- Status: Accepted for implementation and synthetic qualification of the bounded preparation below
- Date: 2026-10-31
- Authority: the user's explicit server-hosting request, continued implementation, cost visibility,
  security and optimization requirements
- Extends: ADR-0096; preserves ADR-0057 and ADR-0091 installation/freshness boundaries
- Does not supersede the deployed LAN-only gateway or authorize production migration

## Problem and intended topology

The current iPhone client requires the Mac Core to remain awake and reachable. The user rejects
that as the final topology and requests one server-hosted Satori, within a total monthly budget
of 1,500–3,000 RUB and with Russian-card payment. Moving only foreground generation to an API
does not relocate appraisal, extraction, embeddings or the other existing cognitive roles.
A small published VPS quote is not execution, performance, billing eligibility or quality evidence.

The intended destination is one server-owned Core with Mac and iPhone as authenticated clients.
There must remain exactly one canonical state owner. Neither device keeps a divergent writable
copy, and no merge of identities or memories is introduced. This decision prepares the service
lifecycle, exhaustive role-readiness checks and independent usage accounting. It does not yet
admit the complete hosted topology.

## Isolation and preserved release

Implement first in `var/artifacts/server-runtime-20261031/work`, an explicit source-only
candidate copied from the current repository. Record exact baseline hashes. Do not copy live
profiles, `.env`, credentials, models, application bundles, caches or personal account data.
The main runtime source, installed Mac16, qualified candidate17 and installed iPhone build3
remain unchanged. Review, a complete fresh Foundation gate and separate target qualification
precede transfer into the common runtime and any application release.

Character remains paused and domain Stage15 closed. No state schema, personality behavior,
source eligibility, canonical message admission or background evidence rule changes here.

## Headless lifecycle

Reuse the existing backend/application owners without constructing Qt objects. A headless host
owns one backend and one serialized execution lane. Existing explicit-session operations are
used atomically; a resume-then-send pair cannot implement client session routing.

Accept only explicit private storage paths and a current, already activated, valid installation.
Startup does not activate a new identity, import a snapshot, adopt changed binding, enroll a
missing policy floor or silently migrate an older database. Existing source-policy validation
remains authoritative. A cooperating service lock prevents a second headless owner; this is
not proof that a legacy desktop, CLI or hostile same-UID process is offline. Real transfer still
requires quiescing those writers under a separately reviewed migration procedure.

Shutdown stops new admission and drains admitted owner work. Caller cancellation must not drop
a dispatched effect or release the owner lease while its outcome is uncertain. A failed drain
is unknown; do not create a replacement writer or replay a timed-out request automatically.
Use closed diagnostics without request/response bodies, exception text, credentials or prompts.

The first slice has no network listener and is not an alternative unauthenticated remote API.
A local process/preflight receipt is not iPhone connectivity or hosted availability evidence.

## Provider-role readiness

Represent all existing roles explicitly: conversation, episode, embedding, semantic, appraisal,
relationship, models, positions and reflection. Validate capability compatibility and reject
missing, duplicate or unqualified assignments. No silent fallback or omission of background
roles may make a small VPS appear sufficient. Public model catalogues, API compatibility and
synthetic parsers are separate from semantic/character/structured-output acceptance.

Remote disclosure needs an explicit approved scope and a provider/account whose eligibility
is established using factual billing details. Unknown residency must not be invented. This
slice makes no paid request and does not connect new role adapters. Existing local production
routing stays unchanged. Any eventual wire/model change must pass the repository's applicable
behavior, grounding, continuity, appraisal and replay gates before deployment admission.

## Usage and budget accounting

Use one separate metadata-only usage owner, not canonical memory or personality state. Count
every billable attempt, including retries and background roles. Reserve a conservative maximum
before dispatch; transactional admission must not exceed the configured accounting-period cap.
Represent money exactly, bind repeated attempt IDs to identical arguments and reject conflicts.

A timeout or missing provider usage retains an unknown liability. Neither an empty response nor
an expired HTTP request proves a zero charge. Reconciliation is read-only with respect to the
provider and never replays inference. A local reservation or provider-reported token estimate
is not a bank invoice. Label measured usage, estimates, reservations, unknowns and invoices
separately. Provider adapters and user-visible settings are a later integration of this owner;
a standalone ledger must not be reported as a working live spending monitor.

The 2,200 RUB planning allocation excludes unknown telephone-provider/number/forwarding fees.
Call inference and speech count once in the shared model/voice categories. The remaining
800 RUB is conditional room, not a quote or proof of the full budget. No number, forwarding,
call, support message, account signup or payment is performed by this work.

## Production admission still required

Before any live deployment, complete and review:

1. Non-Qt request broker integration, bounded authenticated remote access, TLS/device enrollment,
   revoke, quotas and unknown-outcome reconciliation; never flip the LAN listener to public.
2. Real adapters and quality/usage qualification for every selected cognitive role, including
   embedding compatibility and an explicit context-disclosure decision.
3. Reviewed whole-installation transfer: stop old writer, consistent immutable backup, independent
   policy-floor/lineage validation, target binding through its owner, reminder handling and
   device re-pairing. ADR-0091's same-installation adoption is not cross-machine authority.
4. Encrypted backup and actual restore proof, Linux process/restart/lock/resource tests and a
   Mac-asleep physical iPhone continuity test. A Mac-only synthetic process is not Linux proof.
5. Separate native source/capture/notification contracts. The server cannot access a sleeping
   Mac's screen or EventKit by relocation, and session-only mail/TG grants do not become
   persistent server access. Existing November shifts must not be imported again.

No purchase, personal-data transfer, credential upload, public listener, firewall change or
physical-device acceptance is implied by this ADR or an offline passing test.

## Verification

Run meaningful lifecycle, exclusive-owner, failed-start, cancellation/drain, restart, explicit
session isolation and content-free diagnostics tests. Cover missing/mismatched provider roles,
unapproved disclosure, unknown eligibility, usage conflicts, restart, concurrent reservations,
cap boundaries and unknown liabilities. Then run the complete required Foundation toolchain
against the exact candidate in a fresh noneditable environment and record exact source parity.
Keep scoped automated evidence, local process evidence, Linux qualification, provider/account
acceptance and actual Mac/iPhone delivery distinct.
