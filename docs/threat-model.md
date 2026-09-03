# Failure modes and threat model

Scope: risks to continuity, truthfulness, autonomy, privacy and recoverability. `Stage` is the latest point by which mitigation must be operational, not permission to ignore the risk earlier.

| Risk | Impact | Mitigation | Detection | Stage |
|---|---|---|---|---:|
| Personality drift | Satori becomes unrecognizable | Single owner, exact tiny step, endpoint + rolling/lifetime path + approved-checkpoint budgets, cooldown, immutable checkpoints | Long-horizon/reversal evolution and anchor gates, audit drift dashboard | 2 read-only; 14 mutation |
| User mirroring | Loss of independence and intellectual honesty | Separate user vs Satori state; direct trait/taste assignment and user self-ascription ineligible; purpose-specific diversity | Repeated/opposite-pressure adversarial eval, paired trajectory equality and alignment correlation | 3/11/14 |
| Personality evidence correlation | Repetition/paraphrase or one upstream artifact appears independently sustained | Root/interaction/session/week/month/signature/near-duplicate/lineage gates; max two counted roots per lineage | Exact diversity boundaries, paraphrase and one-lineage months-long corpus | 14 |
| Personality feedback through affect/inclinations | Existing character reaction or learned interest proves more of the same trait | V3 purpose receives no current trait values, affect attachment or inclination evidence; accepted roots single-use across traits | Forbidden-source graph, affect/inclination A/B and replay fixtures | 14 |
| Personality checkpoint gaming | Reversal or automatic checkpoint approval resets drift budget | Path never refunded; automatic checkpoints do not reset budget; only explicit append-only approval selects a new origin | Reversal/approval/restore long-horizon simulation and audit inspection | 14 |
| Personality restore corruption | Rollback erases history, changes values/baselines or restores tampered state | Full-vector hash/identity/key validation; optimistic expected version; append-only restore/event/checkpoint | Hash tamper, partial-write, stale restore and export/restart equality | 14 |
| Invisible personality mutation | Numeric traits change without observable behavior, making evaluation gameable | Context v16 records aggregate version; bounded current-vs-baseline qualitative Expression Projection V2 | Before/after/restore anchor projection and sampled conversation rubric | 14 |
| Inclination mirroring / fake autonomy | User taste, assignment or leading question becomes Satori's own preference/interest | Separate inclination aggregate; conservative like/dislike/assignment registry; exact cited-label match; verified Reflection V2 affect attachment; multi-root/session/signature/span gates | User-only and opposite-taste/identical-affect longitudinal fixtures; alignment-correlation metric | 13 |
| Position evidence laundering | Repeated assertion, retrieved/profile text or assistant output appears to corroborate a Satori belief | Only exact canonical user-message roots; materiality plus distinct interaction/content thresholds; retrieved, model and assistant data excluded | Repetition/paraphrase, poisoned-source and provenance-graph fixtures | 11 |
| Position confidence inflation | Retry, duplicate quote or provider confidence makes a weak position look established | Source/version idempotency, message/signature dedup, deterministic kind caps and explicit target version | Replay/restart/concurrency and exact-cap fixtures | 11 |
| Position conflict erasure | Revision silently overwrites kind, evidence or an opposing hypothesis | Immutable kind, append-only revisions, explicit supersession and paired competing hypotheses | Revision/competition/export round-trip fixtures | 11 |
| Memory poisoning | False/hostile content shapes behavior/state | Provenance/confidence, untrusted memory envelope, owner validation, disputed status | Poison fixtures, source graph audit, anomalous proposal reasons | 4/6 |
| False memories | Fake continuity and broken trust | Retrieval-grounded generation, must-not-claim strategy, explicit uncertainty, source-required memory | Unsupported-claim eval, trace source inspection | 3/4 |
| Duplicate memories | Context pollution and double-counted evidence | Idempotency keys, root-message dedup, structured identity, merge lineage | Replay/concurrency test, duplicate-rate metric | 4/6 |
| Contradictory semantic facts | Confidently wrong user/world model | Competing claims, no silent overwrite, explicit resolution state | Conflict fixtures, contradiction disclosure metric | 6 |
| Relationship runaway | Affection/trust explodes with message count | Meaningful-event proposals, bounds/rate limits, per-person state | Long-session stress test, dimension time-series | 8 |
| Emotion runaway | Unstable responses and feedback loops | Bounded vector/delta, deterministic decay, no expression→state write | Property tests, time-series bounds, replay | 7 |
| Reflection feedback loop | Self-reinforcing personality/belief changes | Persisted fixed canonical leaf set; current/generated/reflection state is never evidence; stricter owner thresholds and completed-input consumption | Forbidden/cyclic lineage, replay, repetition and confidence-inflation corpus | 12 |
| Inclination attachment substitution | A source is paired with another interaction's affect or a changed transition to fabricate experience | All-or-none immutable transition ID/state version/signal hash persisted before inference and included in V2 source-set hash; owner verifies identity, interaction, message and fixed-run membership | Missing/partial/cross-identity/cross-interaction/stale-version/hash-tamper fixtures | 13 |
| Inclination feedback loop | Current inclination, its evidence or generated reply helps produce affect or future evidence that strengthens itself | Inclinations excluded from affect, retrieval, relationship, user/world formation and future reflection evidence; inclination evidence has separate tables and is absent from the source query | Forbidden-source graph traversal, reflection-after-commit and generated-reply replay fixtures | 13 |
| Inclination runaway / double count | Novelty, retries or one intense relationship rapidly creates a strong global taste | Root/interaction/transition/signature dedup; session/span diversity; deterministic signals; event/cooldown/rolling budgets; neutral-centred pure decay; relationship state never evidence | Exact-boundary/property, replay/concurrency, two-counterparty and long-horizon time-series fixtures | 13 |
| Reflection compute runaway | Hidden repeated calls consume local/cloud budget | Deterministic weekly automatic gate, rolling-day cap, fixed input/output/proposal/attempt limits and no automatic retry loop | Trigger-boundary, token-cap, outage and long-period distributions | 12 |
| Reflection partial progress | Crash leaves a mutation without outcome or replays an earlier owner change | Per-proposal target mutation + outcome + revision + audit transaction; resumable run finalization | Every write-point failure and multi-proposal restart fixtures | 12 |
| Prompt injection through memory | Retrieved text overrides policy or triggers mutation/action | Trust-separated context; content as data; schema and deterministic permission checks | Stored-injection adversarial suite, trace trust labels | 3/4 and every tool stage |
| Prompt injection through user/external content | Policy bypass, data/tool abuse | Clear trust boundary, least privilege, tools outside cognition, permission gate | Red-team fixtures and tool-call audit | 3; 24 for tools |
| Corrupted state | Identity/history loss or unexplained behavior | Transactions, foreign keys/checksums, backups, staged import, integrity check | Startup integrity scan, export round-trip, audit gap check | 1 then harden pre-v0.1 |
| Provider outage | Conversation unavailable; accidental partial state | Provider outside DB tx, timeout/retry policy, recoverable interaction status | Fault injection, availability/latency metrics | 3 |
| Provider behavioral shift | Style/quality drift despite same state | Capability contracts, pinned configs, provider eval matrix, template versions | Anchor behavior suite across models/versions | 3 onward |
| Schema migration failure | Unstartable app or damaged identity | Versioned migrations, backup, forward test, rollback/recovery plan | Migration CI on representative snapshots | 1 onward |
| Partial interaction commit | Response/state/audit disagreement | Atomic finalize; no non-streaming response before commit | Write-point fault injection and restart recovery | 1/3 |
| Idempotency failure | Double trust/memory/personality changes | Unique request/run key and prior-decision reuse | Replay suite and uniqueness constraints | 1 for interaction; 12 reflection |
| Excessive context | Lost critical identity/policy, latency and cost | Priority budgets, manifest, deterministic truncation/compression | Budget tests, composition telemetry | 3/5 |
| Latency growth | Unnatural interaction and timeouts | Stage timing, bounded retrieval, capability routing, caching only derived data | Percentile latency per pipeline step | 3 onward |
| Cognition pseudo-precision | Heuristic needs/position look like hidden truth or durable belief | Qualitative closed schemas, bounded weights, explicit uncertainty/fallback, transient artifacts only | Mixed-need, ambiguity and fallback fixtures; trace audit | 10 |
| Position/expression reversal | Friendly wording silently changes disagreement, uncertainty or evidence boundary | Strategy carries position stance/uncertainty invariant; deterministic pre-generation validation; grounding unchanged | Position-vs-expression fixtures and debug trace comparison | 10 |
| Cost growth / privacy leakage | Unexpected spend or over-sharing to cloud | Local-first, operation-scoped context, usage budgets/telemetry | Provider usage/context manifest review | 3 onward |
| Post-response failure evidence loss/leak | Billed usage becomes unknowable or rejected provider content enters artifacts | Typed errors retain only numeric usage and closed observed/completed/tier facts; exact integer ledger pricing; no text/body/prompt/reasoning content | Oversize/incomplete/malformed adapter fixtures, ledger privacy and exact-cost tests | 14.2 |
| One-shot evaluation replay or orphaned grant | Stale authority spends again, or a consumed grant has no diagnostic artifact | Distinct ID/digest/paths per attempt; retired digest rejected before I/O; safe targets preflighted; claim precedes immediate durable report; external review digest anchor | Preflight/lifecycle/source/Settings/path collision and immutable-archive tests | 14.2 |
| Cloud API-key exfiltration | Secret is sent to a compatible but attacker-controlled endpoint or appears in diagnostics | Yandex credential target pinned to canonical HTTPS `/v1`; `SecretStr`; transport-local auth header; no body/key logging or export | Config/transport target tests, repr/log secret scan | 14.1 |
| Hidden provider double spend | Automatic retry/fallback spends twice and changes semantics invisibly | No automatic Yandex retry, hedging or fallback in first increment; operator switch is explicit | Exact provider-call count and usage metadata in A/B gate | 14.1 |
| Friendly failure text masks an operator fault or becomes canonical | Characterful UI copy hides credentials/quota/storage action or contaminates history/state | Exact closed recovery allowlist; exhaustive enum partition; bracketed noncanonical label; actionable/refusal/unknown/persistence bypass; no `SatoriReply` construction | TTY/non-TTY cleanup, failed-history/no-affect/grounding, actionable and enum-exhaustion tests | 14.2 |
| Cloud scope creep | Memory owners/background proposals or broader local state are routed remotely without review | Yandex accepted only for foreground conversation; all structured/background settings reject it | Config/composition matrix and remote-request inspection | 14.1 |
| Raw plaintext retention | Local DB compromise exposes exact dialogue/evidence | Local-only scope, no prompt duplication/log content, explicit development retention policy; encryption/erasure gate before production | DB/table inspection, log leak tests, production-readiness review | 4; harden pre-production |
| Loss of provenance | Unexplainable facts/mutations | Source refs required at schema/policy boundary; no orphan commit | Referential checks and provenance-coverage gate | 4/6 |
| Over-proactivity | Annoyance/manipulation/dependency | Default nothing, reason/thread required, rate/quiet hours/permission | Initiation-rate/reason audit and user control | 19 |
| Over-support / constant validation | Dishonest, dependent or unhelpful behavior | Need-mix classification, support≠agreement, challenge/accountability evals | Support rubric and adversarial repeated pattern | 18 (baseline 3) |
| Cross-person leakage | Wrong memories/relationship/model claims affect another user | Relationship and Stage 9 model aggregates/reads/context/export are keyed by opaque counterparty; local deployment remains one configured counterparty and the opaque ID is not authentication | Two-counterparty relationship/model isolation suites; reject unauthenticated multi-user deployment | 8/9 |
| Generated output as evidence | Model invention becomes self-confirming history | Assistant message not evidence of external event/internal change; owner checks origin | Evidence graph rule and hallucination replay | 4 |
| Semantic retrieval feedback loop | Repeated model recall appears as independent corroboration | Formation accepts only new episodic root user messages; semantic/retrieved/assistant records cannot be evidence | Recall-then-reprocess eval, root-message uniqueness and graph inspection | 6 |
| Semantic overgeneralization | One anecdote or temporary state becomes permanent user knowledge | Closed predicates, epistemic kind, lexical root support, two-interaction inference minimum, conservative zero-claim output | Single-anecdote, temporary-event, unknown-predicate and hallucination evals | 6 |
| Semantic evidence double counting | Confidence inflates through retry or repeated reports | Source/version terminal decision, root-message dedup, deterministic source-count caps | Retry/concurrency/independent-evidence evals | 6 |
| Sensitive semantic aggregation | Compact stable profile is easier to inspect/exfiltrate than raw dialogue | Local-only scope, bounded provider context, values excluded from normal logs, production encryption/erasure gate | Log leak test, explicit CLI/DB review, production-readiness review | 6; harden pre-production |
| Malicious/invalid provider proposal | Bounds or permissions bypass | Treat output as untrusted, strict schema, owner policy, reason-coded reject | Fuzz/property tests and hostile adapter | 2 onward |
| Affective runaway or injection | User/retrieved text commands state, user emotion is mirrored, or repeated events create permanent extremes | Strict semantic appraisal, supplied-ID provenance, confidence gate, deterministic owner caps/bounds/decay, no relationship dimensions | Invalid/ref injection, distress, neutral/repeated/alternating/extreme simulations, restart/retry/conflict tests | 7 |
| Affective finalize divergence | Reply reflects tentative state that is not committed, or retry applies emotion twice | State transition/audit and canonical assistant finalize share one transaction; completed replay bypasses appraisal; stale base conflicts | Write-point rollback, 100-call replay, different-interaction optimistic conflict/retry | 7 |
| Premature streamed delivery | User sees text that canonical finalize later rejects or cannot store | No token streaming under current contract; full reply only after canonical reply/affect commit; future durable draft/outbox ADR required | Blocked/failing finalize and cancellation tests; delivery-order review | 7.5 |
| Unbounded recent history disclosure | Long session inflates latency/context or leaks unnecessary dialogue | Completed-pair-only projection, whole-turn/count/character bounds, oldest deterministic drop, no hidden prompts | 105-turn request-bound test and context manifest counts | 7.5 |
| Identity collapse / provider substitution | Model denies Satori's female identity, memory or affect, or claims Qwen is the persistent self | Typed runtime self-model, explicit provider distinction, trusted late reminder, sampled real-model corpus | hierarchy/conflicting-history tests, 3-session corpus and exact golden | 7.6 |
| Generated self-contradiction feedback | A bad assistant reply in recent context reinforces itself as identity authority | Recent assistant remains continuity data; current-turn trusted reminder follows it without editing history | conflicting recent assistant ordering test | 7.6 |
| Dialogue repetition blindness | Repeated input/correction receives the same generic answer or closing and escalates frustration | Bounded transient coherence signals, optional/specific questions, narrow typed max-one response regeneration | Exact 17-turn x3, 30-turn run, acknowledgement/generic-question/per-reason metrics | 8.1 |
| Policy phrase leakage | Truth/autonomy/boundary guidance becomes a repeated slogan instead of behavior | Policy v9 separates authoritative facts from expression guidance; no prompt or assistant phrase becomes self state | Catchphrase count in exact and long-run semantic review | 8.1 |
| Capability/curiosity conflation | Digital embodiment limit is expressed as indifference to the user's activity | Compositional capability + activity facets; physical claim and conversational curiosity are evaluated independently | Versioned film/walk/game/cooking activity corpus | 8.1 |
| Relationship uncertainty inversion | Fresh/low-maturity state is rendered as coldness, distrust, dislike or hostility | Affirmative personality baseline; unknown remains unknown; relationship is only subtle modulation | Fresh/established/damaged expression matrix and warmth false-negative rate | 8.1 |
| Origin/creator fabrication | Model invents biography, dismisses a current creator claim, or stores it without a schema | Origin-unknown authoritative facet; current claim remains attributed input; creator persistence explicitly deferred | Creator assertion/question fixtures, persistence and provenance inspection | 8.1; future schema gate |
| Surveillance-like user modeling | Broad or sensitive profile is inferred from ordinary dialogue | Closed Stage 9 vocabulary, bounded labels, same-counterparty roots, zero-proposal default and no demographics/health/vulnerability graph | Unknown/sensitive predicate rejection, payload and inspection review | 9 |
| Stale current-model claim | Old project/situation is presented as current | Deterministic owner TTL, pure read-time stale exclusion, append-only expiry revision | Before/at/after clock fixtures and planned-active-completed lifecycle | 9 |
| User report promoted to world truth | Counterparty statement is rendered as externally verified fact | World `explicit_fact` means explicitly reported, context keeps attribution/epistemic kind, no web/tool truth | Reported-vs-verified wording corpus and kind-preservation tests | 9 |
| User/world cross-person leakage | One counterparty's facts or situations enter another's context/export | Identity+opaque-counterparty keys on claims/evidence/queries, source ownership validation | Two-counterparty storage/context/export isolation | 9 |
| Response-regeneration runaway | A dialogue/self-consistency repair spends unbounded calls or applies affect/state twice | Closed ten-reason validator, maximum one extra call, same interaction/evidence/tentative affect, one canonical finalize | Clean one-call, attempt-count, all-reason, same-snapshot, failure/replay and single-transition tests | 8.1 |
| Post-response corruption or loss | Episode/index/semantic failure invalidates reply or exposes partial memory | Existing owner/UoW/idempotency decisions, serial in-process processor, drain on graceful exit, failure metadata and explicit retry/backfill | Blocked/failing worker, restart/idempotency and canonical-history tests | 7.5 |
| Export theft or secret inclusion | Full sensitive history compromise | No credentials, encryption policy, explicit export action, integrity manifest | Secret scanner, export schema test, access audit | Before user data export/release |
| Backup/restore divergence | “Restored” Satori is a different state | Identity ID/version verification, atomic staged import, round-trip equality | Restore drills and state hash comparison | Before v0.1 release |

