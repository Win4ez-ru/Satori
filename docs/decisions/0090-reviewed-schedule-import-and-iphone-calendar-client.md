# ADR-0090: Reviewed schedule import and an iPhone calendar client

- Status: Accepted for the bounded implementation below; account/device acceptance remains separate
- Date: 2026-10-26
- Authority: explicit request to prioritize work-schedule photographs, functioning calendar synchronization and an iPhone application, while continuing Telegram/mail/calls in parallel
- Supersedes: ADR-0082/0083's read-only delivery restriction only for reviewed creation of work-shift events; ADR-0079's mobile scheduling prerequisite only for this calendar client
- Preserves: Character pause, Stage 15 closure, canonical admission, Core/providers/state owners, source provenance and all unrelated action permissions
- Related: ADR-0046, ADR-0079, ADR-0082, ADR-0083

## Delivery

Deliver one useful scenario in the main macOS application and a small native iPhone client:
select a schedule photograph, recognize text locally, review and correct dated shifts, choose
an exact writable calendar, and explicitly create the approved events. The user confirms the
same iCloud calendars on both devices and selects the existing calendar named «Работа».
That name expresses user intent; it is not resource identity and does not resolve ambiguous
accounts/calendars. The client selects a current catalog entry by its actual source/calendar ID.

The iPhone application is a calendar client of Satori. It contains no second personality,
conversation owner, learning pipeline or copied Core state. A local workflow requires no server,
Mac endpoint, cloud OCR or model-provider credentials. Platform implementations share the
reviewed plan semantics and fixtures; they may validate independently in Python and Swift.
A reviewed JSON import/export and an ICS export provide useful interchange when the photograph's
layout is not yet supported. They do not substitute for actual EventKit/device acceptance.

Portable interchange v1 is specified by
[`schedule-import-v1.schema.json`](../contracts/schedule-import-v1.schema.json) and the
[synthetic interoperability examples](../contracts/schedule-import-v1.examples.json).
It carries schema version, IANA timezone and 1–100 title/start/end rows, with explicit
second-resolution ISO-8601 offsets, shifts of at most 36 hours and a plan span of at most
62 days. Runtime validators additionally enforce actual dates, zone/offset coherence and
duplicate rejection. Bool is not schema integer 1. There are no device target IDs in this file.

## Photograph and review

Only explicitly selected local images enter on-device Apple Vision OCR. Do not upload images,
OCR text or calendar contents to a model or another service. Bound file size, OCR output,
recognized rows and event intervals; treat every recognized string as untrusted plain data.
Raw photographs/OCR are transient and are not saved to canonical chat, memory, diagnostics,
source fixtures or the repository. No source content becomes self/relationship evidence.

The editable preview shows title, every date, start/end time, explicit next-day end when used,
IANA timezone/UTC offsets, target calendar and unresolved fields. Missing month/year, unknown
shift codes, multiple people/rows, uncertain OCR and DST ambiguity require correction; do not
invent a date, shift, person or timezone. Night shifts cross midnight only when explicitly
represented in the reviewed plan. Reject impossible dates, zero/negative/overlong shifts,
duplicated rows, unresolved fields and excessive plans before any write.

## Narrow action owner

Use a separate calendar-import coordinator as sole owner of the ephemeral draft, preview
revision, confirmation and pending execution. Reuse the existing native EventKit adapter and
owned-process boundary on macOS. Do not add a general executor or route imports through ordinary
conversation Send. EventKit, rather than the Satori canonical database, owns the external events.

A one-use create permit binds principal/application session, current generation, exact target
source/calendar identity, normalized immutable rows, bounds and preview digest. Editing a row,
changing target/profile, disconnecting or expiry invalidates the permit. Recheck permission,
identity and writability immediately before execution. The final «Добавить N смен» action is
explicit and reviewable. Do not accept OS privacy dialogs automatically.

Create only the approved shift events. No deletion, modification of existing events,
recurrences, attendees, invitations, alarms, contact lookup or message sending belongs to this
slice. New permissions do not derive from the old read grant or relationship trust. Do not
create a new calendar: the user chose the existing «Работа» calendar.

## Repeats, uncertain outcomes and receipts

