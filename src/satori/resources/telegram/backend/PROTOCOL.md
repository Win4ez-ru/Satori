# Temporary Telegram client JSONL protocol v1

## Workday session lifetime

The temporary confirmed login lasts until local Disconnect, application exit or 86400
seconds from the original Connect, whichever comes first. Confirmation, focus changes,
source reads and task searches never renew that deadline. Authorization must still finish
within the original 300 seconds. Account and Ready events may advertise at most 86400
remaining seconds; legacy shorter session events remain valid.

Source permissions keep their separate bounds: selected-chat access is at most 900 seconds,
and a displayed page at most 300 seconds. Expiry clears the catalog, selection, text,
handles and pending view operation, closes the opened chat and returns a confirmed client
to READY without fetching anything or renewing authorization. An RPC interrupted by that
expiry retires its exact correlation token. The correlated cleared event finishes the host
view request; its late answer is never admitted as a new result. Any failure during cleanup,
auth loss, the original login expiry or clock rollback still stops the client. Temporary
TDLib storage and its in-memory encryption key are removed on local close as before; no
restart persistence or remote Logout is claimed.

## Desktop App26 focus and authentication lifecycle

While authorization or account confirmation is pending, the desktop hides the login
surface and clears entered secret fields on ordinary focus loss. The current challenge
or bounded account preview stays in memory and retains its original deadline. The desktop
does not send a Conceal command for this transition.

After confirmation, Conceal retires the chat catalog, selection, page and pending view
commands. If it interrupts an admitted native RPC, the worker retires that exact correlation
token, stops the visible operation, and processes the queued Conceal or Close. An
interrupted openChat is included in the normal closeChat cleanup. The desktop waits for
the correlated CONCEAL/NONE status before admitting another view command. The existing
account and session deadline remain in force. No source read is replayed on return.
If a new-message stale-page transition races with the queued conceal, conceal owns
retirement of the whole view and the account remains confirmed.
Authorization loss, malformed input, foreign replies, timeout and output loss still stop
the client. Close and EOF still end the local session. The JSONL envelope is unchanged.

## Revision r5 selected-page freshness without account teardown

The user reports that the actual r4 page disappears after roughly 30 seconds; closed UI
metadata is IDLE/SOURCE_CHANGED/IDLE/IDLE_DRAIN/NONE/NONE, normal exit 3 and cleanup
CONFIRMED. The user additionally confirms that a new message arrived in the selected chat.
R4's closed reason does not identify its exact update subtype, so this new observation is
not retroactively promoted into a captured native cause. R4 source and receipts remain frozen.

R5 changes only the ordinary new-selected-chat-message transition. A strictly typed,
nonzero native chat/message identity in an `updateNewMessage` message envelope invalidates
the displayed page and any in-flight explicit Read. It raises a separate `SnapshotStale`,
not the terminal `Rejected(SOURCE_CHANGED)` used for identity/privacy rechecks or other
material changes. No new message body is normalized, admitted, displayed, copied or logged.
The new transition is recoverable only during idle, explicit Read or viewport processing;
conceal/close and other source-management commands retain conservative terminal behavior.

The main loop catches this specific typed condition per iteration. Before emitting any
stale-page event, it clears the page, all admitted message bodies, row handles and dirty
metrics. The confirmed authorization/account, selected chat/token, exact grant/session
deadlines and already opened native chat remain unchanged. No source or metrics RPC is
sent by the transition, no grant is renewed, and no history query is retried or fetched
automatically. A new Read must come from a new explicit user command and performs the same
fresh identity/privacy and time checks as before. Text edits/deletions, protection, TTL,
content-opened, auth loss, expiry, clock rollback, malformed frames and identity mismatch
remain terminal. Pin metadata remains excluded as in r4; unrelated-message filtering does
not expand in r5.

`page_stale` has exactly the standard event envelope plus `reason:"NEW_MESSAGE"` and
`page_id`, the invalidated opaque GUI page ID or null. Idle invalidation always carries the
old page ID with null request_id. A null page_id is permitted only for a correlated aborted
Read whose page has not yet been published. During Read/viewport processing, request_id is
that existing explicit command's ID. No native message/chat/account ID, raw native type or
body appears. After this event the worker reports SELECTED/NONE and waits for user action.
The frontend matches the invalidated page or current pending Read before bumping its source
epoch, so an old page's delayed invalidation cannot cancel a separately queued fresh Read.