## Trust boundaries

```text
Trusted: application policy, domain policy code, versioned schemas
Canonical but not instructional: identity/state serialized by owners
Untrusted: user input, memories, external content, provider output
Privileged boundary: persistence commit, export/import, external actions
```

Trust is enforced structurally, not only by prompt wording. Even a perfectly phrased injection cannot write state without typed proposal, owner policy, evidence and transaction audit.

## Review cadence

Revisit this table at each Stage exit. Any new data source, provider capability, autonomous mutation or external action must add threat cases and evals before implementation is accepted.

## Stage 3 review note

Implemented mitigations: structurally separate trusted policy/state from user role; no provider write/UoW reference; explicit no-memory/no-relationship/no-emotion capabilities; bounded input/context/output; non-streaming call with timeout; typed transport/schema failures; model/provider configuration outside domain/application; metadata-only logs; provider-swap and injection fixtures. Ollama default is loopback local-first, while a configured remote base URL is explicitly a data-egress boundary.

Residual risk: prompt policy alone cannot prove that an arbitrary model never emits an unsupported past claim or generic/over-agreeable answer. Stage 3 therefore makes only a request-contract guarantee and requires sampled real-model evaluation. Evidence-aware response grounding and persistent interaction recovery are gated with InteractionLog/Memory work; no claim is made that they already exist.

