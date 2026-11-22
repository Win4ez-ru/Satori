# ADR-0083: Transient calendar cards in the conversation view

- Status: Accepted for bounded implementation; real Calendar acceptance remains separate
- Date: 2026-10-20
- Authority: the user's explicit request to coordinate the five chats and integrate ready changes
- Supersedes: ADR-0082's My-day-only presentation restriction for this local card only
- Preserves: existing Core, prompts, canonical admission, state owners, Character pause and all
  ADR-0082 source, consent, bounds, expiry and native-read invariants

## Decision

Add an explicitly invoked Calendar action in the conversation view. It presents an exact
agenda or interval-without-events query, date, IANA timezone, selected calendars and window
before execution. Existing My day connection/selection remains the source/read-grant owner.
An existing read grant alone does not authorize presentation in conversation: the action
issues a bounded one-use local-display permit tied to the principal, chat/session, proposal,
resources, grant generation and request. A fresh read produces one correlated receipt.

Render the answer in a separate transient plain-text card. Do not append a fabricated USER
or ASSISTANT interaction, invoke canonical Send, call an LLM, persist a source-derived answer,
run post-response work, or emit TTS. Normal conversation and editable ASR drafts retain their
existing admission paths. The first UI uses explicit Calendar controls; no broad natural-language
tool resolver, provider projection or general executor is needed.

The Calendar read coordinator remains the sole owner of reads, grants and snapshots. The new
dialogue session owns only its one-use display permit, receipt and temporary answer. A shared
controller dispatches an admitted native read into exactly one result-admission path. Revoke,
selection edits, profile/chat changes, expiry, cancellation and newer requests fence late results
and clear the card. A callback must never overwrite an edited normal conversation draft.

## Answer semantics

Calculate overlap, interval union and complement deterministically in UTC, with valid IANA civil
day boundaries. Timed, tentative, cancelled and exclusive-end all-day occurrences retain the
native contracts. Bound the query to one day, existing 100-event/512-KiB limits and a visible
minimum interval. Preserve DST and ambiguous/nonexistent-boundary failures explicitly.

Call derived gaps only **intervals without events in the selected calendars**. Calendar records
do not prove actual personal availability. Show exact window, timezone, source coverage and local
observation/expiry time. A complete empty result permits only that limited absence statement.
Partial results may show an incomplete agenda but never assert positive gaps. Denied, error,
unavailable, stale and cancelled do not become empty. Local EventKit freshness is not remote
account synchronization acceptance.

## Verification

Review and adapt the isolated proposal from the calendar-dialogue chat, omitting its unused
future-provider projection. Test exact principal/chat/turn/window/resource correlation, single
admission/replay, expiry/revoke/newer-work fences, interval/DST semantics, partial and failed
results, plain-text malicious titles and zero Core/provider/persistence/TTS calls. Verify the
actual DesktopWindow wiring and profile/chat/draft lifecycle. Run one coordinated fresh
noneditable Foundation/build/bundle gate after all integration changes are frozen.

Real selected-calendar behavior, change/refresh, revoke and native data edge cases still need
user-driven OS consent and controlled account acceptance. This decision does not authorize
mail/message reads, calendar writes, cloud source disclosure, Character changes or Stage 15.
