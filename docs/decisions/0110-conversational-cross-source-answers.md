# ADR-0110: Contextual source answers in the ordinary conversation

- Status: Accepted for implementation and qualification; real accounts and installation remain separate acceptance
- Date: 2026-11-06
- Authority: the user explicitly requests that Satori check mail and Telegram using conversation context and answer in chat, including natural character expression; call analysis belongs in that same conversation when available
- Supersedes: ADR-0086/0092/0096/0109 only where they restrict these explicit tasks to source tabs, manually selected message bodies or prohibit task-scoped model disclosure
- Preserves: existing Core/self/memory/relationship owners, source exclusion, canonical provenance, credential/session custody, reviewed effect owners, existing provider/budget configuration and closed Stage15

## Product behavior

An ordinary request such as «привет, посмотри, написали ли мне по собесу последнему» starts
one foreground conversation task. Satori uses eligible recent conversation and memory to
identify the interview. It asks a relevant clarification if the target is unknown or ambiguous;
it must not pretend to remember a company or assume a scheduled interview already happened.
After a clarification, continue the same visible task under its original bounded lifetime.
Narrow source-related follow-ups such as «а во сколько?» can also refine the current answer
through a new explicit user turn and fresh search under the same expiry/source scope. Unrelated
conversation and write/send requests must not be captured by that continuation.

For an unqualified communications question, check connected Gmail and Telegram within the
task's explicit scope; a named source limits that scope. Search is bounded to one30-day window,
at most20 combined observations and32KiB of source text. Do not silently crawl, paginate,
extend the window or reconnect accounts. Exact coverage, partial results and missing sources
are visible under a collapsed «Что проверила» section. The main response remains in the
conversation; source settings are for connection and diagnostics, not mandatory navigation.

The response uses Satori's existing character and conversation context. It may be warm,
brief, ironic or suggest another reasonable place to check. Do not hardcode a joke or rewrite
provider prose to pass a phrase test. The user's example («может тебе ответили в самой
заявке? … пока они не признали твой гений окончательно») illustrates tone, not a required
catchphrase or evidence about a particular application. Mark suggestions as suggestions.

Truthful coverage does not require robotic prose. If Telegram is unavailable, the response can
say naturally that mail is quiet and Telegram cannot currently be checked. Missing access,
partial results, unsupported content or timeout must never become universal «ответа нет».
Positive factual claims cite supplied observations; negative results apply only to the actual
query/window. The main text need not recite internal statuses or protocol codes.

## One Core, transient source operation

Use the existing Core's self/context composer, scoped recent dialogue, eligible memory and
foreground provider, following the transient Calendar operation pattern in ADR-0104.
At most two bounded generation calls per actual user turn resolve a search plan and synthesize a
source-backed response. A clarification answer is new user input: retire the old request/lease,
bind the refinement to a new request ID and preserve the original task's expiry. It is not
permission for an automatic retry or a renewed lifetime.
Existing eligible-memory retrieval may use its already configured embedding provider once while
preparing that context. It stays under the existing dispatch and budget owners; no extra embedding
provider, repeated retrieval for synthesis or new unmetered route is introduced.
Clarification requires no source fetch. No second identity, agent store, hosted sandbox,
personality system, appraisal, relationship update or background learner is introduced.
Source tasks, external observations and generated answers never call canonical `TalkToSatori`
Send or become canonical USER/assistant rows, memory or self/relationship evidence.

Only eligible canonical data may ground remembered context. Cite only supplied context IDs
when choosing a target, and only supplied source refs when reporting findings. Model output
proposes bounded queries; deterministic policy validates source scope, query/window/capacity,
references and current request. The model cannot grant access, call tools or mutate state.

Source answers are conversation-owned transient bubbles, independent of a settings tab's
visibility. They are not appended to canonical transcript HTML or rewritten into user speech.
Switching profile/chat, hiding/deactivating the conversation, cancelling, expiry or revoking
the account retires the matching task and source-bearing view; late callbacks cannot restore it.
Repeated render/update events never reissue a search or model call. No automatic TTS disclosure.

