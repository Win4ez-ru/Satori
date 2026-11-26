# ADR-0117 — Private call inbox for the local Desktop profile

Accepted on 2026-11-24. Extends ADR-0116 for the owner's explicit request to keep
completed messages in Satori across application restarts.

The opt-in busy-call handler may publish its owner callback review after orderly
AudioSocket closure and independent confirmed termination. Publication resolves the
review under its current grant and exact principal. The inbox stores the selected
caller's words, UTC review time, profile and opaque call, grant and receipt identifiers.
The durable owner is the local OS user and selected profile. An admitted entry remains
readable after the intake session expires or the Desktop process exits.

The profile's `call-inbox` directory has mode 0700. Each 0600 record is published
atomically under a key derived from the provider account, route and call ID. A replay
preserves the first entry and timestamp. Conflicting text for that call is rejected.
Separate calls with identical text retain separate entries. Desktop displays the
untrusted text in the owner's “Сводки звонков” dialog. Reading requires no provider,
model, chat session or external delivery. The local adapter remains opt-in.

From the conversation, a calls-only question without a carrier history connection offers
the same profile's inbox. Only the owner clicking the button opens the dialog. The card
contains no caller content and makes no claim about missed calls or a requested time range.
The inbox and its records stay outside provider context and conversation history.
