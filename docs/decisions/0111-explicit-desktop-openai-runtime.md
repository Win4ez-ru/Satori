# ADR-0111: Actual desktop OpenAI runtime and bounded adoption

- Status: Accepted for implementation and offline qualification; real charges and adoption remain separately admitted
- Date: 2026-11-06
- Authority: the user's earlier explicit instruction to move all model tasks to the existing OpenAI API within3000RUB/month, continued by the request for useful contextual source answers in the ordinary app
- Extends: ADR-0100/0102 all-role OpenAI composition from the server candidate into the actual desktop worker
- Preserves: one Core/self/memory owner, all source-policy/custody/effect boundaries, existing budget accounting and consumed/expired test grants

## Evidence and scope

The installed Mac application still selects `qwen3:4b-instruct`. Its launcher passes a model
name but no OpenAI runtime; `DesktopBackend._default_settings` explicitly chooses the default
local providers. The earlier all-role GPT candidate and public Linux trial did not change
that fact or deploy a personal production server.

A bounded public-only real-Qwen smoke of ADR-0110 failed semantic acceptance: invented search
addresses/handles, unsupported absence and accommodating an external request to confirm an
invitation. No actual confirmation, account access or canonical source learning occurred.
These failures are preserved; successful fake-transport tests do not establish model quality.
The new source-analysis capability must not silently run on that rejected configuration.

Implement actual desktop configuration/launch wiring to the existing all-role OpenAI factory,
shared credential-safe HTTP client, nine role-specific budget transports and one usage ledger.
Use `gpt-6-luna` and `text-embedding-3-small`, existing reasoning/output bounds, Standard tier,
no paid built-in tools, local fallback or second identity. A model name alone is not activation.
The worker reports its actual admitted provider, model, budget and source-analysis capability.

## Explicit private configuration and credentials

Use a strict profile-bound private metadata document. It names the selected mode, exact explicitly selected
credential reference, current UTC budget period/cap, reviewed tariff/FX assumptions and explicit
authorization. It contains no key value. Its reader rejects foreign ownership, unsafe modes,
links, replacement, unknown fields and mismatched data-directory identity.

For a new setup, the desktop can review the profile-local `.env` reference for
`SATORI_OPENAI_API_KEY` using file metadata only. An existing saved reference remains
explicitly configured. At runtime resolve only the reviewed reference, without
interpolation or secret-bearing diagnostics. Keep key material outside preferences,
arguments, logs, bundles and snapshots.

Settings explain the configured provider and cloud processing purpose. A clear reviewed action
selects this configuration; ordinary startup does not spend, reindex, transfer a profile or
silently enroll an account. Invalid selected-API configuration fails closed with actionable
status rather than reverting to a local model. Local conversation may remain an explicit mode;
source analysis is enabled only by the worker's admitted capability, not an editable model label.

## Budget and transport ownership

Use one fixed per-profile production usage namespace. Never borrow/reset/relocate the expired
20RUB operator-test ledger, invent a new test quota, or change the namespace to evade previous
liabilities. A proposed1000RUB model-API cap fits inside the user's3000RUB total target with the
existing configured710RUB host; the remaining allowance is not a verified voice/telecom bill.
Configure the actual period/cap only through explicit reviewed runtime configuration. Preserve
the ledger's immutable period cap, unknown-outcome liabilities and reserve-before-dispatch path.
No automatic period/cap renewal is introduced in this slice.

Retain the existing7-day maximum tariff-review lifetime and conservative cache-write accounting.
The FX ceiling is a declared guard assumption, not a current exchange quote. Do not stamp old
bundled prices as freshly checked when the user opens settings. Expiry blocks new paid dispatch;
it does not authorize a fresh budget or fallback provider. Close the shared client/ledger only
after Core drain. Switching modes does not silently retry any failed or unknown inference.

Official rates checked2026-11-06: Standard short-context Luna input0.10USD/output0.50USD per
million tokens, cached input0.01USD,30m cache writes0.125USD; embedding input0.02USD per million.
The existing budget transport conservatively accounts for the higher cache-write factor.
[OpenAI API pricing](https://developers.openai.com/api/docs/pricing),
[Luna model ID](https://developers.openai.com/api/docs/models/gpt-6-luna).

## Memory adoption

Changing the embedding provider must never relabel old vectors or claim unchanged recall.
Preserve canonical episodes, histories, self and relationship state. Reuse the existing
`IndexAllEpisodicMemories` owner for an explicitly approved bounded batch, with bounded SQL
enumeration, identity/counterparty/session/generation and current source-policy admission.
Limit each adoption batch to100 eligible memories; report indexed/deferred/failed explicitly.
No new embedding store, text mutation, startup autoindex, unrestricted crawl or silent rebuild.

Until compatible vectors are available, report the actual adoption state. Recent canonical
context remains available; missing long-term retrieval cannot become invented recall. Real
canonical-memory disclosure/indexing is separate from offline fake-embedding qualification and
must retain the configured budget and source-policy transport guards.

## Qualification and limits

Test actual desktop launch/worker/provider-status plumbing with explicit public private-file
fixtures and fake HTTP transports, all nine roles, credential reference replacement/revocation,
bad/expired pricing, cap persistence/restart/unknown outcomes/no raw fallback, source capability
gating, bounded indexing and source/context provenance. Preserve legacy local conversation,
source-task/account custody and the failed Qwen sample without rewriting output.

Run the combined frozen Foundation/package/native/install gates once after integration. Actual
GPT semantic quality, current key/account validity, personal-profile adoption/indexing and real
mail/Telegram reads are distinct acceptance checks. No live charge follows solely from this ADR,
and no consumed paid-test grant is reopened. Personal deployment, remote source ownership and
Mac-asleep iPhone remain separate; this decision does not upload keys or profile to the VPS.