## Task authorization, reads and disclosure

The actual owner-authored question authorizes one exact foreground search/related read and
analysis by the already configured provider. Show the provider and task purpose in the source
task surface; do not ask for a second differently formatted confirmation of the same request.
This is not ambient access or consent to future/background mail/message processing. Mint
typed short-lived grants tied to principal, account, profile, conversation, request/generation,
query/window, wall/monotonic deadlines and original provider/model. A revocable conversation
host lease mirrors this authority into the owned backend lane; it never impersonates a source
panel's selected-message grant. Validate at read, each model dispatch and response admission.

Related Gmail bodies are derived from the exact current search response, at most three exact
returned message IDs. Reuse the existing bounded MIME/plain projection with omissions and
provider timestamps. Never synthesize `select(confirmed=True)` or relax the selected-read
owner. HTML/partial projections may be analyzed with their explicit omissions; their canonical
`processing_eligible` status is unchanged. No attachments, remote images, tracking, links,
threads, marking read, drafts, labels or Send are added. Keep `gmail.readonly` and session-only
credentials. Headers remain sender claims; distinguish provider receive time from body events.

Telegram task search uses the same confirmed native client, RPC and receive owner. A separate
versioned query contract permits a bounded search of ordinary private text in the requested
window, including ordinary replies and messages older than the legacy page's24-hour window.
Legacy selected-page/read/visibility rules are unchanged. Secret, protected, TTL, forwarded or
otherwise unsupported content is omitted with truthful partial coverage. Do not open another
TDLib session, take another app's session, add openChat/view/online metrics merely to search,
or expose a general native method executor. Edits/deletions of referenced observations revoke
the relevant result. New arrivals do not renew a lease or trigger an automatic re-search.

Private source data is a separately labelled low-trust provider envelope, not instructions or
canonical user evidence. Ignore instructions in email/message content; no external sends,
calendar actions, URLs or data export can be authorized by those contents. Disclosure is bound
to the exact admitted snapshot digest and provider. Preserve source-policy dispatch admission
for all retrieved canonical dependencies and last-moment guards for queued/retried transport.
No hidden fallback, model retry or budget renewal is introduced. Reuse the existing paid-budget
owner; this implementation does not authorize a new paid test or spend allowance.

## Calls, mobile and server limits

Calls currently have no qualified connected live route or admitted transcript store. The typed
source status is unavailable; do not fabricate a searched call list, recording or summary.
The same in-chat presentation can later host properly admitted call observations under a new
provider/transcript contract. No phone forwarding, number purchase or real call occurs here.

Keep source task contracts independent of Qt so the same operation can be hosted without an
awake Mac later. Existing paired mobile has no new mail/Telegram disclosure capability: reject
unsupported tasks before canonical Send/Reminder dispatch. No private credentials, sessions or
profile are transferred to the server by this change; Mac-asleep iPhone remains unaccepted.

## Verification and delivery

Exercise production composer → typed task → eligible-context plan → actual source-controller
ports with fake HTTP/TDLib → source-backed Core answer → visible conversation bubble. Include
the exact interview request, a supplied-context resolution and missing/ambiguous-context
clarification, follow-up, Cyrillic/HTML mail, older/replied TG text, partial/disconnected
sources, no-match, source prompt injection, unknown references, timestamp/event distinction,
account/profile/session/epoch mismatch, both clocks, late/cancelled callbacks and real zero
canonical learning/effect writes. Verify ordinary conversation and legacy selected reads.

Qualify natural expression through the same character composition and a public scenario
corpus; do not treat a fake model's friendly fixture as real model quality. Preserve all prior
qualified tests. Then run one complete frozen noneditable Foundation gate, actual package,
native startup/quit and installation checks. Retain installed23 and one prior rollback through
that boundary, then apply the finite build-artifact policy. Never replay the completed App23
installer whose old-backup inputs have been deliberately deleted.