## Stage 4 review note

Implemented mitigations: exact dialogue is isolated in raw-history tables; hidden policy/context is not copied; completed pair/status is atomic before delivery; failed intake is explicit/retryable; source/version uniqueness prevents replay duplication; derived formation has its own failure-safe transaction; only exact user-message spans can evidence episode v1; generated assistant output, missing/foreign quotes and unsupported proposal shapes reject; every terminal formation has reason/version/provider metadata and audit; no stored history/memory enters generation context; provider-declared unavailable past refs fail before assistant commit; normal logs exclude message/reply/summary/quote content.

Residual risk at Stage 4 exit: the development DB is plaintext and has no
expiry/redaction/erasure/encryption workflow; exact quotes establish provenance reachability but
not complete summary entailment; a plain-text conversation provider may emit a past claim without
declaring it; formation v1 does not semantically deduplicate different interactions describing the
same event. Stage 6 later added consolidation, while production privacy/eval/security work remains
gated.

## Stage 5 review note

Implemented mitigations: embeddings are disposable and exact-space isolated; vector similarity is
only a relevance feature; current source interaction is excluded; threshold/top-k/payload bounds
limit disclosure; retrieved summaries live in a separate explicitly untrusted data envelope;
declared shared-past claims must cite supplied memory IDs; poisoned-memory, distractor, no-result,
space-mismatch and outage fixtures are deterministic. Queries, summaries, quotes and vectors stay
out of normal logs. Retrieval/index outage degrades to no memory and cannot roll back history or
canonical episodes.

