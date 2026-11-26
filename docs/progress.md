# Release status

## Desktop development candidate · 2026-11-26

The work client handles a local busy-call dialogue and stores completed messages in the selected profile. A calls-only chat question can open that inbox. The chat also routes colloquial replies about an interview to the requested inbox and identifies unsupported calendar edits. The installed Satori 26 release is documented below. Carrier calls, delivery of outgoing mail, automated Telegram replies and mobile use with the Mac asleep still require their own end-to-end acceptance. See [the call dialogue](decisions/0116-conversational-busy-call.md) and [private inbox](decisions/0117-private-call-inbox.md).

## Satori 26 · 2026-11-19

This source release derives from internal release 27, the November 19 installed macOS
build. The public tree removes Finder metadata, stray duplicate files and local evaluation
and installation artifacts. It uses a profile-local credential candidate for new desktop
OpenAI setups. Existing explicit credential references still load through profile
configuration.

The installed build's qualification recorded 12,392 passing tests, four optional
Ollama skips and 39 credential-free packaged probes. Its test supervisor needed a
separate identity check for two randomized AudioSocket test IDs before all Foundation
stages were accepted. The installed app started with the existing profile and returned
a live one-day EventKit Calendar answer from the ordinary composer after macOS Calendar
access was restored.

The release adds bounded semantic routing for unmatched read requests on a configured
desktop GPT runtime, a temporary Telegram session capped at 24 hours, recoverable
Telegram view expiry, and a finite compatible pricing-review window.
[ADR-0115](decisions/0115-practical-connected-assistant.md) defines those contracts.

Live Gmail and Telegram answers, a public macOS binary, a physical iPhone, calls, and
operation while the Mac sleeps remain unverified for this release. Character development
is paused and Stage 15 remains closed.
