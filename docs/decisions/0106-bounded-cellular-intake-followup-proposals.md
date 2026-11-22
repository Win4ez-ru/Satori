# ADR-0106: Bounded cellular intake and owner-review follow-up proposals

- Status: **DRAFT — isolated no-effect implementation only; not a deployed call contract**
- Date: 2026-11-03
- Authority: the explicit parallel cellular-backend preparation task; no real call, account operation, paid request or source-policy expansion is authorized
- Preserves: PROJECT_SATORI, ADR-0082/0096/0100, Character pause, closed Stage 15, external-source exclusion and one-owner state rules

## Decision proposed for integration review

Introduce one small ephemeral `CallIntakeSession` contract. It consumes strictly typed, already
normalized assertions from a future qualified call adapter and returns one transient
`CallFollowupProposal` for the owner to review. It is not `AssistantIntent`, canonical `USER`,
long-term memory, self/relationship evidence, provider context or effect authority.

There is no default authority adapter. Every admission and content resolution rechecks the
exact local principal/profile/application session, route/account/call/leg/generation, grant and
UTC expiry through an injected source-owner port. Constructible DTOs and the injected port
are application seams, not authentication against arbitrary code in the same process.
Actual provider signature/transport authentication and participant-consent provenance are
unimplemented. Public synthetic fixtures may exercise the seam; they do not qualify a live route.

The contract reuses `LocalPrincipal` and UTC validation from the existing assistant boundary.
It does not duplicate Core, personality, a general action executor, transport/media, a provider
SDK, speech recognition, a model router, calendar write owner or message sender. Historical
call reducers were documented under `/private/tmp`, but their source files are absent now;
this candidate neither reconstructs nor claims to rerun those old transport packages.

## Bounded lifecycle and privacy

One instance represents one attempted intake. The injected owner must independently qualify
exclusive route/attempt ownership before real use; this scaffold is not a process-wide lease
registry. A grant lasts at most 120 seconds; there are at most eight final segments, 500 characters
per segment and 8192 UTF-8 bytes in total. Text is externally attributed and never interpreted as
commands. Consent must match the exact grant, participant and notice digest. Silence and an ASR
guess are not consent. The normalized assertion does not prove that a real person heard a notice.

Only a correlated, temporally coherent `confirmed_ended` assertion admits proposal construction.
An unknown end clears intake and cannot produce a proposal; later terminal reconciliation cannot
resurrect discarded text. Stop clears local references and does not claim or send a remote hangup.
Revoke, clock rollback, exact expiry, authority failure, byte overflow and conflicting segment
payloads stop intake and clear content. Restart recovers nothing and performs no automatic action.

The owner explicitly selects existing segment IDs and a finite review kind (callback review or
calendar review). One request ID binds one selection; replay is idempotent and changed payloads
reject. The result contains untrusted fragments and explicit false flags for execution, canonical
admission, learning, provider disclosure and TTS. Any later calendar/message action requires a new
owner-authored proposal, its own exact grant/policy and confirmation where required. A caller,
matched Caller ID, contact-style hint or intake grant cannot approve it.

Content resolution must use the session again at display time; a consumer must not cache the
returned body or display it after revoke. Reference clearing is logical revocation, not forensic
RAM erasure. Diagnostics contain finite codes, phase/counts and zero executed effects; model repr
excludes content. No transcript, key, caller number or audio is persisted here.

## Not opened and acceptance still required

No number is purchased, carrier forwarding changed, socket opened, call answered, media
processed, contact read, consent notice spoken, API key read or paid API request dispatched.
The candidate supplies no usable SIP/RTP/Vox/WebRTC bridge, native UI, runtime integration,
telephony retention policy, durable effect journal or cross-border service permission.

Focused local synthetic tests, Ruff and mypy are component evidence. Integration into R7 needs
review of exactly the new modules/tests and a fresh complete Foundation run. A real provider
route additionally needs written individual/territory/route acceptance, exact tariff/CLI checks,
qualified authenticated source adapter and one-owner lifecycle, participant notice/consent,
duplex/termination/failure acceptance, disclosure authority and a new bounded route/budget grant.
Calls stay visibly unavailable until those gates pass. Calendar/iPhone work may proceed separately.