Residual risk: a plain-text provider may omit claim declarations; mutable model tags do not prove
weight identity; exact scan is linear; semantic duplicates are not consolidated; the product has
no person model/security partition and therefore remains single-person only. Real-provider
quality/latency sampling and production privacy controls remain gates.

## Stage 6 review note

Implemented mitigations: semantic state has a distinct owner/UoW and closed user-only registry;
provider output is bounded untrusted proposal data; every accepted edge reaches exact Stage 4 user
evidence; explicit values require conservative lexical root support; inference requires two root
messages and interactions; assistant output, semantic recall and retrieved repetition cannot be
evidence. Confidence is deterministically capped and root-deduplicated. Source/version decisions,
aggregate versions, uniqueness constraints and concurrency tests prevent retry inflation.
Corrections/supersession/disputes preserve lineage and exclude uncertain claims from active recall.
Semantic context is separately untrusted and grounded only by supplied claim ID. Normal logs omit
claim values and quotes; upstream history/episode/index survives semantic outage.

Residual risk: lexical value presence is not full entailment of predicate, polarity, modality or
time; the recent evidence window may miss older support and may disclose unrelated recent local
episodes to the local provider; semantic recall depends on Stage 5 evidence-episode retrieval;
there is no multi-person partition, encryption, expiry, redaction, physical erasure or export
workflow. Real-model precision/skip/temporality evaluation and production privacy controls remain
mandatory gates.

