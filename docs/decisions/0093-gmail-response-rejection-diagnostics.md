# ADR-0093: Closed Gmail response-rejection diagnostics

- Status: Accepted for bounded diagnostic implementation; actual inbox repair remains unverified
- Date: 2026-10-29
- Authority: the user's continued installed Calendar/Gmail/Telegram account checks and actual
  Gmail inbox rejection after completing login
- Extends: ADR-0086/0087/0092 only with transient closed response-rejection diagnostics
- Preserves: response admission, all bounds, source owners, OAuth scopes, selected reads,
  session-only credentials, Core isolation, Character pause and Stage 15 closure

## Evidence and problem

The user's screenshot of ordinary installed bundle 12 shows an active Gmail session, an
explicit inbox-list action and a received Google response. The list fails with
`response_invalid` and displays zero headers. This establishes a failed list, not a failed
login or an empty INBOX. The screenshot and account identifiers are not copied into reports.

The same broad code covers HTTPS framing, JSON parsing, list structure and metadata-header
validation. The exact failing branch and source value are unknown. Synthetic reproductions
of a possible rejection are not evidence that it caused this account's failure. Do not raise
limits, suppress malformed fields or skip messages merely to turn the list into a success.

## Decision

Add a separate typed, finite response diagnostic vocabulary. A diagnostic belongs only to
the existing `response_invalid` error code. Carry it transiently through the existing Gmail
worker, action receipt and local UI. Revalidate its membership and error-code association at
each boundary; forged, unknown and mismatched details are discarded or rejected according to
the existing boundary contract. Broad exception strings and audit codes remain unchanged.
Receipt validation checks both optional diagnostic fields without invoking equality methods
on supplied values. Only exact strings from the matching finite vocabulary are admissible.

Distinguish transport framing/read shape, JSON syntax/structure, list rows/identifiers/cursor,
metadata payload shape and metadata-header shape/name/value-limit/controls/decoding failures.
Use only literal categories and static UI explanations. No account/address, resource ID,
header name/value, message ordinal, actual size/character, request or callback URL, token,
exception payload or traceback is included. Preserve denial and exact original limits.

Clear transient diagnostics on new actions, success, cancellation, profile/generation change,
disconnect and shutdown, retaining only the existing explicitly historical action receipt
where its lifecycle already permits. Late or cross-generation results cannot populate them.
No automatic login, list/read, retry, credential refresh or account discovery is introduced.

This change does not alter MIME projection admission or its version, request fields,
transport endpoints, TLS validation, account binding, selected resources, deadlines, batch
atomicity or any durable store. Source data still has no canonical USER/history, Core/model,
memory, self/relationship or TTS path. Existing Telegram and Calendar behavior is unchanged.

## Verification and acceptance

Use synthetic response failures to cover the closed categories, unchanged broad error/denial,
malicious or mismatched diagnostic values, worker/controller/receipt/UI propagation and
clearing, cancellation/generation races and zero source values in displayed diagnostics.
Include a valid small metadata response and bounded-limit counterexamples without treating
them as a reconstruction of the user's actual mail. Preserve existing HTTP and mail tests.

Run a fresh noneditable Foundation gate with exact complete collection coverage, current
source parity, packaged runtime/resource checks and ordinary native lifecycle verification.
Root owns the qualified reversible app-only update; retain installed bundle 12 and earlier
backups. Actual Gmail acceptance requires a new user-driven list in the resulting installed
app. A disclosed closed reason identifies the failing check, not automatic repair. If the
list still fails, use that evidence for a separate minimal correction and repeat relevant
qualification; never claim inbox repair from synthetic tests or diagnostic availability.

## Primary references checked 2026-10-29

- [Gmail messages.list](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list)
- [Gmail messages.get and metadata headers](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/get)
- [Google Workspace header limits](https://knowledge.workspace.google.com/admin/gmail/gmail-message-header-limits)

Provider documentation defines supported response shapes and provider-side message limits;
it does not identify the user's rejected field or replace Satori's stricter local bounds.
