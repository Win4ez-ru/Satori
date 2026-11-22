# ADR-0105: One reviewed Telegram text reply candidate

- Status: Accepted for this explicitly requested isolated implementation and offline qualification; no real account/send effect is admitted
- Date: 2026-11-03
- Authority: user requests assistant backend actions and continued parallel Telegram work; coordinator assigns a bounded reply pipeline
- Isolation: `var/artifacts/telegram-reply-20261103/work`, public source from exact R6
- Extends: ADR-0092/0096 only for the separate reviewed-reply candidate below
- Preserves: existing read-only helper/pins, installed applications, canonical Core/self/memory ownership, Character pause, Stage15 closure and all existing account scopes

## Decision

Prepare one plain-text reply to one explicitly selected message in one already confirmed
personal chat. No automatic reply, whole-account lookup, new recipient, group/channel,
attachment, formatting, scheduling, paid Stars, paid broadcast, contact lookup, message
deletion or second model is introduced. A caller supplies a user-authored or separately
admitted draft; this slice does not disclose private source text to an LLM.

The receive owner remains sole owner of account/client/session, selected chat, page, native
IDs and source grants. It supplies a current `SelectedReplyScope` and a trusted native
handle resolver. These are not frontend assertions. The resolver must check the current
confirmed account, actual personal-chat eligibility, exact selected page/message and all
original deadlines. Hidden/revoked/expired state returns no binding. No read permission
becomes write authority: drafting is inert, while a separate deliberate exact preview
approval mints one new request/recipient/reply-target/text-bound, maximum60-second send grant.
`approve(confirmed=True)` is a trusted host UI/policy boundary, not a network boolean.

Editing text/target creates a different payload and invalidates approval. Profile, app session,
account/auth epoch, source generation, selected chat/page, expiry, rollback and revoke are
checked before dispatch and again inside the owned native RPC lane. The host must serialize
the owner, perform that callback immediately before native.send and revalidate publication.
One owner keeps bounded transient drafts/approvals; it never creates another Core or receiver.

## One attempt and honest outcomes

A separate owner-private SQLite metadata journal atomically records UNKNOWN before a
transport may dispatch. It binds the request ID to the exact scope/reply-target/text digest.
Repeating or conflicting with that request cannot create a second attempt, including after
restart. Capacity is bounded512 by default and fails closed; no automatic retention/delete
or recovery of a disabled store is provided. This is action metadata, not canonical memory.
The journal contains no reply text, account secret, native chat ID or source message body.
Its admission metadata records the operation, exact approval ID/time, principal, scope digest
and claim time; outcome updates retain that immutable admission and their last update time.

The fixed TDLib adapter uses the locally pinned public schema commit
`bc9c263e2bfee06aaab41e82db51a103376030bc`: sendMessage with topic_id=null, one explicit
inputMessageReplyToMessage, plain formattedText/entities=[], previews disabled, no markup,
no scheduled/background send, no Stars or paid broadcast and no draft clearing. Its
application sending_id is nonpersistent correlation, not a Telegram idempotency guarantee.

A pending message is UNKNOWN, not SENT. SENT means correlated server-accepted outgoing
message, not recipient delivery/read. Exact native chat/reply target/text and unpaid state
must agree. A trusted terminal update may correlate temporary/final IDs but does not itself
publish success; explicit read-only getMessage verifies the current message. Missing data,
404, malformed result, timeout, lost acknowledgement or revoked publication remains UNKNOWN.
No resubmit/resendMessages operation exists. An attempt with no message ID, or with a lost
temporary/final mapping after restart, may remain unresolved; text matching/history scanning
must not manufacture a receipt. Reconciliation requires current exact source authority.

## Native integration remains a separate gate

The existing helper has an explicit read-only native/wire allowlist. It is NOT changed or
bypassed. The new fixed-function adapter requires a separately enabled `OwnedReplyRPC`
capability with one owned receiver and guarded dispatch; current helper cannot supply it.
No network, TDLib instance, credentials or account is constructed by the new modules.

Before enabling a real UI action, root must approve and qualify a separately versioned
native command/receive-owner extension, exact selected-message handle mapping, visibility/
auth generation fences, correlated terminal update forwarding, packaged resource pins and
shutdown. Root must wire a concrete preview, new write approval, action result and explicit
reconcile surface. Existing read credentials and user account acceptance cannot stand in
for approval to send. Real sending requires the user's exact action/recipient/text authority.

## Verification

Offline synthetic tests cover one attempt, crash/restart metadata, independent duplicate
claims, exact recipient/text approval, stale/foreign receipt, queued revoke, generation/
profile/account/page changes, wall/monotonic expiry, malformed native output, paid-message
rejection, pending-to-final correlation and getMessage-only reconciliation. Installed wheel
checks, Ruff and mypy qualify the new modules. The root integrated R7 Foundation gate,
native transport, GUI, packaged bundle and real account/send acceptance are separate NOT_RUN
outcomes. No paid API allowance is consumed or renewed.


## Isolated chat text handoff extension — 2026-11-05

Accepted only for the explicitly authorized isolated implementation and offline qualification;
this extension admits no live account operation, automatic approval/send or LLM disclosure.
The later R16/App21 candidate already integrates the versioned native reply worker, receive
owner, exact preview, 60-second approval, send-once journal and explicit reconciliation UI.
The original native-integration gate above is historical, not a claim that these are absent.

An explicit ordinary-chat command, `Ответь в Telegram на выбранное сообщение:TEXT`, passes
only the literal user-authored text after the colon to the existing Telegram panel. Whitespace,
case and line breaks are preserved. This is a typed transient task, never canonical Send or
private-source/model input. The parser and assistant dispatcher cannot select a recipient,
preview, approve or send. A device capability cannot invoke the desktop UI handoff.

The handoff binds its request ID, current profile/app principal, conversation, local epoch and
original wall/monotonic deadlines (at most two minutes). At most 512 IDs per panel instance are
admitted, then staging fails closed. Duplicate or retired IDs cannot restage text. No message
handle or read/send grant travels from hidden Telegram. The owner must explicitly choose a
current visible message; its receive generation, auth/selection epoch, page and original lease
deadlines are then fenced without renewal. Existing preview retains the original request ID.
Manual edits retire the handoff and old approval; hide, conversation/profile changes, stale
selection, expiry or clock rollback clear it. Preview and deliberate approval/send clicks
recheck the handoff before delegating to the existing owner. There is no new effect path.

Ordinary conceal clears page/selection/catalog and increments the source epoch, while the
existing confirmed temporary account/TDLib worker stays READY within its original session.
This handoff therefore requires fresh catalog/selection/read after returning from chat, not a
silent old target. Conceal alone does not require OTP; expiry/auth loss may require reconnect.
Persistent authentication, background reads and automatic contextual drafting stay separate.

Offline acceptance uses the actual Window/composer and current reply engine/owner with public
native fixtures; fixture RPCs are not account acceptance. Real acceptance still requires one
owner-chosen intended personal message, exact intended text, visible review, deliberate approval,
one send and correlated server receipt. Server acceptance does not establish delivery/read.
