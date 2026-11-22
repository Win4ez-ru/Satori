# ADR-0109: Explicit whole-mailbox Gmail search from ordinary chat

- Status: Accepted for implementation and qualification; account acceptance remains separate
- Date: 2026-11-05
- Authority: the user explicitly requests that Satori find mail across the connected mailbox
  without manually selecting messages before searching
- Supersedes: only selected-only Mail search in ADR-0086, ADR-0092 and ADR-0096; explicit
  selected-snapshot lookup and message-body reading retain their existing policy
- Preserves: Gmail account/OAuth owners, session-only credentials, Core/source exclusion,
  no writes or automatic model disclosure, Character pause and closed Stage15

## Product behavior and scope

An explicit ordinary-chat request starts one foreground search against the already connected
Gmail account. No inbox refresh, checkbox or selected-body Read is needed to find messages.
Show the effective Gmail query and up to20 matching sender/subject/date metadata results in
the source surface. Generic mail search covers all mail; explicitly selected-mail commands
retain local snapshot lookup. Disconnected state shows the concrete Connect step.

Use Gmail's server-side index, including message contents, without downloading the mailbox or
fetching matching bodies. Omit `labelIds=INBOX`; set `includeSpamTrash=true`. Another-page
indication means more matches, not an exhaustive result; invite query refinement. No automatic
pagination/crawl. Empty, partial, unsupported, cancelled, timeout and malformed results differ.

Recognize common explicit command prefixes and preserve Gmail query syntax. Deterministically
translate only explicit bounded sender/subject wording to `from:(...)`/`subject:(...)`; show
the effective query. This is not an arbitrary-description semantic-understanding claim.

## Authority and lifecycle

The current user request authorizes the exact query; it is not a fabricated message selection.
A strict transient `MailSearchRequest` binds UUID, local principal, connected account, query,
wall/monotonic creation and deadlines of at most30 seconds. Never simulate human selection via
`select(confirmed=True)`. Reuse connector session/generation/attempt and strict catalog parsing.
Validate identity, request and both clocks before access, each HTTP step and result admission.

Desktop delivery additionally binds profile, selected conversation, handoff epoch and original
wall/monotonic lifetime. Revalidate callback DTOs. Render/change callbacks cannot redispatch or
renew authority. Hide, deactivation, edits, session/profile change, expiry, cancel, disconnect
and shutdown retire the matching operation/result; late callbacks cannot restore them. Cancelling
oldA must not cancel newerB/login/selected-read work. Preserve connected-account custody when
concealing source text. No silent refresh/relogin or cross-account request inheritance.

A staged own query may resume after connection only under its original valid foreground scope;
actual browser deactivation may retire it, requiring a new query after connection. Finding mail
does not require opening bodies. A later explicit result Read still uses the existing exact
selected-resource/grant/projection owner, never automatic full/thread/attachment/tracking fetch.

## Transport, privacy and cost

Existing `gmail.readonly` suffices; do not request broader OAuth scopes. Extend fixed verified-TLS
endpoint admission only with the exact search-list parameter set and strict UTF-8 query decoding.
Unknown fields/endpoints/encoding and control characters remain rejected. Bound queries to512
characters, URLs and response bytes; keep existing30-second request and per-HTTP time limits,
no redirects/retries or ambient credentials.

Queries and result headers are transient untrusted data, absent from diagnostic logs, canonical
USER/history, memory/self/relationship evidence, model context and TTS. No extra LLM call,
subscription, paid allowance, mail write, label mutation, mark-read, draft or Send. No server
credential/session upload is implied; session-only custody remains unchanged.

## Verification and delivery

Test Cyrillic/markup query transport and unchanged endpoint rejection, existing inbox/read
regressions,20-result/byte/time limits, account/principal/request mismatch, cancellation/rollback
and late callback discard. Exercise actual production composer/controller/UI with fake transport:
one automatic search, zero manual selection, plain-text visible results and no Core/model path.
Keep explicit selected-only lookup coverage. Freeze one integrated source; run the complete
fresh noneditable Foundation workflow, qualify the actual bundle, native preview and reversible
app-only installation. Preserve installed22 and all rollback copies. Live Gmail login/search is
separate acceptance; fixtures/build/metadata do not prove it. Mac-asleep iPhone remains separate.

## Primary references checked 2026-11-05

- [Gmail messages.list](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list)
- [Gmail search differences](https://developers.google.com/workspace/gmail/api/guides/filtering)
- [Gmail messages.get](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/get)
- [Gmail scopes](https://developers.google.com/workspace/gmail/api/auth/scopes)
