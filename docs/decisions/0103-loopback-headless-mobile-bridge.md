# ADR-0103: Loopback headless mobile bridge preparation

- Status: Accepted for isolated source implementation and qualification only
- Date: 2026-11-01
- Authority: the user's server-independent Satori goal and coordinator's concrete loopback integration task
- Extends ADR-0096/0099; preserves ADR-0100/0102 model, accounting and state boundaries
- Isolation: `var/artifacts/server-runtime-20261031/work-mobile-headless-r5`

## Decision and scope

Reuse the existing finite mobile wire, AuthOwner, Broker, private metadata store and TLS
transport with an injected, already started HeadlessRuntime. A new thread-safe bridge schedules
into that owner's event loop; it never constructs another backend, changes the selected
desktop session, or implements Send by resume-then-Send. The CoreBridge protocol's older
Qt/QProcess-only wording is extended for this injected headless implementation. There is one
Core and one serialized owner lane. No model, prompt, canonical state schema or Character
behavior is changed. Character remains paused and domain Stage 15 closed.

Construction is inert. The trusted host must explicitly start a LoopbackMobileGateway. Its
bind and advertised host are fixed to `127.0.0.1`; no public, LAN, wildcard, DNS, tunnel or
reverse-proxy configuration is exposed by this wrapper. The existing allowed_bind policy and
native iPhone local-host restriction remain unchanged. No new production CLI or service is
installed. This is preparation for an authenticated remote client, not remote delivery.

The trusted host supplies a previously qualified ScopeBinding. This slice is not a new binding
owner and may not infer, adopt or migrate a profile from a path, hostname, OS uid or existing
desktop grant. Real host binding and whole-installation migration remain separately reviewed.

## Admission, effects and reconciliation

The bridge admits one call at a time. Every operation retains the exact device, scope,
generation, request, conversation and finite deadline. The Broker's dispatch_guard is checked
for reads inside the scheduled HeadlessRuntime lane; for Send/session creation it is passed
through to DesktopBackend and consumed only after its internal turn lock is acquired, directly
before the owner operation. Queue wait must not preserve a revoked grant. Callback publication remains correlated and
revalidated by the Broker. Exceptions carry closed codes only, never private bodies or causes.

Add a typed `new_session` HeadlessCommand requiring request_id and a dispatch guard. The
gateway's existing durable intent journal is written before dispatch and owns duplicate-ID
payload binding. Direct stdio supplies no durable journal guard and cannot create a mobile
session. The existing backend's new_mobile_session owner creates the session without changing
desktop selection. An unknown create or send outcome is never retried automatically.

Completed response bodies stay RAM-only. A journal reloaded after restart may report a known
session ID with unknown response state; that is not authority to replay session creation.
Conversation reconciliation reads the existing canonical request index and never sends again.
This slice does not invent stronger recoverability for session creation than the existing
mobile journal contract provides. The journal remains bounded to 512 records and fails closed
at capacity; production retention remains a separate decision.

Cancellation of a client or close waiter does not cancel dispatched Core work or release its
lease early. Closing stops network admission and waits for the injected bridge and runtime to
drain. An uncertain owner drain remains unknown; no replacement backend starts automatically.

## Source and task boundaries

The existing deterministic assistant parser runs before canonical Send. Every recognized
Calendar, selected Gmail/Telegram lookup, reminder or clarification intent returns a separate
unavailable/connection-required task card in this slice. No source/reminder owner is injected,
no data is read or modified, and no task request/result is passed to canonical conversation,
memory, provider or TTS. Status explicitly labels these sources disconnected with no
capabilities. Pairing grants may contain status/history/chat only; reminders are not granted.

Ordinary conversation uses the existing explicit-session Send and selected GPT profile. This
adds no external-source disclosure, persistent mailbox/TG credentials, background observation,
phone source capture, notification delivery, location, voice or call capability.

## Qualification and remaining delivery

Use synthetic stores and providers to test revoked-while-queued admission, guard checks after
async scheduling and a blocked backend lock, timeout/unknown/reconcile without duplicate Send, device/session isolation,
durable new-session deduplication, restart journals, shutdown with active work, no Qt import or
second Core, source-intent exclusion, and an actual synthetic loopback TLS pairing/chat/revoke.
Focused source-import diagnostics are not the required fresh noneditable Foundation gate.
Preserve the exact frozen R4 source and its receipts; qualify this R5 separately before any
selective integration or packaging.

Public remote endpoint/enrollment, certificate renewal and pin rotation, Linux resources and
process qualification, encrypted backup/restore, reviewed single-writer profile transfer,
production journal retention and physical Mac-asleep iPhone acceptance remain unimplemented.
The current 30-day pinned leaf and device grant lifetimes are unchanged. No provider account,
public listener, credentials, personal-state transfer or real model request is performed by
these synthetic diagnostics. Existing November shifts must not be imported again.
