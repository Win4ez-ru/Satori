# ADR-0115: Practical connected assistant lifecycle

- Status: Accepted for the owner-requested repair
- Date: 2026-11-18
- Extends: ADR-0110 and ADR-0114 for typed foreground read routing
- Supersedes: ADR-0111 only for the seven-day price-review ceiling; ADR-0092/0114 only for the confirmed Telegram login duration and recoverable view expiry
- Preserves: Core/self/memory ownership, scoped source grants, effect approval, provider budget owner and closed Stage15

## Problem

The owner reports connected sources being ignored in ordinary conversation. The installed
composer still depends on literal intake patterns, the configured public price review expired
on November13, and the temporary Telegram account lifetime is only15minutes. The native worker
also treats expiry of a selected view as a fatal account failure. Isolated successful adapter
tests do not establish a usable connected assistant.

## Decision

Keep deterministic recognized requests on their existing owners. For unmatched foreground
composer input on an admitted GPT runtime, ask the same budgeted Core provider for one bounded
typed read route before canonical conversation. The proposal can select ordinary chat, a
single-day Calendar agenda, a subset of mail/Telegram/call observations, or clarification.
It cannot create an event, send a message, approve disclosure, or acquire an account grant.

The route receives only the current owner text and civil-date/timezone metadata. It receives
no source contents, persistent self projection, retrieved memories or account credentials.
Date phrases must be literal substrings of the request. The deterministic host resolves the
day and rejects conflicting or invented dates. Actual source planning still uses the existing
Core context and exact source-owner grants. Calls without an admitted route remain unavailable.

Routing has a400-output-token ceiling, one provider attempt and a30-second timeout inside the
original120-second foreground task. A routed source execution receives a fresh single-attempt
ID but inherits the original expiry and monotonic deadline. The same serial worker, provider,
usage ledger and dispatch admission apply. Profile/session/provider changes, new draft,
visibility loss, revoke and expiry retire the route. Only an accepted chat route invokes
canonical Send; route proposals and external receipts never become canonical user/evidence.

Confirmed temporary Telegram login may last up to24hours from its original start, ending
earlier on Disconnect, application exit, profile change or terminal failure. Authentication
challenge, selected-chat, page, search, reply and source-task deadlines retain their existing
bounds. PAGE_EXPIRED/GRANT_EXPIRED retires the selected source and outstanding tokens, closes
the viewed chat and returns a confirmed live account to READY without automatic reading.
This does not introduce persistent credentials or promise reconnection after quitting.

Use a finite, hash-pinned public price review with a maximum90-day validity. A bundled exact
legacy-review allowlist may adopt the current compatible review in memory only when the
same price and FX assumptions are explicitly reviewed. Preserve the configured month, cap,
credential reference and existing ledger. Unknown/modified reviews, expired current reviews,
exceeded caps and stale budget periods remain refusals. Do not rewrite private config during
startup or make paid startup requests. Explicit settings refresh preserves the existing cap
and period instead of replacing them with defaults. This is reviewed tariff metadata, not a
promise that provider prices or exchange rates remain fixed for90days.

## Calls and server boundary

The release includes a loopback-only Asterisk AudioSocket adapter with explicit UUID
admission, one concurrent call, bounded frames and timeouts, and a PCM callback. The adapter
remains disabled in the desktop runtime pending a call provider and end-to-end SIP, ASR,
dialogue and TTS qualification. A caller ID claim grants no data access or action authority.

## Qualification

Exercise both reported unmatched wordings through the actual Window and worker contract,
same-Core routing with no canonical learning, exact selected sources and original deadlines,
ordinary-chat fallback, revocation and invalid proposals. Verify Telegram recovery without
read-authority renewal, original24-hour expiry and Disconnect. Check exact legacy pricing
adoption, private-file/ledger preservation, unknown reviews and90-day refusal bounds.

Then run the complete frozen Foundation gate, actual package and native startup/readback.
Report actual account reads, acoustic calls, installation and Mac-asleep operation separately.
No consumed public test budget is reopened by this decision.