An RPC interrupted by SnapshotStale retires its already issued @extra token (bounded by
4096). Its late answer is consumed only as retired metadata, never as a source result. A
viewport command carrying an exact known retired page ID is ignored after current grant,
authorization, session and clock checks, without any RPC. Unknown page IDs/foreign native
responses remain rejected. Retired page IDs are bounded by 4096 and hold no bodies.

An interrupted online-option RPC can have reached Telegram before its acknowledgement.
Its state remains explicitly uncertain rather than incorrectly claimed offline. The next
safe maintenance tick may reconcile only the ordinary offline option; stale-page recovery
itself performs no RPC. This does not repeat a Read, view acknowledgement or metrics report.
The source/grant/clock and malformed-input checks still precede external dispatch.

Native decoding, its finite 4 MiB budget, depth512/numeric384, strict correlation, the
cooperative command reader, and GUI/page projection budgets are unchanged. The r4 decoder
resource measurements remain historical supporting evidence, not a newly repeated r5 run.
Actual r5 account/Read acceptance remains NOT_RUN until separate user-owned verification.

## Historical revision r4 bounded native decoding and pin metadata

The actual r3 user trial briefly displays messages, then stops with closed metadata
`IDLE / CLIENT_INVALID_NATIVE_FRAME / IDLE / IDLE_DRAIN / JSON_STRUCTURE_LIMIT /
GT1M_LE2M`, normal process exit 3 and cleanup CONFIRMED. This is transient display,
not sustained selected-read acceptance. The combined r3 reason cannot establish
whether its 64-container or 200000-punctuation predicate failed; no real frame,
object type, identifier or content is collected.

The finite **4 MiB native transport JSON budget is unchanged** from r3. Pinned
TDLib `ClientJson.cpp::from_response` uses a growable `StringBuilder`; its 256 KiB
initial allocation is not a wire maximum. Diagnostic size buckets retain fixed
512 KiB/1/2/4/8/16/32/64 MiB boundaries. The bounded 64 MiB+1 length probe remains
so rejection buckets are honest; frames over 4 MiB are never copied.

R4 removes the punctuation quota. The byte budget already bounds JSON width:
for B bytes, valid JSON has at most floor((B+1)/2) value/key nodes, so at most
2097152 at this transport limit. This is a node-count bound, **not** a 4 MiB heap
claim: Python containers and strings allocate more memory than their wire bytes.
A fresh-process synthetic decoder benchmark records near-limit shape-specific
RSS and elapsed time separately from the account-free regression suite.

A linear bounds-only scan excludes quoted/escaped string contents and limits
nesting to **512 containers**, with distinct `JSON_DEPTH_LIMIT`. This finite
engineering guard is not a TDLib output-depth guarantee; TDLib's request decoder
DEFAULT_MAX_DEPTH does not specify its output serializer's depth. Numeric literal
conversion is limited to **384 bytes** with `JSON_NUMBER_LIMIT`; ordinary string
contents are exempt. Pinned `JsonFloat` uses `StringBuilder(double)` and
`FixedDouble(...,6)`, whose finite maximum double may occupy 317 characters
including a minus sign. The 384-byte limit leaves margin over this representation.
The same strict numeric parser also protects GUI commands, whose admitted numeric
fields have much smaller deterministic bounds.

Full JSON grammar, duplicate decoded keys, strict UTF-8, finite numbers, envelope,
exact native client ID and response correlation still apply. Exponent overflow
such as `1e999` is invalid JSON; it cannot become Python infinity. Legacy
`JSON_STRUCTURE_LIMIT` remains in the schema for historical diagnostics but the
r4 worker emits the two distinct bound reasons above. No frame is truncated,
prefix-filtered, skipped on decoder failure or automatically retried.

Pin-only `updateMessageIsPinned` no longer invalidates the frozen text page:
that projection contains no pin field. This narrow metadata correction covers
both rendered and unrendered message IDs in the selected chat, without another
RPC or restoring/repeating a read. Real text edits/deletions, protection/TTL,
content-opened, new selected-chat messages and authorization loss preserve their
previous invalidation behavior. The actual r3 stop remains JSON_STRUCTURE_LIMIT;
a pin refresh is a separately proven source bug, not its observed cause.

Idle draining preserves r3's guard-only expiry check after receiving a decoded
frame and before applying it. Presence/metrics maintenance cannot dispatch an RPC
before a held invalidating update is applied.

**Projection limits do not change:** GUI input 32 KiB, GUI output 512 KiB, one
explicitly selected page of at most 50 messages, 16 KiB per admitted text and
256 KiB admitted text total. Larger account-wide updates still have no source
grant, normalization, output, Core, model, memory or TTS path.

## R3 owned command reader and interpreter shutdown

