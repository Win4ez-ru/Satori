# Native Calendar helper

`main.swift` builds one one-shot macOS 14+ EventKit adapter. ADR-0090 extends its four
read operations with local image OCR and narrowly reviewed schedule preflight/create/reconcile.
It exposes no calendar creation, existing-event editing/deletion, invitation, recurrence or
alarm API. OS permission, the application's selected-resource read grant and its separate
one-use reviewed-create permit remain distinct boundaries. A read grant never grants creation.

## Build and packaging

The entry point is top-level `exit(main())`; do not use `-parse-as-library`.
For the current arm64 target:

```sh
xcrun swiftc -O -swift-version 5 \
  -target arm64-apple-macosx14.0 \
  -module-cache-path /absolute/private-build/module-cache \
  -framework Foundation -framework EventKit -framework Vision \
  -framework ImageIO -framework CoreGraphics -framework CryptoKit \
  -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist \
  -Xlinker /absolute/private-build/SatoriCalendar-Info.plist \
  src/satori/desktop/calendar_native/main.swift \
  -o /absolute/private-build/SatoriCalendar
```

The linked helper Info.plist must contain a stable `CFBundleIdentifier`,
`CFBundleExecutable=SatoriCalendar`, `CFBundleName`, `CFBundleVersion`, and a nonempty
`NSCalendarsFullAccessUsageDescription`. The responsible parent app also needs that
usage description. Bundle the executable at
`Contents/Resources/calendar-native/SatoriCalendar`, exposed to the Python adapter as
`sys._MEIPASS/calendar-native/SatoriCalendar`. The enclosing app's signing/permission
verification remains part of packaging. App Sandbox, if introduced later, requires a
separate calendar entitlement review.

## Input

One UTF-8 JSON object arrives through stdin, followed by EOF. The helper accepts at
most 256 KiB, rejects unknown keys and wrong types, and handles one operation per
process. No data belongs in command-line arguments.

Common required fields:

```json
{"schema_version":1,"request_id":"123e4567-e89b-42d3-a456-426614174000","operation":"status"}
```

`operation` is exactly `status`, `request_access`, `list_calendars`, `read_events`,
`recognize_schedule_image`, `preflight_schedule`, `create_schedule` or `reconcile_schedule`.
The first three accept only the three common fields. `status` only calls the static
authorization-status API; it does not instantiate a store, request permission, list
calendars or read events. `request_access` alone can call
`requestFullAccessToEvents`, and only after validating the embedded usage description.
An already denied/restricted permission is reported without another request.

`read_events` additionally requires every following field:

```json
{
  "selected_calendars":[{"source_id":"opaque-source-id","calendar_id":"opaque-calendar-id"}],
  "start":"2026-10-20T00:00:00+03:00",
  "end":"2026-10-21T00:00:00+03:00",
  "time_zone":"Europe/Moscow",
  "max_events":100
}
```

The selection contains 1–100 unique source/calendar pairs. IDs are nonempty,
control-free strings of at most 512 Unicode scalars, without wildcard `*` or the
reserved value `all`. Each calendar is resolved by its exact ID and checked against
its exact source ID before querying. An absent calendar or mismatched source fails
the whole operation. No nil/empty/wildcard calendar predicate is created.

Dates must contain an explicit `Z` or `±HH:MM` offset. The half-open range has positive
duration and is at most 31 elapsed days. `time_zone` must be a known IANA identifier
(including UTC); `max_events` is an integer from 1 through 100. Non-status data reads
require `full_access`; write-only access cannot read.

## Output

One JSON object plus a newline is written to a reserved stdout descriptor, at most
512 KiB including that newline. Native/vendor stdout and stderr are discarded.
Every response contains exactly these common fields:

```json
{
  "schema_version":1,
  "request_id":"123e4567-e89b-42d3-a456-426614174000",
  "operation":"status",
  "status":"ok",
  "access":"not_determined",
  "error":null,
  "observed_at":"2026-10-20T00:00:00.000Z"
}
```

`status` is `ok` or `error`. Access is `not_determined`, `restricted`, `denied`,
`write_only`, `full_access`, or `unknown`. `observed_at` is the actual response/fetch
completion wall time in UTC. Successful catalog responses add only `calendars`;
successful event responses add only `events` and `partial`. Error responses have no
data payload. An undecodable request uses the all-zero UUID and `status` operation
as its safe fallback envelope; callers must require their own correlation.

