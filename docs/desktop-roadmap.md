# Desktop and connected sources

The macOS desktop client owns its UI and settings. An isolated Core worker owns
conversation and persistent state. Tool requests and source results travel through
typed surfaces with finite grants, deadlines and independent approval for effects.

- **Calendar** uses EventKit helpers on macOS. A scoped live read from ordinary chat
  passed in release 26. Portable Calendar support has separate qualification gates.
- **Gmail** uses explicit OAuth consent and scoped read grants. Live answers in the
  release 26 composer remain unverified.
- **Telegram** uses a TDLib worker. Temporary sessions and source views have independent
  deadlines. Live release 26 account acceptance remains open.
- **Local sources** include reviewed schedule imports, public web and selected files.
  Optional voice and map features depend on the corresponding local runtime and
  native helpers.

The GitHub release is source-only. Native helper builds and distribution signing need
their own environment. For fresh desktop OpenAI setup, a profile-local `.env` can provide
`SATORI_OPENAI_API_KEY`. The file must be owned by the current user with mode `0600`.
An explicit review selects the provider and budget before inference becomes available.

See [release status](progress.md), [desktop delivery](decisions/0079-desktop-first-companion-delivery.md)
and [connected assistant lifecycle](decisions/0115-practical-connected-assistant.md).