## Stage 7 review note

Implemented mitigations: appraisal is a replaceable strict structured capability with no
repository/UoW access; current user and retrieved text remain untrusted data; source refs are a
subset of supplied interaction/memory/claim IDs; malformed/out-of-range/unknown dimensions and
confidence below `0.35` cannot mutate. `EmotionManager` alone derives personality-modulated
per-event capped deltas, clamps all ranges and applies slow one-way mood impulses. User distress is
not copied one-to-one, and relationship/user-emotion state is absent. Pure lazy half-life decay is
restart/read-frequency stable. Tentative expression state, canonical assistant completion,
transition and audit commit atomically; provider/generation/finalize failure cannot leave a partial
emotion, and completed replay cannot double-apply. Logs and transition/audit records omit raw
message, memory summary, semantic value, quote, prompt and CoT.

Residual risk: a stochastic model may miscalibrate otherwise schema-valid semantic appraisal or
produce theatrical prose despite expression policy; the deterministic layer limits damage but
does not prove human-perceived appropriateness. Concurrent different interactions may both spend
inference before one stale finalize conflicts. There is no automatic conflict regeneration,
longitudinal real-user calibration, encryption/erasure, multi-person partition, relationship
model, background observer or expression channel beyond text. These remain explicit later gates.

## Stage 7.5 review note