The actual worker's Python crash report identifies `_enter_buffered_busy` during
`_Py_Finalize`. R2 left a daemon thread blocked in `sys.stdin.buffer.readline`
after the main loop returned. R3's production reader never uses Python buffered
stdin: it duplicates the raw input fd, marks it noninheritable/nonblocking and
has one non-daemon owner read with `select`/`os.read` in bounded 50 ms polls.
The reader alone closes its duplicate in `finally`; it does not close the borrowed
stdin fd. Setup/start failures close the still-untransferred duplicate.

The incremental buffer remains bounded by the same 32 KiB line budget, requires a
newline and uses the same strict JSON parser. The queue remains 16 commands; puts
have the prior one-second backpressure bound, now in stop-aware 50 ms slices.
Conceal, close, EOF and malformed input signal interruption before queue delivery.
On main-loop shutdown, a `finally` signals the reader to stop and joins for up to
two seconds before queued command dictionaries are released. No `os._exit`, thread
kill, fatal-output suppression or real-account retry is used as a shutdown fix.
The old blocking `read_commands(BytesIO)` helper remains only for authored tests.

## Preserved r2 implementation notes

The wire shape and 512 KiB output limit are unchanged. Catalog enumeration
validates nonzero signed int53 dialog IDs, correlates each getChat response to its
exact requested ID, and only then filters known chat types. Only positive ordinary
private chats with equal strictly typed chat/user IDs and a matching regular-user
response are admitted. Fresh select/read checks apply the same strict integer
identity rules; bools, floats, unknown variants and foreign IDs are rejected.

Loss of the private stdout pipe permanently revokes further source dispatch.
Bounded native close and owned-session cleanup continue even if no terminal status
can be delivered to the GUI. A broken output pipe is not proof of remote Logout,
physical erasure or the cause of the user's catalog failure. GUI transport ownership
and first-failure diagnostics are documented separately in TRANSPORT.md.

This revision is an engineering diagnostic handoff. Its account-free tests and
no-parameters native startup probe do not authorize or repeat a real account login.

This is a new, standalone engineering client, not a reconstruction of historical r7.
The worker uses only the Python standard library. It imports no Satori/Core module.
The GUI starts it only after explicit Connect. No worker or session directory is
needed for the disconnected UI. Command:

```
PYTHON -I -S -B worker.py --library ABS_DYLIB --library-sha256 SHA256 --session-root ABS_OWNED_EMPTY_ROOT
```

The frozen parent launcher binds the worker, Python and SDK bytes. SDK library hash
is checked again before loading. Never put credentials in arguments or environment.
One private child stdin/stdout pair carries UTF-8 JSON lines. stdout is IPC, not a log.
stderr never contains exception text, native frames, source data or credentials.
Input frame limit is 32 KiB; the r4 native transport frame limit is 4 MiB. Duplicate request
IDs, unknown fields or commands fail closed. All commands have `v:1`, `command` naming the operation below, and a unique
`request_id` matching `[A-Za-z0-9_-]{1,80}`. `close` is accepted at every live phase.

## Commands

* `connect`: `session_only:true`, `api_id` positive int32, `api_hash` 32 hex characters.
* `auth_submit`: `challenge_id`, `kind` (`PHONE`, `CODE`, `PASSWORD`), `value`.
  The current one-use challenge ID and kind must match; input must use a secure field.
* `confirm_account`: `account_token` from the current account event. Separate human action.
* `account_preview`: no additional fields. After conceal while awaiting confirmation,
  explicitly repeat getMe and issue a new account token. The old token stays invalid.
* `catalog`: no additional fields. Separate catalog consent action; 60-second catalog.
* `select_chat`: `catalog_id`, `chat_token`, `read_view_consent:true`.
  Exactly one supported private cloud chat. This grants no automatic Read.
* `read`: no additional fields. One page, at most 50 messages, last 24 hours,
  at most 16 KiB per text and 256 KiB total. No automatic pagination.
* `viewport`: `page_id`, integer increasing `sample_id`, `foreground` bool,
  `active` bool, `viewport:[top,bottom]`, `rows:[{handle,top,bottom},...]`.
  `active` means the GUI currently has a measured user action within 15 seconds,
  not a new action on every timer tick. The worker adds no second activity window;
  presence goes offline if a fresh viewport sample is absent for more than one second.
  Rows contain full rendered text-body spans in backing pixels. GUI applies one
  common integer origin AFTER floor/ceil quantization. Top >= 0, bottom > top;
  maximum coordinate 1,000,000. No clipping or per-row normalization. Each handle
  belongs once to the current page. Positive intersection means visible.
  Total view time accrues for foreground samples <=1 s apart; active time is the
  intersection with the 15-second activity window. Reports flush after >300 ms
  unseen, before a valid viewport is destroyed, and when duration reaches five minutes.
  Invalidated source metrics are dropped; reported durations are reset, not sent twice.