Calendar descriptors have `source_id`, `calendar_id`, `title`, `source_title`, `writable`,
`allows_modification`, `supports_events` and `source_kind`. The two diagnostic booleans are
exact projections of `EKCalendar.allowsContentModifications` and
`EKCalendar.allowedEntityTypes.contains(.event)`. Eligibility remains exactly their conjunction:
`writable = allows_modification && supports_events`. There is no fallback or inferred permission.
New helper responses include both facts; compatible Python readers may treat their absence
in an older wire response as unknown, rather than as an observed false value. These facts are
reported only by an explicit catalog operation; compilation or synthetic tests do not establish
the current OS values or explain an empty target selector. Source kind is exactly `local`, `exchange`, `caldav`, `mobileme`,
`subscribed`, `birthdays` or `unknown`. These are EventKit source types: `caldav` alone
does not establish that an account is iCloud. Writability is rechecked at execution;
the display name is never resource identity.
Catalogs over 100 calendars fail with `catalog_too_large`. Calendar/source titles
are normalized to one line, clipped to 512 Unicode scalars and must remain nonempty.
Event titles use the same bounded display projection and may be empty.

Event rows contain:

- `source_id`, `calendar_id`, `event_id` and `occurrence_start`;
- `title`, UTC `start` and `end`, and `all_day`;
- `start_date` and exclusive `end_date` (`YYYY-MM-DD`) for all-day events, otherwise
  both explicitly null;
- `time_zone` (event timezone when present, otherwise the requested timezone);
- `status` (`tentative` when EventKit says tentative, otherwise `confirmed`).

Cancelled occurrences are omitted. EventKit expands recurrence with its own event
predicate API. `occurrence_start` uses the original occurrence date, falling back to
actual start, so moved exceptions remain distinguishable from their scheduled slot.
It is not sufficient to identify a recurring occurrence by event ID alone. A caller
can hash the source/calendar/event/original-occurrence tuple. For all-day identity,
use the original occurrence's local date in `time_zone`; hashing its UTC instant
would change floating-event identity when the request timezone changes.

EventKit returns floating all-day dates in the process default timezone. Before a
read, the one-shot child sets its own Foundation default to the requested timezone;
the OS timezone is never changed. Returned civil dates are then interpreted in the
event timezone when present, otherwise in the requested timezone. Overlap is checked
with `event_start < request_end` and `event_end > request_start`; ongoing events are
included. Timezone/DST/all-day behavior still requires a controlled calendar fixture
and physical permission checks; compilation and status do not establish acceptance.

Rows are sorted by start and deduplicated by their source/calendar/event/original
occurrence tuple. `partial=true` explicitly reports an event-count/output-size limit
or a clipped event title. Notes, locations, attendees, organizers, URLs, contact IDs
and other event bodies are never returned.

Closed read error codes are `invalid_request`, `permission_required`,
`permission_denied`, `permission_restricted`, `permission_request_failed`,
`calendar_unavailable`, `calendar_scope_mismatch`, `catalog_too_large`,
`calendar_data_invalid`, `calendar_read_failed`, `response_too_large`,
`operation_timeout`, and `platform_unavailable`. Error bodies never include native
exceptions or user content. Success exits zero; errors exit nonzero. The permission
completion runs through the main RunLoop. A safe timeout is attempted at 89 seconds;
an independent watchdog terminates the process at 90 seconds even if native I/O hangs.

## Local photograph OCR

`recognize_schedule_image` accepts only the common fields plus `image_path`, an explicitly
selected absolute local file path sent through stdin. It does not inspect Calendar TCC status,
open an event store or request permission. The helper accepts one regular image file of at
most 20 MiB, one frame, at most 60 million pixels and no dimension above 20,000. ImageIO
applies orientation and creates a thumbnail bounded to 4096 pixels before on-device Vision
recognition. No image upload, model call, source file copy or diagnostic image is made.

The successful response adds only `ocr_lines` (objects with `text`, `confidence` and `bounds`) and
`ocr_partial`. Recognition is accurate, with language correction disabled. The requested
Russian/English languages are intersected with locally supported languages; missing requested
support is explicitly partial. The output is at most 200 lines, 512 Unicode scalars per line
and 64 KiB of text, with clipping/limits marked partial. Confidence is retained in [0, 1].
Bounds contain finite `x`, `y`, `width`, `height` in normalized Vision coordinates with
the origin at the **bottom left** of the oriented image. Width/height are positive and the
rectangle must fit within [0, 1] × [0, 1]. These observations preserve geometry for a separately
validated preview parser; the native helper does not infer columns or create shifts from OCR.
This is plain untrusted OCR text, not a calendar plan. It does not interpret table columns,
people, month/year, codes, overnight shifts or missing times. The application must show
ambiguities and require editable human review; it must not route OCR into Core/history/TTS.

Image errors are `image_unavailable`, `image_invalid` and `ocr_failed`, without native text.

## Reviewed schedule creation

`preflight_schedule`, `create_schedule` and `reconcile_schedule` accept exactly the common
fields plus `target`, `rows` and `plan_fingerprint`. This is the internal execution protocol,
not the portable `.satori-schedule.json` file format. The application validates the latter
and binds the immutable rows to its local principal/session/generation and exact target before
issuing one create permit. The helper is not a general executor or a second permit owner.