Implemented mitigations: exact CLI commands are parsed before untrusted input reaches the model;
normal output is quiet while structured logs remain in a separate sink; prompts/recent text/memory
are absent from timing diagnostics. Recent context reads only bounded canonical completed pairs and
does not masquerade as persistent memory. Shared HTTP/model residency is infrastructure-only and
finite. Canonical reply/affect commit still precedes display; post-response work uses existing
owners and cannot roll back history. Cancellation during generation creates no completed assistant
message, completed replay causes no appraisal/generation/affect/post-processing, and token streaming
is explicitly deferred.

Residual risk: the in-process queue is drained on graceful exit but is not a durable scheduler, so
process kill may leave missing derived work for explicit backfill. A plain-text provider can still
misread supplied recent turns or omit declared claim refs. Current appraisal/model generation is
slow and stochastic; model residency removes repeated load but cannot bound token speed. Debug JSON
contains operational IDs and timings and therefore still requires local access control. Safe
streaming, production retention/encryption, multi-person isolation and Stage 8 remain gated.

## Stage 7.6 review note

Implemented mitigations: identity/capability facts are derived from authoritative state and live
composition rather than model self-report; female/digital/embodiment/provider distinctions are
trusted and versioned; recent assistant text cannot outrank the late reminder; user/retrieved text
remains untrusted. Personality guidance is deterministic, source-linked and read-only. There is no
prompt-derived persistence, output phrase rewrite, hidden second personality, relationship state
or claim that digital affect equals human physiology. Prompt/request content remains absent from
production logs and dialogue storage.

Residual risk: a 4B stochastic model can still be verbose, overly grateful, metaphorical or miss a
voice preference on an individual sample. Prompt hierarchy cannot formally prove semantic
adherence. The mitigations prevent state corruption and make regressions observable, but stronger
behavioral reliability may require a separately benchmarked model or future constrained generation
design. Provider output is never accepted as authoritative self-state merely because it sounds
confident. Stage 8, human-equivalent consciousness claims and biological embodiment remain gated.

