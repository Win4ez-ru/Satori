# ADR-0092: Unified personal-source desktop workspace

- Status: Accepted for bounded implementation; account and installed acceptance remain separate
- Date: 2026-10-29
- Authority: the user confirms the persistent companion/product vision and explicitly requests
  coordinated Calendar, Gmail and Telegram delivery in one usable Satori application
- Extends: ADR-0079/0082/0083/0086/0090 with a common workspace and an owned Telegram adapter
- Supersedes: only the provisional standalone-only Telegram delivery/admission ordering in
  `docs/parallel/telegram-2026-10-20.md` and the source-only frontend's staging admission DTO
- Preserves: source eligibility, selected grants, privacy/action gates, Core owners and prompts,
  Character pause, Stage 15 closure and all unrelated account/provider permissions

## Decision and nearest delivery

Use one ordinary DesktopWindow for conversation, Calendar, Gmail and Telegram. A common home
surface shows current connection, selection, freshness and actionable failure states, and opens
existing source controls. Source status is a projection of the existing owner, not another
permission or snapshot owner. Constructing or opening the application performs no account
connection, calendar/mail/message read, model call, microphone use or location/screen capture.

Keep the working reviewed calendar-import flow and existing iCloud calendar. The already
created November batch is not imported again. Existing deterministic transient Calendar cards
remain separate from canonical conversation and explain selected coverage/freshness. Gmail
keeps its qualified foreground/session-only route while persistent credential custody remains
unqualified. A blocked one-use Keychain fixture is not retried or relaxed under this decision.

Integrate the separately corrected Telegram worker behind one owned Qt process transport and
protected authentication controls in the same application. Reuse the frozen r5 wire/lifecycle
semantics rather than launching another diagnostic app or reusing another Telegram client's
session. The visible Connect action starts the separate user-authorized temporary client;
account confirmation, bounded catalog, one selected personal chat and explicit Read remain
separate actions. Credentials travel only over the private bounded process channel, never
arguments, environment, preferences, logs, fixtures or canonical state. No Send is introduced.

Runtime engineering qualification and actual account acceptance are separate records. Replace
the source-only frontend's provisional `selected_read_receipt_sha256` prerequisite with a
root-verified typed runtime qualification binding exact worker/schema/native-library/policy
bytes and the actual owned transport. Do not manufacture a selected-read acceptance receipt
from synthetic tests. This permits user-owned account qualification in the integrated app;
it does not claim that login or reading has passed. A worker frame, checkbox, DTO supplied by
an arbitrary callback or stale previous qualification cannot admit a runtime.

## Source and lifecycle boundaries

The existing source coordinator remains the single owner of principal, generation, selected
resources, grants and snapshots. Neither the home UI nor an LLM can issue or extend them.
Source content has no canonical USER/history, memory, self/relationship, provider or TTS path.
The existing Calendar transient-card exception is unchanged. Natural-language source inference,
mail/message summaries, drafting, sends and a general agent executor are outside this slice.

For Telegram, a well-formed new message makes a displayed/in-flight page stale. Drop its text,
handles, pending body result and metrics; preserve the already confirmed account, selected chat
and exact original session/grant deadlines for an explicit new Read and eligibility recheck.
Never auto-fetch or retry. Retire interrupted RPC tokens and known invalidated page IDs with
finite bounds; only exact known stale callbacks may be discarded. Unknown/foreign/malformed
callbacks fail closed. Identity/privacy/protection/TTL, edits/deletions, auth loss, expiry,
clock rollback and other terminal failures keep their current revocation behavior.

Quiet reading in a foreground window does not revoke source access after 15 seconds without
input. Active-use metrics and presence reflect actual activity separately from passive visible
time. Telegram hide/deactivate/minimize/occlusion and explicit conceal still clear its local view.
Catalog expiry blocks new selection; it does not shorten an existing valid selected/page lease.
No deadline is renewed by repainting, polling, staleness or a home status request.

Integrate Telegram in profile-change, conceal, quit and bounded asynchronous worker shutdown.
A process-finish receipt, local directory cleanup, server Logout and physical erasure remain
different claims. A stopped or ambiguous account operation is not automatically replayed.

## Packaging and verification

The main bundle remains self-contained. Package an explicitly allowlisted worker/schema,
qualified native TDLib library and required provenance/licenses. Use the bundled executable's
separate worker entrypoint and bundled Python; no dependency on a checkout, temporary path,
visualization directory or `/Library/Frameworks/Python.framework` is admitted. Requalify the
worker, byte/resource bounds and actual native loading/close behavior on that packaged runtime
without credentials before handing protected account validation to the user. Loading a native
SDK without account parameters does not prove connected-account acceptance.

Keep the previous installed bundle and profiles intact while preparing the candidate. Root
owns the source freeze, required fresh noneditable Foundation gate, artifact/signature/parity
checks, ordinary GUI check and reversible app-only installation. Parallel chats run focused
checks in disjoint assigned paths; they do not run competing full gates or modify live accounts.
Test actual DesktopWindow/profile/hide/quit wiring, zero startup I/O, source isolation, stale and
late-result races, catalog versus lease expiry, protected-field redaction and packaged entrypoints.
Account/provider/device acceptance is reported separately from engineering/build success.

## Parallel next direction

The user additionally requests consented foreground activity/screen context, location and maps
with preference-aware place discovery. A separate workstream prepares the concrete provider,
permission/retention/disclosure contracts and isolated prototypes. This decision does not turn
on capture or location, permit historical surveillance, create a persistent inferred User Model,
or disclose coordinates/screen/mail/message content to a provider. A subsequent bounded
integration must define exact session controls, OS permissions, source scope, freshness,
unknown opening-hours semantics and external disclosure before actual capture or network use.
