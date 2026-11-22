# ADR-0113: Conversational sender lookup and source refinements

- Status: Accepted for the current correctness repair and qualification
- Date: 2026-11-11
- Supersedes: ADR-0112 only for deterministic sender questions and explicit local-day windows
- Preserves: ADR-0109 exact mailbox commands, ADR-0110 request leases and source exclusion, and ADR-0112 literal provenance for contextual search

## Evidence

In the installed App24, a natural question about whether a named person wrote today fell into
ordinary conversation. A source-first Telegram refinement also missed intake. Ordinary chat
then guessed at account availability. An employer clarification had no task to resume. The
ordinary composer also intercepted exact Gmail commands before the deterministic mailbox route.

## Decision

Recognize ordinary incoming-message questions and source-first requests at the composer boundary.
Keep a source-only follow-up on the existing foreground task, with the original expiry and an
explicit subset of its sources. A substantive clarification remains owner-authored context for
the same bounded search. Preserve exact mailbox commands and ordinary unrelated conversation.

For an explicit named-sender question, derive the literal from the owner's words. Compile a Gmail
sender query or a Telegram peer query, as appropriate to the task. A question about mentions of a
person still searches message text. Contextual questions continue to require a grounded literal.
Host time arithmetic narrows explicit «сегодня» and «вчера» to the owner's local calendar day,
subject to the current instant and the existing maximum window.

Telegram peer lookup uses the connected TDLib owner. Resolve one eligible ordinary private
peer from a bounded candidate set, then read one bounded history batch. Admit only incoming
messages from that peer within the requested interval. Do not open or mark a chat read. Report
ambiguous names and incomplete history as limited coverage. Account/session changes still revoke
the task. The model receives only bounded observations under the original disclosure lease.

## Qualification

Reproduce the natural question, Telegram narrowing and employer clarification with synthetic
records through the ordinary composer. Preserve exact Gmail routing. Exercise exact local-day
edges, same-name ambiguity, incomplete Telegram coverage, incoming/outgoing filtering and
revocation without using a personal account. Separate signed bundle/native startup evidence
from subsequent live account acceptance.
