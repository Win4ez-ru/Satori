# ADR-0104: Calendar-aware Core operation and reviewed event creation

- Status: Accepted for isolated implementation and qualification; account effects and deployment remain separate; superseded in part by ADR-0108 only for the conversational exact-command confirmation surface
- Date: 2026-11-03
- Authority: explicit user instruction after the Notion product-fit audit to implement conversation/memory → calendar-context → proposed time → requested event creation, while continuing Telegram and calls in parallel
- Supersedes: ADR-0096 and ADR-0100 only where they forbid the operation-scoped, explicitly approved Calendar disclosure described below
- Preserves: one canonical Core, source exclusion and body-floor admission, Character pause, closed Stage15, existing source/read grants, explicit write authority, old paid-test budget, server deployment and profile-transfer boundaries

## Scope

Implement a bounded one-day scheduling operation and create one ordinary event in one
exact selected calendar. Retain the R6 source and installed Mac16/iPhone3 unchanged while
qualifying the isolated R7 candidate. No guests, invitations, recurrence, editing/deletion,
background observation, automatic source ingestion or autonomous sends/calls are added.
The current request opens this implementation; another architecture approval is not a
prerequisite for ordinary reversible development within this contract.

## One Core, separate source operation

An assistant planning use case reuses the existing Core's self reader, context composer,
request builder, scoped recent conversation, eligible episodic/semantic retrieval,
relationship/affect/position projections and foreground provider. It does not create
another identity, seed, personality store, relationship owner, scheduler or hosted agent.

This is a transient Core operation, not canonical `TalkToSatori.execute`. The actual user
request is distinct from selected calendar observations. Neither observation nor generated
plan is inserted as a canonical USER/assistant pair or sent to post-response learning.
No appraisal, relationship or personality mutation is derived from calendar contents.
Existing owners may initialize/read their normal projections; source-dispatch admission
may persist content-free dependency metadata. Normal canonical Send remains unchanged.

Calendar source data uses a separate explicitly labelled low-trust provider data envelope,
never a privileged instruction. A provider's user/data channel does not make that envelope
a canonical user statement or evidence. The final request message remains the real user's
request. Policy explains the provenance and denies instructions embedded in event titles.
No raw reasoning is requested or retained.

The existing deterministic cognition/Character kernel may prepare request-local V35 builder
inputs for the actual user operation, using `calendar-input:<request_id>` and
`calendar-operation:<request_id>` as ephemeral string references. These are not
CanonicalRootRef values, database messages, shared-past evidence or learning roots; they
never enter canonical history, source-policy root registration or post-response work. Only
already eligible canonical history/memory supplies past grounding. Calendar observations
remain outside this cognition intake, and affect is an existing read-only SKIPPED projection
without a new appraisal/model call or state mutation. No Character policy/version changes.

The first disclosed fields are bounded event references, title,
start/end/status, observation time/window and timezone. No attendees, notes, email, Telegram,
contacts, native calendar/account IDs or arbitrary file content are included. Complete selected coverage is required
to propose gaps; every non-cancelled event, including tentative/unknown/all-day intervals,
blocks time. Results describe gaps in selected calendars, not universal personal availability.

## Consent and provider admission

Reading, disclosing to the configured provider and writing are three independent permissions.
The host obtains a real current selected-source read grant. Explicit disclosure mints a
short-lived one-use permit bound to identity/profile/session/generation, exact request,
snapshot digest and provider/model. Revoke, expiry, changed scope or changed source blocks
use. A dedicated operation dispatch guard composes with existing source-policy admission;
nested retrieval and actual transport retry/queue dispatch must retain both guards.

All paid attempts continue through the existing provider budget owner. No new model route,
SDK, built-in paid tool, hidden fallback or automatic retry is introduced. This ADR does not
renew the old 20 RUB test grant or its expired tariff window. Live qualification requires
a concrete current-price bounded allowance; offline qualification uses explicit fake transports.

## Planning and action authority

UTC interval arithmetic and free-slot enumeration are deterministic. The model may select
only an enumerated slot ID, clarify or decline. Its bounded JSON also names only supplied
memory/event references. It cannot invent a timestamp, alter the user-authored event title,
grant permission, call a provider or claim creation. Invalid output produces a typed failure,
not another generation or a guessed action.

The action owner receives the user-authored title, exact UTC interval, IANA timezone,
request ID and bound planning/context digest. A durable private journal outside canonical
personality/memory holds the proposed intent and effect lifecycle. Preview includes exact
calendar/account/title/timezone/time; explicit reviewed approval binds this full payload.
Read or disclosure grants never mint this approval.

Before effect dispatch, revalidate current scope/catalog/grants and the selected source
snapshot; changed facts require a new plan/review. The owner durably claims one attempt
before invoking a provider and supplies a one-use last-moment effect guard. Unknown outcomes
are terminal for dispatch and use read-only reconciliation; absence never silently rearms
creation. Restart cannot revive old approvals. A provider token is a reconciliation marker,
not a claim of remote exactly-once or atomicity with other Calendar clients.

An EventKit adapter is a separate create-only helper, not an expansion of reviewed shift-import
authority. It checks full access, exact writable non-subscribed event calendar and fresh
overlap, uses READY/COMMIT admission, creates no invitations and verifies a fresh exact
token/field readback. Other-client races are reported as a limit, not ruled out by SQLite.
On Linux or without a qualified helper the EventKit adapter is unavailable. An independent
server/iCloud adapter is a separate implementation/qualification, not implied Mac access.

## Backend integration and acceptance

Default-off bindings expose prepare/disclose/plan/review/create/reconcile in the existing
owned backend lane, using its actual Core/session. No public listener, phone protocol change,
credential enrollment, account connection or personal-profile transfer follows automatically.

Required checks include actual Core/retrieval/history composition with public fixtures,
choice affected by eligible memory/recent dialogue and selected observations, full restart
without fabricated recall, no source in canonical learning, no private-content logs,
partial/expired/cross-scope rejection, revoked queued/retried provider dispatch, invalid model
JSON/references, changed snapshot before effect, exact approval, concurrent request binding,
crash/unknown/reconcile without duplicate effects and strict provider receipts. Real provider
quality, Calendar account writes/iCloud delivery and physical iPhone remain distinct gates.

The complete Foundation starts with the required frozen noneditable rebuild. Pure/fake-provider
tests establish wiring and deterministic safety only. They do not establish semantic model
quality, acoustic Character expression or successful real account operations.