## Stage 7.6.1 review note

Implemented mitigations: complete capability truth no longer appears in every provider request;
deterministic contextual disclosure reduces accidental architecture, embodiment and relationship
leakage. Social Russian defaults to informal/feminine; numeric affect is reduced to a qualitative
expression hint; technical mode receives only an authoritative bounded fact list. Relationship
answers receive current epistemic truth without a persistent relationship object. Recent/model
text remains untrusted, production logs still omit prompts/context, and provider output is never
rewritten into apparent compliance.

Residual risk: deterministic cue selection is deliberately narrow and can choose general depth for
unusual paraphrases. Qwen 4B can ignore a stylistic instruction, use an emoji or produce awkward
wording in an individual sample. The eleven-dimension human rubric observes these semantic issues
but is not a mathematical conformance proof. A future model change must repeat multi-session evals;
it cannot justify Stage 8 state, an output filter or weaker canonical delivery.

## Stage 7.7 review note

Implemented mitigations: heavy local inference is serialized per provider origin so episode or
semantic generation cannot normally contend with newly queued foreground work. Background grace
gives the user-facing path a deterministic opportunity; bounded aging prevents permanent derived
starvation. The scheduler is infrastructure-only, carries no content and never changes owner,
transaction, retry or evidence decisions. It does not interrupt an HTTP call already in flight.

The categorical appraisal wire accepts only a closed vocabulary, bounded confidence and exact
supplied provenance handles. Infrastructure maps it to the unchanged continuous proposal;
`EmotionManager` still validates provenance/confidence and owns every delta/cap/bound/mood write.
No chain of thought, provider prose, raw prompt, user content or memory value appears in telemetry
or benchmark artifacts. Appraisal failure still produces no mutation, and completed replay still
bypasses every provider and transition path.

Residual risk: the 4B semantic corpus is 80%, with humor and explicit uncertainty
misclassifications. A background request already running can delay one foreground turn because
safe preemption is unproven. Long runs on 8 GB unified memory use swap and can reduce prompt/output
throughput; no reliable thermal-throttling flag was available. These limits do not justify an
appraisal skip gate, combined post-turn semantics, OS tuning, cloud provider, relationship state or
weaker canonical delivery.

The required character sample also found unsupported closeness wording and a false denial that
affect changes answers. Context schema v9 corrects the trusted late reminder rather than filtering
output or inventing relationship state. Provider adherence remains stochastic, so the full
three-session rubric—not a phrase rewrite—continues to be required after any prompt/model/runtime
change.

## Stage 8 review note

Implemented mitigations: relationship is counterparty-scoped and has one deterministic owner;
provider output is a closed categorical proposal with exact canonical handles and no dimensions.
Maturity ceilings, saturating updates, asymmetric impulses, event/session caps and canonical-root
dedup prevent instant trust/closeness, compliment farming, retry inflation and retrieved-memory or
assistant-output feedback. Ordered processing and optimistic versions prevent lost/out-of-order
updates. Provider/background failure cannot roll back a reply or affect, and old history is not
silently inferred by migration.

The conversation receives only a qualitative trusted projection; numeric axes and private IDs are
not shown in normal chat. High trust/affection explicitly grants no love, dependency, possession,
exclusivity, obedience, agreement, truth override or safety exception. Transition/audit telemetry
stores categories, handles, versions and timings without raw user text, prompts, memory or CoT.

Residual risk: relationship state is sensitive derived plaintext with no erasure/export/encryption
workflow. The configured counterparty ID is not authentication, so multi-user deployment remains
unsupported. The 4B classifier can misclassify a schema-valid event; v1 cannot independently prove
real-world reliability from one current message. The process-local queue cannot preempt an already
running Ollama call and requires explicit retry after a crash. No longitudinal human calibration,
silence-decay policy or historical backfill is accepted yet.

## Stage 8.1 review note

Accepted architecture: `DialogueCoherenceContext` and the primary-mode/facet plan are bounded
request projections with no persistence or mutation path. Context schema v11/behavior policy v9
make corrections, repeated turns and question choice explicit while preserving authoritative
self/affect/relationship sources. Recent assistant content remains untrusted about self. Unknown
relationship is not negative, embodiment limits do not erase curiosity, and current creator
attribution cannot become a durable fact or invented backstory.

