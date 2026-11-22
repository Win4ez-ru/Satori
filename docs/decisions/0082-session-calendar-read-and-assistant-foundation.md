# ADR-0082: Session-scoped calendar reading and assistant foundation

- Status: Accepted for the bounded implementation below
- Date: 2026-10-20
- Authority: the user's 2026-10-19 request to develop personal-assistant capabilities and explicit
  2026-10-20 continuation; calendar, mail, messages and calls are the requested direction
- Supersedes: only scheduling prerequisites in ADR-0046/ADR-0079 and the future roadmap that
  would require complete 14.4, durable external ingestion, Internet tools or Stage 15 before this
  ephemeral local calendar display; all source, permission and owner invariants are preserved
- Related: ADR-0046, ADR-0053, ADR-0074, ADR-0079, ADR-0080, ADR-0081

## Decision and scope

Open a combined **D3.1 permission/source foundation + D4.1 calendar-read** delivery. The first
scenario is the desktop **My day** view: explicitly connect macOS Calendar, select calendars,
view today/tomorrow/a selected date, refresh, and disconnect. This uses real EventKit access
when the user grants it. It is not a fixture calendar or a claim that mail, Telegram or calls
are implemented. Calendar accounts already configured in macOS are the first adapter; provider
OAuth, account setup and other connectors remain separate implementations.

The existing conversational Core, prompts, model, schemas and persistent state owners remain
unchanged. Character improvement remains paused. A calendar snapshot is a separate attributed
local view, never a fabricated USER message, remembered claim or self/relationship evidence.
Calendar data is not sent to the conversation provider or TTS. A future conversational answer
using sources needs an explicit transient-source context contract and owner-eligibility rules;
putting source text into the existing `send` command is forbidden.

This slice establishes the necessary **narrow** read policy before the adapter. It does not
claim full local authentication, durable grants, encryption/key recovery, credential custody,
external memory ingestion, a general action executor, background sync or completion of 14.4.3.
There are no provider credential bytes, source-body files or new canonical database tables.

## Access and ownership

One ephemeral application coordinator owns the catalog, grant, in-flight request and current
snapshot. The principal binds the actual OS UID, selected profile and owning application
session; it is separate from Satori identity, conversation counterparty and relationship trust.
This is a local process/profile boundary, not protection against arbitrary same-user malware.

The user explicitly selects exact account/source and calendar IDs from the current catalog.
No wildcard, nil calendar list, display-name identity, implicit account merge or automatic
restore is allowed. A read grant lasts at most one hour and binds those resources, principal,
generation, interval/event/byte bounds and local-only display. Restart creates no grant.
Changing profile, disconnecting or expiry invalidates outstanding callbacks and clears content.
Authorization is rechecked before dispatch and after the response; late results cannot restore
revoked data. The snapshot is current for at most five minutes and records its observation time.

Reads are explicit, bounded and cancellable: at most 31 days, 100 occurrences and 512 KiB per
result. The initial UI uses day requests and IANA time zones. Timed instants and exclusive-end
all-day dates are distinct; DST, recurrence instances, cancelled events, wrong resources,
duplicates, partial results and stale responses are checked. No notes, attachments, attendees,
locations, contacts or URL opening belong to this first display.

## Platform adapter

Use a small native Swift helper with Apple's EventKit and Foundation frameworks. This avoids
another Python/Objective-C dependency tree in the existing packaged runtime. The build compiles
and packages the allowlisted helper; installed users do not need Xcode or a source checkout.
The helper has one bounded JSON request/response over owned process pipes and exports only
status, explicitly requested access, calendar listing and selected-event reading. It contains
no calendar write/delete/invitation operation or generic shell/SDK execution path.

Status does not prompt or read event content. Opening the app/tab does not list calendars,
request permission or fetch. A visible connect action precedes any access request. The GUI
owns the helper, enforces response/time limits, discards stderr and maps only closed error
codes. It never displays arbitrary native exception text. Disconnect/Quit stops only the owned
helper; completion remains fenced by the principal and generation.

**EventKit has no OS read-only grant.** Modern macOS event reading requires full Calendar
access, which technically permits OS reads and writes across calendars. The consent text must
say this plainly; Satori's adapter and grant constrain its own operations to reading selected
calendars. The app and helper contain `NSCalendarsFullAccessUsageDescription`. No system access
dialog is accepted automatically, and no test reads a personal calendar merely to establish
API availability. Denied/restricted/write-only/not-determined states remain distinct from a
successful read. Removing Satori's session grant does not falsely claim to revoke macOS TCC;
the UI explains the separate OS setting.

Native event titles and source names are untrusted plain text. They are never instructions,
HTML, automatic links or new actions. Metadata-only audit records contain operation, opaque
request/grant IDs, counts and closed outcomes, never titles, account names, event bodies or
raw provider errors. Deliberate local display remains sensitive content, not publishable telemetry.

## Verification and acceptance

Automated checks cover exact principal/profile/account/calendar scope; expiry and generation;
revoke during fetch; restart and no startup I/O; injected titles; bounded input/response;
DST/all-day/recurrence/cancellation; duplicates and partial reads; no body in audit; process
timeout/failure/cancel; and no source-to-Core/provider/TTS path. Run the mandatory Foundation
toolchain with a fresh noneditable rebuild first. Verify the actual compiled helper's bounded
status/error protocol without requesting access, the frozen bundle/resource/signature boundaries,
and native UI in an isolated profile.

Real account acceptance requires user-driven OS consent, selected calendars, a visible bounded
read, refresh/change and disconnect/revoke checks. Without this, platform compilation, fake
transport and UI checks are engineering evidence only. Personal contents are not copied into
reports or test fixtures. Calls, mail/message sends, paid voice trials and account credentials
are not silently exercised by this gate.

## Primary platform references

- [Apple: Accessing the event store](https://developer.apple.com/documentation/eventkit/accessing-the-event-store)
- [Apple: Updated Calendar access levels](https://developer.apple.com/documentation/technotes/tn3152-migrating-to-the-latest-calendar-access-levels)
- [Apple: Retrieving events and reminders](https://developer.apple.com/documentation/eventkit/retrieving-events-and-reminders)

The implementation and actual acceptance status are recorded in `../progress.md`; the older
D1/D2 records retain their historical scope.
