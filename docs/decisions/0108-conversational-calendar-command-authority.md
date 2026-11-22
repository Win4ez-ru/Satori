# ADR-0108: Conversational authority for one exact Calendar command

- Status: Accepted for isolated R13 implementation and qualification
- Date: 2026-11-04
- Authority: the user rejects mandatory forms, rigid time entry and repeated confirmation after an explicit request to add an event
- Supersedes: ADR-0104 and ADR-0107 only where they require a separate form, Apply, source-read button or creation confirmation for the exact command below
- Preserves: one Core, CalendarAccessCoordinator, action owner/journal, typed policy and evidence, fresh source validation, one durable attempt and read-only reconciliation

## Intended interaction and command authority

The original request names tomorrow's call at 20:00 and its destination calendar. The current
form has an empty destination picker, rejects end `21` and requires repeated read/review steps.
Ordinary chat must instead ask only for missing information, accept «на час» or «до девяти»,
and carry out the complete direct instruction. Only a verified action receipt permits saying
that the event was added, with the actual title, calendar, date, interval and timezone.

A positive foreground command to add one simple event authorizes that effect once its title,
date, exact interval, timezone and named eligible calendar are resolved. The host derives this
from actual user-authored input. Provider plans, instructions or approval flags cannot grant
it. Missing duration has no silent default. Suggestions or availability questions do not
become creation commands. Recurrence, guests, multiple events, edits and deletion stay outside
this contract; unsupported semantics must not be reduced silently to one event.

Clarification binds to current task/revision, identity, profile, session, generation and the
original monotonic expiry. Preserve original input separately from user corrections. Relative
dates anchor to the civil date of the original command in its timezone, including answers after
midnight. Unrelated input falls through to normal chat. Negation and cancellation cannot become
approval. Natural end-time inference requires unambiguous current-task context; duration bounds
must not choose AM/PM for the user. Ambiguity gets a short question, not an HH:MM form.

## Existing owners and exact approval

The command permits necessary metadata discovery and one bounded target read through the sole
CalendarAccessCoordinator, subject to current macOS full access. Automatic discovery must not
trigger an OS permission escalation. If access is absent, explain the one-time Settings step.
The live catalog must resolve the name uniquely among eligible calendars of the connected
account: writable, allows_modification and supports_events true, excluding subscribed/birthdays.
Existing metadata does not prove exclusive ownership of an iCloud calendar; do not claim it.
Missing/duplicate names require clarification. No personal name or destination fallback is coded.

Reuse a current same-scope selected grant containing that target, or ask the same owner for a
bounded target-only grant. Do not construct a second owner. Local read authority is not cloud
disclosure, unrelated resource access, persistence or background observation. No guests are added.

Use deterministic exact_preview. Compare the complete proposal to the user command and scope:
request/task identity, title, start/end, zone, principal, provider/account and calendar. Only an
exact match becomes the existing typed approval and one action-owner create. Check again after
async boundaries before create. Changed payload/scope/source/task is rejected. Incomplete source
or a conflict is not positive availability. No action-owner, native guard, host lease, bounds or
audit check is removed. Stable task/serial/busy fences prevent duplicate dispatch. Success needs
a correlated verified receipt. UNKNOWN allows read-only reconciliation, never automatic replay
or a fresh create armed by another answer, focus change, timeout or late callback.
The original unexpired foreground lease may remain only for an explicit read-only UNKNOWN
reconciliation. It is never renewed and is revoked on focus loss or expiry. Known verified
terminal outcomes retire leases and task-only grants immediately through the same owner.

## Transient chat and lifecycle

Show short questions/status using existing chat styling, without mandatory forms or technical
permission plumbing. Keep this typed operational channel separate from canonical USER and
SatoriReply, memory, self/relationship evidence, provider context and TTS. No raw chain of thought.

Only user-authored pending fields may survive focus change in RAM within original expiry.
A verified terminal outcome may retain its status and original user-authored summary in RAM;
focus loss must not relabel a known success UNKNOWN. No source snapshot, lease or approval survives.
Revoke external snapshots, source handles, leases and approvals on hide/deactivation. Return
never resumes an effect automatically: require new real foreground input. A task dispatched
before suspension remains uncertain and cannot start over. Session/profile/close/expiry clears.

## Checks and unchanged scope

Tests cover production composer routing, natural followups, midnight anchoring, unique automatic
target, absent permission, one exact effect, payload/late-result rejection, lifecycle boundaries,
UNKNOWN without replay and verified-only success. Public native renders must show the new flow.
Complete Foundation, packaged/native and guarded install checks remain required. Fixtures are
not real-account Calendar or iCloud acceptance.

Character stays paused and Stage15 closed. This adds no broad autonomy, personal source ingestion,
Telegram Send policy change, paid generation, private-profile server transfer or new memory owner.
Portable Calendar and use without an awake Mac remain separate work.