The optional response validator is deterministic, runs before canonical finalize and recognizes
exactly ten typed reasons: changed-dialogue duplicate, routine reciprocal question after
correction, masculine self-reference, human/biological self claim, blanket affect denial, blanket
memory denial, current creator claim promoted to fact, invented origin backstory, blanket
prompt/policy denial and activity-interest false negative. Applicable checks require their
coherence/facet/activity/probe condition; this is not a general semantic or quality judge.

One reason permits one extra provider call only. Both attempts reuse one interaction, evidence
manifest and tentative affect; only one validated/grounded reply can commit. Normal turns still use
one provider call. Metadata may report reason/similarity/attempt/outcome without prompt, draft or
user content; non-duplicate failures use `self_consistency_violation_detected`. The retry timing is
generic `response_regeneration_ms`; the Boolean `duplicate_response_detected` remains
duplicate-specific. There is no output rewrite, judge model or validator state mutation.

Residual risk before Stage 8.1 acceptance: Qwen can still ignore a correction, choose a generic
question, recite policy, or express an authoritative facet awkwardly. Lexical similarity can miss
semantic duplicates; narrow lexical self-consistency checks can miss paraphrased contradictions or
need careful negation/quotation exclusions. A triggered retry adds foreground latency. These are
sampled-behavior and threshold-calibration risks, so the exact three-session dialogue, 30-turn run,
activity/relationship corpora, per-reason/normal-path evidence, before/after token/latency report
and full regression suites remain required. Stage 9 persistence is not an allowed workaround.

## Stage 13 review note

The accepted boundary is a separate identity-global `SatoriInclination` aggregate owned by
`PositionManager`; it does not extend epistemic `PositionKind` and shares no position evidence,
revision or policy record. Reflection V2 keeps the Stage 12 source allowlist but may persist an
immutable all-or-none attachment to the already committed owner-approved affective transition.
Only sources whose identity, interaction, user message, transition version and signal hash verify
may support an inclination. V1 runs remain readable and resumable and receive no historical
inclination backfill.

The provider proposes only labels, confidence, fixed source IDs and an optional exact target
version. The owner rejects declared/assigned user tastes, absent labels, ambiguous option matches,
duplicate roots/interactions/transitions/signatures and every derived or cyclic source, then owns
all diversity, signal, delta, cooldown, rolling-budget, confidence, stability and decay arithmetic.
Accepted mutation/evidence/revision/outcome/audit is atomic; a rejection stores only outcome and
audit. Inclinations can affect bounded current-turn cognition only and cannot enter affect,
retrieval, relationship, user/world formation or future reflection evidence.

Residual risk remains: the attached appraisal is model-proposed semantic evidence rather than
objective proof of experience, and exact Russian/English lexical matching is conservative and can
miss legitimate paraphrases. Sparse formation and false negatives are preferred to fabricated
autonomy; broader semantic matching requires a later evaluated policy. Stage 14 personality/value
mutation and Stage 19 proactivity remain locked.

## Stage 14 decision note

Accepted architecture: Reflection V3 has a separate `personality_evolution` purpose and consumed-
root namespace. It reuses only canonical Stage 12 leaf owners, excludes roots already accepted as
inclination evidence and removes affect attachments from source persistence, hash and provider
request. Current trait values, relationship, inclinations, generated text and prior reflection
artifacts are not proposal inputs. Conservative lexical filters run before inference and again at
the owner boundary.

The strict provider may propose one exact trait and direction with fixed-set citations and expected
version, never a delta or new state. `PersonalityManager` alone applies an exact `±0.005` after the
90-day structural gate, confidence/support checks, cooldown and independent activation/checkpoint/
rolling/lifetime path budgets. Every accepted version receives immutable provenance and a full-
vector checkpoint. Checkpoint approval and restore are explicit local append-only owner actions;
neither erases history nor refunds evolution spend. Values remain immutable.

Residual risk before Stage 14 acceptance: a schema-valid provider can still semantically
misclassify a non-assignment canonical event, and conservative lexical/near-duplicate clustering is
not complete entailment or paraphrase detection. Qualitative expression cues can be ignored or
overexpressed by a small language model. These risks require the paired alignment corpus,
longitudinal human review and before/after/restore real-model anchors; they do not justify larger
deltas, affect/relationship shortcuts, output rewriting or value mutation.
