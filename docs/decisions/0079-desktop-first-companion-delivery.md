# ADR-0079: Desktop-first Companion Delivery

- Status: Accepted for the bounded D1 implementation and delivery-order revision below
- Date: 2026-10-16
- Authority: explicit user request to pause Character improvement, audit the existing project,
  restore its complete vision, and implement the first desktop milestone without another
  architectural approval when ordinary technical choices are sufficient
- Supersedes: the scheduling assumptions of ADR-0046 and ADR-0053 only where they would require
  Character acceptance, autobiographical narrative, proactivity, or later embodiment before the
  bounded desktop, user-initiated voice, and safe agent-foundation slices described here
- Preserves: all existing source/evidence, owner, permission, privacy, recovery, provider and
  action-safety contracts; historical Stage identifiers and accepted/rejected behavioral verdicts
- Related: ADR-0001–0008, ADR-0012–0014, ADR-0022–0027, ADR-0046, ADR-0053, ADR-0055,
  ADR-0057, ADR-0074, ADR-0078

## Context

Satori already has a local modular monolith with explicit persistent owners, provider-neutral
conversation, history, memory, emotion/mood, relationships, structured cognition, and bounded
learning. A terminal remains the primary user surface. The user now prioritizes an application
that stays available during ordinary computer use and can later acquire voice, approved sources,
tools, and bounded background tasks.

Character acceptance remains open. The instruction pauses improvements to Character, Emotion,
and Relationship expression; it does not disable their existing owners or ordinary runtime
transitions. A desktop shell can reuse the existing application contract without choosing a new
persona or fixing provider behavior. Waiting for autobiographical narrative, emotional concepts,
proactivity, or an avatar has no correctness benefit for that bounded shell.

The previous dependency analysis already distinguishes these delivery choices from hard safety
dependencies. The new instruction explicitly changes delivery priority; it does not grant access
to accounts, authorize paid inference, complete 14.4 privacy, or open Stage 15.

## Decision

### Delivery order and acceptance

Use the product milestones in [Desktop and Companion Roadmap](../desktop-roadmap.md):
**D1 Desktop → D2 Voice → D3 Safe Tools & Internet → D4 Personal Sources → D5 Agent Runtime →
D6 Daily Assistant → D7 Multimodal & Mobile**. Necessary permission/source/action substrate is
implemented before the first real tool or connector within its milestone. Security is not deferred
to D5 or a final release.

D1 is explicitly opened by this instruction. Its completion does not automatically open all
later milestones. Each accepted delivery has a runnable scenario, relevant automated checks,
documented limits and a separate progress entry. Existing Stage 15–28 identifiers remain useful
capability references; they are not renumbered or retrospectively marked complete.

Character improvement is paused at its existing unaccepted checkpoint. Current prompts, provider
defaults, feature flags, owners, state schemas, evidence rules and behavioral verdicts are preserved.
No new seed, renderer, phrase rewriting, appraisal tuning, or behavioral experiment belongs to D1.
Existing functionality continues through the same use cases. Character acceptance is a separate
product-quality claim and is not inferred from desktop tests.

### D1: native shell and owned worker

Choose Python/PySide6 for the first desktop shell. It reuses the repository's Python application
layer and avoids a second frontend runtime, HTTP server, or replacement core for this first slice.
Qt dependencies belong to the desktop adapter; core/domain/application remain framework-independent.

The GUI owns one `QProcess` worker connected through inherited process pipes. The worker calls
existing application use cases directly and owns its composed provider/runtime resources. It does
not parse interactive CLI output, synthesize user messages for control commands, or expose an
unauthenticated localhost API. Versioned, bounded commands/events identify requests and report
safe status/error metadata. UI code never writes repositories or canonical SQLite tables.

Worker control and conversation submission are explicit operations. Activation is a separate,
visible user decision; opening status/history must not silently create a new identity. Conversation
replies become completed only after the existing canonical commit succeeds. Reconnect or restart
does not silently resubmit a possibly committed request. Stop requested, stopping, stopped,
failed, and restarting remain distinct from a completed reply or cancelled external task.

One desktop instance owns its worker and launch controls. Preserve the CLI as a developer surface;
the desktop's ownership/single-instance checks must not be described as protection against every
independently launched CLI or third-party process. One canonical database and existing storage
barriers remain authoritative. An incompatible worker/protocol fails visibly.

### Lifecycle and operating-system boundary

Closing the window leaves the application available through the system tray/menu-bar icon.
Explicit Quit shuts down the owned worker. Start/stop/restart use a graceful protocol that stops
accepting work and lets an already admitted interaction reach a truthful result. A forced process
termination, if needed, must be visibly distinguished from successful cancellation; an ambiguous
operation must not be automatically replayed. Bounded worker restart/backoff and a visible failed
state avoid an endless crash loop. Recovery must not start a fresh identity or reset the database.

