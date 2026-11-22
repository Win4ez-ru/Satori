# ADR-0096: Daily assistant actions and a paired iPhone client

- Status: Accepted for the bounded implementation and qualification below; actual account,
  device and notification delivery acceptance remain separate
- Date: 2026-10-31
- Authority: the user's explicit daily-assistant implementation request, including real tool
  chains, durable reminders, iPhone, design, controlled permissions and parallel coordination
- Extends: ADR-0079, 0080, 0082, 0083, 0090, 0092 and 0095
- Supersedes: ADR-0083's additional per-question checkbox only for an explicit recognized
  assistant request against an already valid exact selected-calendar grant
- Preserves: canonical Core/self/memory/relationship owners, source exclusion, grants,
  explicit effect authority, manual voice Send and Character pause; domain Stage15 stays closed

## Evidence and delivery scope

Installed16 has a normal chat and separate Calendar/Gmail/Telegram panels. Its ordinary Send
does not dispatch tools. Calendar has a usable typed read/grant/answer owner; the iPhone app
only reviews/imports shifts. There is no shared reminder runtime or paired mobile transport.
The user explicitly opens this implementation workstream; old roadmap exclusions do not turn
these requested surfaces into forbidden incidental work. They do not authorize changing the
personality/memory evidence rules or silently enabling permissions.

Build complete, separately verifiable user scenarios: current selected-calendar plans;
create/list/cancel durable reminders; selected-source lookup; explicitly selected text files;
bounded public information/places and external routes; PTT through the same dispatcher; and a
native paired iPhone chat against the same running Mac Core. Individual source adapters may
remain unavailable pending real credentials/device/provider eligibility, with truthful UI.

## Typed task surface, separate from canonical conversation

An assistant dispatcher runs before ordinary canonical Send. Recognized requests become typed
intents with request ID, explicit timezone, finite operation/arguments and exact source scope.
Unsupported or ambiguous operations produce a clarification/action card, not a fabricated
success or an unbounded tool call. Ordinary conversation retains its existing route.

External-source requests, tool data, result cards and diagnostics never become `send.text`,
canonical USER messages, new memory/self/relationship evidence or automatic TTS input. A
separate transient task surface may visually sit next to conversation. Its labels distinguish
user-authored task requests, external observations and tool receipts. No hidden second LLM or
provider call is introduced merely to route deterministic dates, permissions or operations.
Model-assisted synthesis of private source content requires a later explicit provider-aware
disclosure contract; it is not opened by this ADR.

Every result carries operation, request correlation, finite status, origin and observation
time/expiry where applicable. Cancellation, profile/session/generation change, source revoke,
deactivation and expiry fence late callbacks. A result must not cross into another profile,
conversation, selected source or phone. Tool errors distinguish disconnected, permission
required, empty, partial, unavailable and unknown execution outcomes.

## Calendar and selected-source reads

Reuse the existing Calendar controller/coordinator and exact valid selected resources. An
explicit assistant question authorizes that exact bounded local-display read while the source
grant is valid; no additional question-specific checkbox is needed. It does not connect an
account, select resources or renew an expired grant. If a grant is absent, show the concrete
connection/selection step. Claims concern events in selected calendars, not comprehensive
personal availability. Existing EventKit write policy and reviewed shift import remain unchanged.

Clear the captured composer draft before creating a new calendar proposal/lease, so its own
textChanged event cannot cancel the dispatched read. Subsequent real edits still invalidate
the appropriate request. Source text remains transient and is never rewritten into history.

Mail and Telegram search initially operate only on an already admitted fresh selected snapshot
or visible selected page. State the actual search coverage; no implied whole-account search.
Telegram foreground/conceal authority cannot be retained by moving data from a hidden panel.
A simultaneously visible source/result surface or an explicitly source-local search is needed.
No Send, message deletion, background mailbox read or persistent credentials are implied.

## Durable local reminders and tasks

One dedicated reminder owner stores typed user-authored reminder intents outside canonical
personality/memory databases. Use a private bounded store, explicit schema/version, atomic
transactions and stable request-ID/payload binding. Repeating the same request is idempotent;
the same ID with different arguments rejects. Record create/cancel/due/delivery status and
minimal structured audit metadata without reminder text in diagnostic logs.

Resolve time deterministically with an explicit timezone. Ambiguous expressions such as
"tomorrow evening" require a visible exact-time proposal/clarification unless the user has
configured an applicable default. Do not infer a user habit or select a DST fold silently.
The UI shows the exact title/time before an uncertain or otherwise confirmation-required write.
An explicit unambiguous request or a configured scenario grant supplies effect authority.

