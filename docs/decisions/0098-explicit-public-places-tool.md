# ADR-0098: Explicit public places tool

- Status: Accepted for implementation and qualification; main-app map and route acceptance separate
- Date: 2026-10-31
- Extends: ADR-0096 public places scope; preserves all private-source and canonical-state boundaries

Use the reviewed native MapKit helper and its typed Qt adapter as a same-application tool.
City, district and query start blank. An explicit search discloses those fields to Apple;
the user visibly selects the search origin, even for a singleton result. Device location,
IP-derived location and calendar inference are not used. Mount the Apple map before showing
bounded MapKit cards. Cuisine, hours and budget remain unknown when the provider supplies no
supported evidence. Never claim a venue is open now from these cards.

Opening a route is a separate visible action bound to an unexpired result and the exact
confirmed origin/destination. A Maps handoff receipt proves only acceptance of the handoff.
The auxiliary map is an owned tool window; moving focus to it must allow origin selection.
Actual panel hide, profile change, expiry, helper failure and Quit clear/fence the operation.
This focus exception does not weaken Gmail/Telegram foreground privacy.

Compile only the explicit native source allowlist into the new app. Development does not
silently compile or use an arbitrary checkout helper. Missing packaged helper is unavailable.
Include asynchronous helper completion in the main Quit barrier. All tool data stays transient,
outside canonical conversation, memory, relationship, provider generation and speech.

The reviewed packet has scoped Python/native synthetic checks and a fresh four-record public
provider smoke. They do not prove main-app map layout, selected route UI or personal suitability.
Root owns integrated checks, packaging, focus/lifecycle verification and reversible installation.
Foreground screen/app context and GPS remain separate default-off work; the optional unqualified
unlock/context proposal is not enabled by this decision.