Login launch is opt-in, reversible, and separate from activation. The first macOS delivery is a self-contained local `.app` with Python, Qt and explicitly
allowlisted migration/seed resources. A source-linked development launcher is retained as a
developer alternative. Building either artifact must
not silently register or enable a login item, install a service, modify real user data, or launch
an inference workload. GUI/worker ownership is sufficient for the first background experience;
an independent OS daemon is not required merely to keep a tray application open.

The local bundle includes its runtime but not model weights or user data. Local ad-hoc signing
is verified; Developer ID signing, notarization, clean-machine install/upgrade/uninstall, update
delivery and Windows packaging require their own
verified release slices. Supported platform behavior is measured on the actual host and recorded
in `progress.md`; a Qt API's existence alone is not target-platform acceptance.

### User surface and privacy

D1 provides chat, persisted conversation history, explicit activation, activity/runtime status,
local model settings with a fixed loopback Ollama endpoint, bounded memory
inspection, and safe diagnostics accessible from the GUI. Settings changes take effect through
an explicit runtime restart; they do not rewrite canonical identity or personality. Existing
provider/model names may be displayed without exposing credentials.

Do not create a plaintext cloud-key editor or silently enable cloud disclosure. Provider expansion,
OS-backed credential custody and explicit operation-scoped disclosure controls are prerequisites
for new cloud/source capabilities. D1 does not claim that inherited environment configuration is
a completed consent system. Diagnostic events exclude raw conversation, memory bodies, prompts,
credentials and arbitrary provider exception text. Content deliberately viewed in chat/history/
memory remains sensitive local UI content, not telemetry.

Memory inspection reuses scoped owner reads. Broad erase/export/restore buttons are not added
without their existing preview/freshness/approval contracts. Existing logical-retention controls
do not prove physical erasure, encryption, authenticated access, or whole-14.4 acceptance.

### Stable extension seams

- Voice feeds the same application interaction boundary; transcript uncertainty, capture consent,
  interruption and delivery semantics remain explicit. PTT precedes hands-free and full realtime.
- Avatar consumes immutable expression/state projections; it is not a second personality source.
- Tools receive typed proposals through deterministic scope/risk/confirmation policy and produce
  attributed receipts. The model never gains direct shell, browser, OS, repository or SDK authority.
- External sources need source/account/resource identity, provenance/revision/freshness, scoped
  consent and retention/disclosure gates before ingestion. They are never fake user messages.
- Scheduler and delegated tasks reuse action/permission contracts; they do not derive permission
  from warmth, proximity, elapsed time, a notification, or a previous tool result.

## Verification and limits

D1 requires protocol/size/error tests; explicit activation and no hidden initialization; persisted
history/memory scope checks; single-instance and worker ownership; failure/restart/no-resubmit;
graceful shutdown; secret/content-free diagnostics; settings validation; and opt-in login-launch
configuration tests. Run the mandatory Foundation toolchain with the wheel rebuild first.

The manual gate covers the actual macOS app opening, chat/history continuity on a synthetic
installation, tray reopen after window close, start/stop/restart, visible provider failure,
settings restart and memory inspection. Login-session behavior and clean-machine distribution
remain NOT_CHECKED until actually exercised; a generated launch configuration is not that proof.
No paid inference or private-account access is authorized by this ADR. Offline/fake-provider verification must be labelled
as such and cannot establish real model quality, latency, voice quality, or account integration.

## Consequences and alternatives

The first useful product can ship while Character remains frozen and unaccepted. One existing
core is retained, with a small native adapter and an isolated worker rather than another server
or canonical state owner. The source-linked launcher remains a development tool. The actual self-contained local build
removes the checkout/venv dependency, while public distribution remains a separate release gate.

An HTTP/browser UI would add local-server/session/auth concerns; a second-language shell would add
another runtime and packaging boundary. Both remain possible later if a concrete product need
justifies them. An independent service is likewise a later operational choice, not a prerequisite
for this first tray application. None of these choices permits weakening the preserved safety
contracts or treating desktop completion as Stage 15, privacy, or Character acceptance.


### Packaging verification amendment — 2026-10-16

The source-linked launcher stalled before GUI startup while Python opened its environment in
Documents. The final local artifact therefore includes its Python and Qt runtime. Generated
FinderInfo/ResourceFork metadata in Documents caused strict signing failures, so build staging
and verified installation moved outside Documents. Only those generated bundle metadata attributes are
removed before local ad-hoc signing; quarantine/security controls are not bypassed.

Migration resources are allowlisted, dynamic `logging.config`/Alembic modules are collected, and
bytecode writes into the signed app are disabled. Canonical state, credentials, private `.env`,
logs and model weights are excluded. A successful build must pass strict code-sign verification
and real frozen-worker activation/restart checks before delivery. Evidence is recorded in progress.
