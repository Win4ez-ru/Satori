# ADR-0095: Chat-first desktop and persistent settings

- Status: Accepted for the bounded presentation change below
- Date: 2026-10-30
- Authority: the user requests a chat with voice as the main screen, with Calendar, Gmail,
  Telegram and other connections under settings
- Supersedes: only ADR-0092's home-first placement and top-level source navigation
- Preserves: existing Core/source/voice owners, permissions, session-only credentials,
  selected reads, privacy lifecycle, manual Send, Character pause and Stage15 closure

## Decision

Open the existing conversation by default. Use a quiet, readable desktop shell with conversation
history in a sidebar, a central transcript and a compact composer containing text, voice and
Send. Keep the actual existing conversation/history/commit protocol. A static empty-state label
is presentation, not a generated response or a canonical interaction. Source data is never
promoted into the transcript, model context, memory or TTS by navigation or styling.

Connections, voice preparation/auditions and service controls live in a persistent settings
workspace in the same window. Reuse each existing panel/controller exactly once. Ordinary
navigation performs no Connect/List/Read, provider call, microphone capture or download. Do not
reparent persistent panels into the disposable profile-edit dialog. Returning from settings
restores the existing conversation and draft without creating a new session.

The existing voice controller presents compact chat controls and separate persistent settings
controls. Push-to-talk remains press/release, local recognition produces an editable draft,
and Send remains explicit. A continuous realtime voice conversation is not introduced here.
Leaving conversation cancels capture/transcription; late results remain fenced by the existing
identity/session/draft checks. Cloud keys remain process-local and protected fields are cleared
on modal completion; the existing profile/settings save path remains the only writer.

Keep Telegram's own viewport fully fitted, without a new outer scroll area that could overstate
visible text. Panel hide/deactivation/conceal, Calendar hidden-refresh handling, Mail cancellation,
profile switching and the root bounded Quit barrier retain their existing owners and semantics.
Settings statuses use content-free validated owner projections, never account/message details.

## Verification

Test default conversation, navigation without source I/O, repeated settings visits without
duplicate/deleted owners, draft/session preservation, navigation cancellation and late ASR,
Telegram hide/viewport, Calendar hidden refresh, profile save/cancel and shutdown. Preserve
plain-text or escaped presentation of canonical text and do not show pending/failed sends as
committed replies. Verify keyboard focus, long content, scrolling and720px geometry with
synthetic data. Root owns a fresh complete noneditable gate, packaged/native checks and a
reversible app-only update. Visual, account and acoustic acceptance remain separate.