- `target` has exactly `source_id` and `calendar_id`, both exact bounded opaque identifiers.
- `rows` contains 1–100 objects with exactly `row_id` (nonzero unique UUID), `title` (1–120
  single-line Unicode scalars), explicit-offset ISO `start` and `end`, IANA `time_zone`, and
  a unique 64-character lowercase hexadecimal `owned_token`.
- Dates use whole seconds and contain no fractional seconds. Their explicit UTC offsets must
  match the IANA zone at each instant, including an explicit DST branch. Positive duration is
  at most 36 elapsed hours per row; the complete plan spans at most 62 elapsed days.
- `plan_fingerprint` is 64 lowercase hexadecimal characters. The trusted application validates
  the exact fingerprint and one-use permit again immediately before sending stdin. The helper
  echoes it only with a successful correlated result; it does not inherit a read grant.

The portable content token is independently verified with CryptoKit before account access:
SHA-256 of UTF-8 `satori-shift-v1\n` + start in UTC `YYYY-MM-DDTHH:MM:SSZ` + newline + end
in the same UTC format + newline + NFC-normalized title. Device-local calendar/profile/session
IDs and display names do not enter the token. Matching is always additionally scoped to the
exact selected calendar. New events carry only the opaque marker URL
`satori-calendar://shift/v1/<token>`; the helper never opens that URL.

Full Calendar permission is required for exact catalog/target resolution and duplicate reads.
Target identity, allowed event type and `allowsContentModifications` are rechecked immediately
before creation. Preflight/reconcile issue predicates only for each approved row's interval
and the one exact target; never nil/all calendars. They inspect at most 1000 occurrences per
row and 5000 across the operation, failing closed above those bounds. No event IDs, titles,
notes, locations or attendees are returned.

Each successful schedule response adds only `plan_fingerprint` and `schedule_results`, whose
objects contain `row_id`, `owned_token` and a closed `outcome`: `absent`, `owned_exact`,
`conflict`, `created` or `unknown`. Exact owned matching includes marker, NFC title, UTC dates,
timed/nonrecurring state and absence of alarms/attendees. A different IANA alias or an ICS
import that retains UTC instants does not create a new shift. A changed owned record, duplicate
marker or equivalent title/time under another marker is a conflict. Preflight/reconcile
produce `absent`, `owned_exact` or `conflict`; creation is allowed only if every row remains
absent in an immediate native preflight. Any duplicate/conflict rejects the whole create
before staging. There is no implicit skip, update, deletion or rollback by deleting events.

Creation stages new timed events with `save(..., commit: false)` and then calls `commit()`.
It sets no attendees, alarms, recurrence, notes or location. After commit, a **fresh**
`EKEventStore` must observe every exact owned record before `created` is returned. This is
local EventKit readback, not proof of remote iCloud delivery, physical iPhone observation,
distributed atomicity or exactly-once execution across devices. Concurrent external writers
cannot be locked by this helper.

`calendar_not_writable`, `schedule_conflict` and `schedule_preflight_failed` are closed
pre-execution failures. Staging/commit/readback errors or create timeouts return
`operation_unknown`, with no row payload. The application must also treat a process failure,
lost acknowledgement or cancellation during create as unknown, not as proof of no writes.
Reconciliation is an explicit separate bounded read; absence does not automatically authorize
replay, especially if a record was moved outside its approved interval. A changed plan needs
fresh human review. Raw native errors and all event bodies stay out of responses/diagnostics.

## Verification scope

The historical 2026-10-20 read-only source compiled for arm64/macOS 14 with the installed Xcode
SDK and no compiler warnings. Its sole direct helper invocation was `status`:
exit zero, `not_determined`, 188 stdout bytes, zero stderr bytes, no permission prompt
and no calendar/event read. The private metadata receipt is at
`/private/tmp/satori-assistant-20261020/native-status-result.json`. Request-access,
catalog, selected events, recurrence, all-day/DST, revoke and packaged TCC behavior
are not verified by that status check.

The 2026-10-26 extension is compiled with the actual local Swift compiler. Automated native
fixtures replace only `exit(main())` with an account-free entry point and exercise the real
JSON/scope/token/DST validation, duplicate classifier and one-shot timeout envelope. They do
not invoke `handle`, EventKit authorization, an event store, OCR or account operations. This
is focused engineering evidence. Real supplied-photograph recognition, reviewed creation,
fresh local readback, reconciliation and physical iPhone synchronization are separate gates.
Current bundle/account acceptance is recorded by the coordinating delivery, not inferred from
the historical bundle-6 read acceptance or these fixtures.
