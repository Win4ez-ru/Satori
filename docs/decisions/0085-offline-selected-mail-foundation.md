# ADR-0085: Offline selected-mail policy and projection foundation

- Status: Superseded in part by [ADR-0087](0087-bounded-mail-alternative-selection-and-diagnostics.md) only for MIME-alternative decoding and projection version; remaining decision unchanged
- Date: 2026-10-20
- Authority: the user's request to integrate ready parallel changes; Gmail selected in the mail chat
- Related: ADR-0046, ADR-0079, ADR-0082; Calendar grants are not widened

## Decision and scope

Integrate strict typed, deterministic source/selection/read/freshness contracts and a bounded
Gmail message-to-plain-text projection, adapted from the isolated mail prototype. This is an
offline foundation without an installed UI, OAuth, credentials, network transport, inference,
remote drafts, sending, persistence or actual mailbox access. Do not expose a simulated connected
mailbox or call this a working Gmail integration.

Reuse the existing local principal type; mail has its own account/resource scope and ephemeral
owner. Exact account and message IDs, selection revision, request/grant/generation, expiry and
single admission bind results. Thread/header/display-name identities do not expand resources.
Revoke, account/profile changes, expired snapshots, clock rollback and late/replayed results
fail closed. Audit contains closed metadata only, not account addresses, headers or bodies.

Project bounded MIME and headers as external untrusted plain text with exact source/span
attribution and explicit completeness/unsupported reasons. Preserve sender/header claims as
claims, not authenticated identities or trusted instructions. Handle valid repeatable headers
separately from forbidden duplicate singleton headers. No attachments, embedded messages, HTML
execution, remote images/URLs, tracking pixels or implicit fetch belongs to this parser.
Unsupported charsets/content and truncation never become complete successful evidence.

No source content becomes canonical USER, Core context, self/relationship evidence, durable
memory or TTS. The future summarizer/draft interfaces remain proposals; deterministic fixtures
do not establish model semantics. Avoid a general access framework or duplicating Calendar's
grant owner. Network OAuth and session credential custody need their own accepted implementation
and user-driven account consent: Gmail readonly technically permits mailbox-wide reading,
while future selected-message narrowing is application policy.

## Verification

Port meaningful adversarial fixture tests to repository pytest, strict mypy/Ruff and the
coordinated fresh noneditable Foundation run. Independently review expiry/revoke/admission,
bounded bytes/tree/decode, duplicate headers, quoted/forwarded claims, source spans and clear
unsupported outcomes. No tests may access actual Gmail or copy private mail into fixtures.
Working read/summary/draft delivery and real account acceptance remain future separate units.