Preflight a bounded interval of the exact target calendar and show duplicates/conflicts.
Use a stable, opaque row fingerprint from normalized event content, scoped by the selected
calendar when matching; device-local calendar IDs must not be assumed portable across devices.
The cross-client fingerprint is lowercase SHA-256 of UTF-8
`satori-shift-v1\n<UTC-start>\n<UTC-end>\n<NFC-title>` without a trailing newline, where each
UTC instant is `YYYY-MM-DDTHH:MM:SSZ`. Titles contain no control/newline characters. The proposed
owned marker is `satori-calendar://shift/v1/<fingerprint>` and is never opened automatically.
An owned import marker and a minimal local receipt journal can support reconciliation without
storing photographs or OCR text. This journal is a calendar-operation owner, not canonical
Satori memory. Keep only operation/row fingerprints, target identity, event references and
closed outcomes needed to avoid repeated creation.

Calendar identifiers are local to a device and can change after a full synchronization.
Bind the current catalog separately on Mac and iPhone; portable plans carry no target IDs.
Local preflight/journaling does not provide distributed exactly-once creation between two
offline clients. The first real batch has one creating device; the other verifies delivery.

Do not silently overwrite an existing or edited event. Distinguish created, already present,
conflict, rejected, failed and unknown. A timeout/crash/failed acknowledgement does not prove
that nothing was written. Reconcile against the exact target and approved rows before any
manual retry; no automatic replay or rollback by deleting events. Batch EventKit operations
are not advertised as a distributed transaction or proof of remote iCloud delivery.
Verify local readback after commit through a fresh event store; a fetch from the same store
can include saved but uncommitted objects and is not proof of persistence.

## Calendar freshness and synchronization

iCloud remains the system's synchronization provider; no second calendar server is introduced.
Show OS permission, exact selected resources, last successful local read, snapshot age and
in-progress/error/disconnected status separately. A saved authorization is not a current read.
A successful EventKit save means local acceptance, not delivery to another device.

A visible, cancellable foreground auto-refresh mode may reread the already selected calendars
within the current scoped session grant, at a bounded interval of at least 60 seconds with at
most one outstanding request. It stops on hide/disconnect/profile change/expiry and never
extends a grant. It does not perform background ingestion or source-to-Core disclosure.
Event-store changes may invalidate snapshots; late callbacks cannot restore revoked content.

The real synchronization gate creates approved shifts on one device and verifies the same
records in the other device's Calendar/Satori view. Do not label remote synchronization verified
from a local cache, compilation, fixture, receipt marker or elapsed time.

## Platform permission and release

EventKit write-only access does not expose the real calendar catalog or existing events.
Exact target selection plus duplicate/reconciliation reads therefore requires explicit OS full
calendar access. Explain that the OS permission is broad while Satori's operations are limited
to the selected calendar and reviewed creation. Existing macOS TCC acceptance is not iPhone
permission or acceptance of a newly signed bundle.

Build and test iOS with the installed official Xcode/SDK and simulator when available. Device
installation requires legitimate signing/device authorization; do not create an Apple account,
accept developer agreements, enroll/pay or change signing credentials on the user's behalf.
A simulator build, unsigned archive and physical iPhone installation are separate outcomes.

## Verification

Test reviewed-plan validation, OCR/parser uncertainty, month/year and overnight/DST cases,
exact calendar scope, one-use confirmation/replay, duplicates, partial/unknown outcomes,
reconciliation, source-free diagnostics and zero Core/provider/TTS calls. Verify actual main
DesktopWindow wiring and bundled helper resources. Run one coordinated Foundation toolchain
with a fresh noneditable rebuild after the desktop changes are frozen, followed by bundle checks.

Run iOS domain/parser tests and an actual simulator build/UI scenario where available. Test
real photographs only after the user provides them; keep their contents out of reports/tests.
Real calendar writes need the concrete reviewed preview and user action. Confirm iCloud delivery
on the second device and distinguish it from simulator and local EventKit evidence.

## Primary references

- [Apple: Accessing the event store](https://developer.apple.com/documentation/eventkit/accessing-the-event-store)
- [Apple: EventKit access changes](https://developer.apple.com/documentation/technotes/tn3153-adopting-api-changes-for-eventkit-in-ios-macos-and-watchos)
- [Apple: Recognizing text in images](https://developer.apple.com/documentation/vision/recognizing-text-in-images)
- [Apple: Set up iCloud Calendar on all devices](https://support.apple.com/guide/icloud/set-up-calendar-mme4d73a8727/icloud)