* `conceal`: no additional fields. Flushes valid pending view metrics, then clears source view/grant;
  no automatic Read or grant restoration on return.
* `close`: no additional fields. Closes this owned client locally. This is not Logout
  or proof of Telegram Devices revocation.

## Events

Every event has `v:1`, increasing `event_seq`, `type`, `request_id` (current UI
request ID or null for an unsolicited source update). Only these fields are added:

* `status`: `phase`, `reason`, `operation`, `step`, `decoder_reason`, `wire_bucket`.
  These are closed metadata. `phase` includes DISCONNECTED, NATIVE_START,
  PARAMETERS, AUTH, ACCOUNT_CONFIRM, READY, CATALOG, SELECTED, READ, IDLE,
  CLOSING, STOPPED. `reason` NONE means nonterminal progress.
* `auth`: `challenge_id`, `kind` PHONE/CODE/PASSWORD. No hint, code, phone or password.
* `account`: `account_token`, `label`, `session_seconds` (<=86400). Label is sensitive
  local UI text from verified getMe (name, optional username and masked phone),
  not an audit identity or log field. Numeric identity stays internal to the owner.
* `ready`: `session_seconds` (<=86400). No catalog/read happens automatically.
* `catalog`: `catalog_id`, `expires_in_seconds` (<=60), `chats:[{chat_token,title}]`,
  `bounded:true`. Titles are sensitive UI text. Max 100, only ordinary private chats.
* `selection`: `chat_token`, `title`, `grant_seconds` (<=900).
* `page`: `page_id`, `expires_in_seconds` (<=300), `messages:[{handle,sender,text,date}]`,
  `bounded:true`, `omitted` integer. Text/sender are local sensitive UI projection.
  Native chat/message/user IDs are not exposed; handles are random opaque tokens.
* `page_stale`: `reason:"NEW_MESSAGE"`, `page_id` opaque invalidated page ID or null for an
  aborted correlated Read. Drop the matching page/pending result; retain the existing grant
  and deadlines. Exact ordering and race rules are specified by revision r5 above.
* `cleared`: `reason` closed metadata. Clear all page text, row handles and selection.
* `closed`: `cleanup` CONFIRMED/PENDING/NOT_CREATED, `reason` closed metadata.
  CONFIRMED means owned directory removal observed, not secure erasure or remote logout.

`status` must be plainly visible in UI, including operation/step/decoder reason/bucket;
not tooltip-only. Native failure reports `CLIENT_INVALID_NATIVE_FRAME`, clears source
as SOURCE_REJECTED, and stops. The final STOPPED status retains the original failing
operation/step/decoder tuple across cleanup. `decoder_reason` is one of FRAME_TOO_LARGE, INVALID_UTF8,
INVALID_JSON, JSON_STRUCTURE_LIMIT (legacy), JSON_DEPTH_LIMIT, JSON_NUMBER_LIMIT,
INVALID_ENVELOPE, INVALID_CLIENT_ID, UNKNOWN (or NONE when irrelevant).
`wire_bucket` is EMPTY, LE512K, GT512K_LE1M, GT1M_LE2M, GT2M_LE4M,
GT4M_LE8M, GT8M_LE16M, GT16M_LE32M, GT32M_LE64M, GT64M, UNKNOWN, NONE.
No exact received byte count, native @type, raw error or source content is diagnostic.

Protection/TTL changes, material text edits/deletions, authorization loss,
session/grant/request expiry, clock rollback and malformed/foreign native frames
clear the page and stop source access. A well-formed new selected-chat message follows
only the separate r5 stale-page transition above. No native retry, decoder skip or dynamic/per-frame cap
relaxation occurs. The reviewed r3/r4 transport budget above supersedes r2's static 512 KiB
native cap without changing projection or selected-source limits.
Hide/conceal revokes source selection while a still-valid authorization can remain.
During AUTH it keeps only the current challenge type/ID and never the entered secret.
During ACCOUNT_CONFIRM it invalidates the account token; explicit account_preview is
required to show and confirm the account again. Closed enum sets are in schema.json.
Source content never enters Core, model, TTS, memory, canonical USER, drafts or Send.
An existing selection cannot be overwritten. Obtain a fresh catalog first; this
closes the previous owned chat view and revokes its page before another selection.
