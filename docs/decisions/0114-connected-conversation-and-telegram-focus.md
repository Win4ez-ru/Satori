# ADR-0114 — Connected conversation and Telegram focus

- Status: Accepted for the reported desktop repair
- Date: 2026-11-12
- Supersedes: ADR-0092 only where ordinary focus loss terminates temporary Telegram authorization
- Extends: ADR-0109, ADR-0110 and ADR-0113 for direct reads in the ordinary composer

## Evidence

The owner confirmed the Calendar and Gmail settings connected in App25. The ordinary chat
answered a Sunday agenda question from conversation memory. A window switch during the
Telegram connection cleared input and could interrupt the authorization worker.

## Decision

Route explicit personal agenda questions with a single local day through the existing selected
Calendar owner. A presence statement needs a request or an interrogative marker to authorize
the read. Use the current grant, civil-day bounds and typed receipt. Show events or the
empty, partial or unavailable result directly in the conversation. Retire this read when the
conversation loses visibility. Calendar writes keep the existing authority path.

Route simple inbox and sender lookups to the Gmail owner under an explicit foreground request
with bounded metadata results. Render the result in the conversation. Preserve the exact
provider-query route and the existing contextual search with scoped model disclosure. An
explicit Telegram location governs a request that uses the generic word for a letter.

An ordinary window or tab switch conceals Telegram content and retires read, search and reply
authority. A temporary login challenge or confirmed account can survive the switch within its
original deadline. Clear sensitive input on focus loss and display the account confirmation
only while the panel is visible. Retain a valid numeric API ID in the input during ordinary
focus switches. Keep the worker session local and temporary. Re-entering the
foreground requires a new explicit read or search. Disconnect, profile change, terminal
failure, expiry and application quit still end the temporary session.

## Qualification

Exercise the exact Sunday question through the ordinary composer with a selected Calendar and
complete, empty and partial results. Exercise ordinary Gmail metadata requests, disconnected
status and cancellation with the same composer. Verify Telegram focus loss before and after
confirmation, cancellation of a read in flight and the original session deadlines. Report
live account acceptance separately from isolated and packaged verification.