Persistence and notification delivery are separate states. A saved row does not prove an OS
notification is scheduled or delivered. Native notifications require their own permission and
correlated scheduling/readback receipt; timeout after dispatch is unknown, followed by bounded
read-only reconciliation rather than blind resubmission. Do not claim closed-app/sleep delivery
from a QTimer or tray balloon. A working foreground scheduler may show overdue reminders after
restart while accurately reporting unavailable OS/background delivery.

## Native mobile client and single Core owner

The iPhone is a client of the running Mac, not a second identity or canonical SQLite writer.
Retain photo/Calendar import as a separate workflow. Build native Chat/Today/Settings with
proper safe areas, keyboard handling, long-text scrolling, offline draft and clear connection
states. Cached history, if any, is a read-only stale cache, not another memory authority.

The Mac gateway is default-off and starts only after an explicit setting/action. Initial
scope is a private local network, with no router change, public listener deployment, cloud
account or VPN enrollment. The UI states that the Mac must be running and reachable. A phone
grant contains finite allowed capabilities, identity/profile binding, expiry and revoke.

Use TLS and an explicitly paired certificate SHA256 pin. A short-lived one-use pairing code
is displayed locally, rate limited and exchanged for a cryptographically random device token
only during the pairing window. Store only token digests on Mac; store phone credentials in
device-only iOS Keychain. Keep TLS private material owner-private, reject unsafe paths/owners,
and never log pairing codes, tokens, request bodies or authentication headers. No certificate
validation bypass or general ATS allow-all setting is permitted; the native client's explicit
certificate pin is restricted to the paired host/port. Re-pairing/revoke closes old authority.

Expose only bounded authenticated status/sessions/history/chat and separately granted task
routes. A request binds device, profile/generation, conversation and request ID. No API opens
arbitrary files, executes commands or calls another provider with attacker-supplied credentials.
All network requests enter the same existing owned Core process through a serialized broker.
The backend must support atomic explicit-session Send; separate resume-session then Send from
different clients is unsafe. A phone cannot silently switch the desktop's selected session.
Unknown send outcomes are reconciled by request ID; no automatic replay after reconnect.

Local mutating reminder actions received through chat also verify the exact selected open
session before their effect. A repeatable authorization preflight guard admits the existing
worker's bounded explicit-session history read; its body is discarded. After the exact open
session receipt, revalidate device/profile/epoch/deadline and consume the one-use effect guard
immediately before the reminder owner call. Revocation during preflight permits no effect.
Known pre-effect source/permission/session refusals return terminal assistant cards; they must
not strand the phone in an unknown pending send. Uncertain reminder commits remain unknown.
Read-only reconciliation uses the durable request index for both create and cancel, including
after restart; it neither repeats an action nor enables/recovers a disabled store implicitly.

Phone backgrounding cancels ordinary foreground reads/capture without claiming a cancelled
write did not commit. Restore an offline draft without resending. Network changes retain
pairing but require fresh authenticated requests. Notifications/microphone/local-network
permissions remain explicit platform permissions; physical acceptance is separate from Simulator.

## Files, public information, places and context

Selected-file access binds an explicit chosen regular file, content/size/type and one-use read.
No whole-directory crawl or hidden document upload. File contents are untrusted and transient;
external provider disclosure needs its own preview/permission. Public URL reading is bounded,
checks redirects/destinations against private-network/SSRF targets and executes no scripts.
Search provider coverage, missing keys and actual opened-link outcomes must be named honestly.

Places use an explicit query and explicit city/area; current location is off by default and
must not be guessed from IP, calendars or profile. Preserve provider display/licensing rules,
including an Apple map for MapKit results. Unknown opening hours stay unknown; no unsupported
"open now" claim. Opening a selected route is a separate visible user action with exact target.

Context metadata, screen content and location have independent default-off scoped controls,
visible Start/Pause/Stop/revoke and bounded lifetime. No hidden observation or credential/window
content collection. Unknown lock/unlock is not treated as trusted unlocked state. A capability
whose platform gate cannot be qualified remains visibly unavailable rather than bypassed.

## Credentials, effects and verification

Existing session-only Gmail/Telegram/voice-key custody is not silently changed. A remember-login
option requires a separate concrete native storage qualification and scoped explicit enablement.
The old failed Keychain fixture is not a reason to relax ACL/ownership checks or enumerate tokens.
Calls/provider eligibility remains external and cannot be inferred from a public price page.

Root coordinates disjoint implementations and one integrated freeze/complete Foundation/build
gate. Test idempotency, restart, unknown reconciliation, time/expiry/late-result fences, source
exclusion, mobile pairing/revoke/concurrent sessions and private-data-free logs. Preserve prior
installed16 and unrelated work. Native UI, real account, actual notification, Simulator and
physical iPhone outcomes are recorded separately. No mock, screenshot or successful build is
reported as a real external action. Explain each remaining manual step after preparing its
complete implementation. Delivery records distinguish available functionality and blockers.
