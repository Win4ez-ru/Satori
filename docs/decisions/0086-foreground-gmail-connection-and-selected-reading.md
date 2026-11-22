# ADR-0086: Foreground Gmail connection and selected reading

- Status: Superseded in part by [ADR-0087](0087-bounded-mail-alternative-selection-and-diagnostics.md) only for MIME-alternative decoding and projection version; remaining decision unchanged
- Date: 2026-10-20
- Authority: the user's explicit request to connect their Calendar and Gmail and continue
  Telegram/calls in the existing parallel chats
- Extends: ADR-0085's offline foundation with an explicit foreground Gmail connection/read UI
- Preserves: Calendar grant ownership, existing Core, prompts, canonical state owners,
  editable ASR draft/explicit Send, Character pause and the closed Stage 15 gate

## Decision

Deliver a real read-only Gmail connector in the desktop application. Opening the app or its
Mail tab performs no OAuth, account discovery, message listing or source read. The user selects
a bounded Google **Desktop installed-client JSON** file and explicitly connects. Registration,
Gmail API enablement and the consent test-user setup are prerequisites, not credentials that
can be invented, borrowed from another application or inferred from a connector in Codex.

Use the system browser, authorization code with PKCE S256, random one-use state and an owned
loopback listener bound to `127.0.0.1` on an ephemeral port before opening the URL. Admit only
the exact random callback path, Host, bounded GET, state and current attempt. Bound login to
120 seconds. Cancel/profile change/quit closes that listener and fences late results. No pasted
authorization-code, embedded-browser or password flow is provided.

Request only `openid`, `email` and `https://www.googleapis.com/auth/gmail.readonly`. Google's
documented OIDC scope set includes email or profile; email avoids the wider profile scope.
Check granted scopes, including Google's normalized email equivalent, and reject missing or
broader grants. Obtain the subject from the fixed Google OIDC UserInfo HTTPS endpoint using
the freshly redeemed access token. Never trust an unsigned JWT, message sender, display name
or email address as account identity. Bind the source to issuer, subject and client registration.

Credentials are session-only process memory with secret-redacted representations and errors.
There is no token file, environment cache, Keychain, refresh, remember-login or restart restore
in this increment. The UI explains that reconnect is required after expiry/restart. Persistent
Keychain custody requires a separate verified native-app decision; current ad-hoc signing does
not establish it. Local disconnect drops credential references; physical memory/swap/crash
erasure and removal of the remote Google grant are not claimed.

## Foreground source flow

After connection, a separate explicit action lists one INBOX page of at most 20 message IDs
and allowlisted metadata headers. No snippets, bodies, automatic pagination, thread expansion,
attachment fetch, tracking URL or background sync is permitted by listing. Show the account
label and any further-page indication without claiming complete inbox coverage.

The user selects at most ten exact messages and explicitly reads them. Reuse the existing
`MailAccessCoordinator` and bounded MIME projection: exact principal/account/catalog/resource
selection, one-use selection proof, at-most-one-hour grant, 30-second request and five-minute
snapshot. Recheck connection generation, token expiry, scope and request deadlines before and
after transport. Selection edits invalidate the previous grant and preview; re-admitting a
still-fresh catalog must retain its original observation time. Late/replayed/cross-profile or
cross-account results cannot repopulate the UI.

Use plain-text local previews with sender/header claims, omissions, attribution and freshness.
Gmail readonly is technically mailbox-wide; the selected-message limit is Satori's policy.
No source payload enters canonical Send/history, Core/model context, self/relationship evidence,
memory or TTS. No generated summary, draft, marking read, label mutation, remote write or Send
is implemented by this decision. A later source-processing contract remains separate.

HTTPS transport uses fixed allowlisted Google endpoints, verified TLS, bounded response bytes,
JSON structure and deadlines, no redirects, ambient cookies/auth/proxy credentials or automatic
retry. Logs/errors contain closed metadata only, never callback URLs, codes, tokens, client
secrets, addresses, headers or message bodies. Configuration and source content are untrusted.

## Cancellation and revocation

Ordinary Disconnect invalidates locally first, cancels owned operations and clears source-derived
views. Explicit remote revocation is a separate confirmed action: Google revocation can affect
the user's other scopes/clients in the same project, and its application can be delayed.
Serialize explicit revoke against reconnect/read. Never issue late automatic project revocation
for a cancelled login after a newer connection has begun. Report local disconnection separately
from remote `revoked`, `failed`, `unknown`, `not_requested` and `not_connected` outcomes.

All network waits run on owned workers. GUI cancellation is nonblocking; keep workers alive
until they finish and wait asynchronously for bounded shutdown before application exit.

## Verification and acceptance

Automated checks cover client-file bounds, endpoint/scope rejection, synthetic real loopback
PKCE/state/Host/path/deadline/cancel cases, HTTP/TLS/redirect/byte bounds, credential redaction,
account isolation, existing policy/projection admission, UI selection/expiry/profile/quit races
and zero startup/source-to-Core I/O. Run the fresh noneditable Foundation toolchain, build and
frozen artifact checks before app-only installation; preserve the previous app for rollback.

Real account acceptance requires user-driven Google login/consent, a bounded inbox list, exact
selected-message preview, refresh and local disconnect. Never copy personal contents into
fixtures, screenshots for publication or performance records. Calendar acceptance uses the
already accepted ADR-0082/0083 path with user-confirmed OS access and calendar selection.
Telegram and calls continue independently; this ADR does not claim either is integrated.

## Primary references checked 2026-10-20

- [Google Desktop OAuth](https://developers.google.com/identity/protocols/oauth2/native-app)
- [Google OIDC](https://developers.google.com/identity/openid-connect/openid-connect)
- [Gmail scopes](https://developers.google.com/workspace/gmail/api/auth/scopes)
- [Gmail profile](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users/getProfile)
- [Apple Keychains](https://developer.apple.com/documentation/technotes/tn3137-on-mac-keychains)
