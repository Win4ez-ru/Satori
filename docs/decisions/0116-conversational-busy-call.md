# ADR-0116 — Conversational busy-call messages

Accepted 2026-11-24 for the local busy-call handler. This supersedes the keypad and fixed
two-answer sequence in ADR-0106's busy-call scenario at the owner's request.

An owner-selected `BusyMessageGrant` identifies one participant leg and binds the exact
invitation to the source grant. Satori introduces herself as Kirill's assistant, says he
is busy and asks what to pass on. After the adapter finishes speaking the invitation,
the session may process the caller's reply. The application records the opening as a
spoken invitation. Existing explicit-consent intake keeps its own admission path.

The media sink must have drained at least one new frame containing a nonzero PCM
sample during the greeting. An empty or digitally silent greeting stops the session
before recognizing caller audio. This checks local output only; the call transport
cannot attest to what the person heard.

A complete message goes directly to acknowledgment. A greeting, isolated name or unclear
answer can prompt one clarification. The caller can leave a message without giving a name.
The private review quotes the caller's attributed words and delivery requires both orderly
media closure and independent end confirmation. Silence, an abandoned introduction, refusal
or transport loss produces no delivery. This mode admits only a callback review under the
existing expiry, revocation and owner-read checks. Deployment remains disabled.
