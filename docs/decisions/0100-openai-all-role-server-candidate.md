# ADR-0100: All-role OpenAI inference and a server candidate

- Status: Accepted for implementation and qualification; deployed application and personal-state transfer remain separate
- Date: 2026-10-31
- Authority: explicit user instruction to move all model tasks to GPT API and select hosting independent of an awake Mac, Russian-card payment, total monthly ceiling 3,000 RUB
- Extends ADR-0099; supersedes the foreground-only OpenAI restriction of ADR-0031 for the explicit all-role mode below
- Preserves source-policy, canonical ownership, Character pause and closed domain Stage15

## Scope and isolation

Continue the source-only candidate in `var/artifacts/server-runtime-20261031/work`. Implement
conversation, episode, embedding, semantic, appraisal, relationship, models, positions and
reflection through real OpenAI adapters. Do not change the installed Mac16, qualified bundle17,
iPhone3 or their source freeze while this candidate is qualified. No personal-state migration,
purchase, provider signup, firewall/public exposure or real call is implied.

The user reports an existing OpenAI API account/balance and use from the Netherlands. Those
are user reports, not an inspected account or verified balance. Select supported-region hosting
and factual billing details; Russian-card payment remains required. Public tariffs do not prove
checkout success, tax handling, eligibility, supply or actual compute performance.

## Explicit routing and independent state

An explicit all-OpenAI configuration must include all nine roles and reject mixed or missing
assignments. Default local operation remains available for existing installations, but this
server mode has no hidden Ollama fallback. A provider failure is a provider failure, not
permission to disable affect, memory formation or evidence checks.

Initial candidate: `gpt-6-luna` for the eight generative roles and `text-embedding-3-small` for
embeddings. Pin the supported endpoint, model IDs, dimensions and input-schema identity. This
is candidate selection, not semantic/character acceptance. Alternative models need their own
quality/cost qualification rather than silent routing on errors.

Reuse the existing role policies, typed proposal documents, evidence mapping and owner
validation. Extract pure reusable builders/parsers if required; do not wrap OpenAI output in
a pretend Ollama HTTP envelope or label OpenAI receipts as Ollama. Distinct provider/method
provenance must survive persistence and replay. A model never writes state directly.

OpenAI receives the bounded context required by the explicitly requested model operation:
current canonical input, bounded dialogue, relevant retrieved memory, typed self projections
and owner-selected evidence for the applicable role. No entire database, arbitrary source
snapshot, credential or raw reasoning is uploaded. External mail/calendar/Telegram/file/tool
cards remain outside this admission. Their separate disclosure restrictions are unchanged.

## Wire, credentials and failures

Use canonical HTTPS `https://api.openai.com/v1`, no redirects to custom credential recipients,
stateless Responses requests with `store=false`, no background hosted execution or built-in
paid tools. Strict structured output uses the documented required/nullable object schema;
local Pydantic/domain validation is still mandatory. Refusal, incomplete, mismatched model,
unexpected output items, malformed JSON and absent usage are not valid completed proposals.
No raw chain-of-thought is requested, retained or logged.

API keys remain transport-local secrets from explicit environment or qualified secret storage;
never put keys in chat, logs, SQLite, exports, a phone build or checked-in configuration. No
key discovery by scanning unrelated files/accounts. Prepare concrete safe configuration before
requesting any manual credential step. Missing credentials prevent dispatch, not offline tests.

## Budget admission and reporting

Every role and every retry must pass one metadata-only budget owner before the actual HTTP
send, including a retry eventually rejected by semantic validation. Reserve conservatively
using bounded request/output sizes and explicit current tariff/exchange assumptions. Only one
dispatch permission is issued for an attempt ID. Unknown transport outcomes retain liability;
read-only reconciliation never replays a request. Missing usage is unknown, not zero.

Provider-reported tokens/cost estimates differ from verified invoices and bank conversion.
A RUB ceiling based on an exchange-rate assumption is not a guarantee of future FX, tax or
card-processing fees; admission must reject unknown price/model/tier rather than pretend a
zero charge. The global 3,000 RUB ceiling includes server, backup, model, voice and ordinary-call
provider charges. Unconfirmed telephone fees keep the full total unknown and calls disabled.
Do not double-count call ASR/TTS/inference in both model/voice and telephone-provider categories.

The first bounded usage store remains a preparation component with explicit capacity limits.
Qualify retention and sustained multi-role workload before calling it production daily-use
accounting. A test ledger and synthetic tokens are never reported as real account spending.

## Embeddings and continuity

OpenAI embeddings have their own provider/model/dimensions/input-schema space. Existing Ollama
vectors are never compared with or relabelled as OpenAI vectors, even with equal dimensions.
Use existing owner-controlled index construction for eligible evidence and expose absent or
pending coverage honestly. No automatic destructive index purge or canonical-memory mutation.
A reviewed reindex/continuity check is required before promotion of an existing profile.

## Delivery gates

Run adapter wire/validation/refusal/usage tests for every role, existing Ollama regression tests
for extracted helpers, all-role composition tests, budget attempt/failure/retry/concurrency
checks, embedding-space incompatibility tests and an end-to-end synthetic Core conversation
with all nine OpenAI ports and no local network/provider fallback.

Before integration/release, run the complete fresh noneditable Foundation toolchain. Before
model acceptance, run applicable affect, character, grounding, continuity and replay corpora
against the actual configured provider; report incomplete or unrun evidence honestly. Before
hosted delivery, qualify Linux, authenticated client networking, encrypted backup/restore,
reviewed single-writer state transfer and Mac-asleep iPhone continuity. Passing adapters alone
is not a deployed independent Satori.
