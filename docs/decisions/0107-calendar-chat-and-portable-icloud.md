# ADR-0107: Calendar actions in ordinary chat and portable iCloud transport

- Status: Accepted for the explicitly requested isolated R8 implementation and qualification; superseded in part by ADR-0108 only for the conversational exact-command confirmation surface
- Date: 2026-11-03
- Authority: the user requests ordinary-chat Calendar creation, parallel Telegram and operation without an awake Mac
- Extends: ADR-0104 Calendar operations and ADR-0105 separately versioned reviewed Telegram reply
- Preserves: one Core/self/memory, sole read-grant owners, typed effects, independent source disclosure/write approval, budget, Character pause and closed Stage15

## Calendar conversation surface

Recognized Calendar requests in the ordinary composer open a transient task card in the
conversation. Ambiguity requests clarification; it does not fall through to a model that
could claim an unperformed action. Selected source contents, plans and action receipts do
not become canonical USER messages, self/relationship evidence or TTS text. Ordinary
conversational Send keeps its existing canonical owner.

A request for an exact user-authored interval is deterministic: obtain a fresh complete
selected-calendar observation, verify the interval is available, show exact calendar/title/
timezone/time and require explicit creation approval. It does not need a model generation
or provider disclosure. It cannot silently convert a wider search window into an exact
interval or bypass policy. Memory-aware slot selection still uses ADR-0104 disclosure and
the same Core/provider; the new exact path is not a fallback generation or fake model receipt.

## One read owner across the desktop process boundary

### Natural request clarification — 2026-11-04

Direct scheduling instructions can follow a descriptive clause; a recognized incomplete task
must still remain in the Calendar card. Explicit qualified evening times may be normalized
deterministically. Missing duration or ambiguous/unsupported fields never imply an effect or
ordinary model Send. RAM-only editable fields preserve the original user text and require an
explicit Apply. Unsupported semantics remain blocked across no-op/partial edits; replacing them
with one ordinary event requires a separate explicit authoring choice, without granting read,
disclosure or write authority. A requested calendar name resolves only to a unique already
granted writable descriptor. Missing or duplicate matches require an explicit eligible choice,
without a fallback to another calendar. Exact review and all source/effect boundaries below
remain unchanged. This is bounded intake repair, not general natural-language understanding.

The Qt CalendarAccessCoordinator remains the sole read-grant and native-snapshot owner.
The existing Core stays in its owned JSONL worker process. A finite interleaved read RPC
lets that worker request a bounded observation while Qt prepares and admits the actual
read through its existing coordinator. No second coordinator is constructed in the worker,
and a cached observation cannot be called a fresh read.

Qt may export an immutable, short-lived metadata-only dispatch lease to its private profile
directory. The lease binds the original grant/resources, actual Core identity/session,
profile/generation, exact request, selected write calendar, original catalog revision/time,
wall/monotonic deadlines and GUI PID. It contains no event bodies, credentials or user text.
Qt alone creates/removes it; the worker consumes it without minting permission. Its private
regular file must have exact bytes, ownership, modes, link/path identity and active parent
process. Each source/model/effect/publication boundary rereads and validates it. Original
deadlines never extend. Hide/profile/session/grant changes revoke it; failure to establish
revocation retires the consuming worker. This lease is not another persistent grant owner,
write approval, cloud credential or a lease suitable for an unattended server.

The new worker ingress routes only correlated Calendar host results while a foreground
operation waits. Ordinary commands remain in a bounded serial queue. It uses cancellable
pipe readiness, not an abandoned blocking stdin thread, so shutdown can drain with the
input pipe still open. No arbitrary host RPC or public endpoint is added.

The create-only EventKit helper is separately packaged and supplied by the trusted launcher,
never an untrusted command path. PyInstaller may alias the advertised Resources directory to
Frameworks. The trusted launcher resolves only these two fixed internal placements from the
actual packaged executable; aliases outside the bundle or to another internal path are rejected.
The infrastructure port keeps its strict canonical/owner/mode guard unchanged. Artifact acceptance
must verify the actual launcher-resolved path through that guard, beyond a parser-only probe.
Real writes remain impossible without the current exact
review approval and last-moment native guard. UNKNOWN outcomes never resend automatically.

## Portable iCloud source/effect ports

A separate CalDAV adapter implements explicit discovery, bounded selected reads and the
existing create/lookup ports over authenticated HTTPS to validated iCloud endpoints. It
requires separately supplied application credentials; the Codex iCloud connector does not
export runtime authentication to Satori. No account connection or credential transfer is
implied by this implementation.

CalendarSourceRef gains the explicit caldav provider; its default remains macos_eventkit.
Scope provider and every selected resource must agree. CalendarAccessCoordinator remains
read-grant owner on the host that composes the server adapter. Unsupported recurrence,
timezone or incomplete projection remains partial/unsupported_projection, never empty/free.

Creation uses a stable opaque operation UID/resource, conditional PUT, exact readback and
read-only reconciliation. There is no PUT replay after uncertainty. A separate repeatable
current read/source guard is mandatory for preflight/readback; the one-use effect callback
cannot authorize prior reads. Network-thread callbacks are marshalled to the owning event
loop before accessing action SQLite state. DNS/TLS/HTTP/XML/ICS parsing and byte/time bounds
are deterministic. Only successful real-account qualification can establish Apple behavior,
permissions, normalization and iCloud delivery.

## Parallel Telegram

The new reviewed-reply-v1 entrypoint subclasses the pinned legacy receive engine, preserving
its original readonly worker/schema/policy bytes and one native receiver. Native handles and
reply IDs stay in that owner. The root may opt into the versioned capability after software
verification; it does not issue a Send grant. The UI requires selected target/text preview,
new exact approval, one durable attempt and explicit reconciliation. Pending/terminal updates
alone are not SENT. No model drafting, automatic replies, Stars, groups, broadcast or live
message is admitted by qualification.

## Delivery and checks

Implement and qualify R8 in isolation from frozen R7. Required checks include normal composer
routing and display, exact/no-model creation, source RPC with genuine coordinator admission,
private lease revoke/rollback/PID/path failures, queued/post-await guards, EOF/shutdown without
an orphan stdin reader, unknown/no-replay, unchanged canonical state, actual packaged helper
and resource parity, CalDAV wire/recurrence/conditional-write/thread-affinity negatives and
versioned Telegram frames/native mappings/UI scope invalidation.

Run fresh complete Foundation starting with the frozen all-groups noneditable rebuild, then
package/startup verification. Public synthetic proofs are distinct from live GPT semantics,
real EventKit/Telegram effects, Apple account enrollment, Linux deployment and physical iPhone.
No current paid-test allowance, tariff, profile upload, public listener, hosting purchase or
remote persistent service is authorized automatically by this contract.
