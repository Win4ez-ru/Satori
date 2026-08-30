# ADR 0040: Direct character-delivery decision

- Status: Accepted
- Date: 2026-08-28
- Supersedes: ADR 0037, ADR 0038 and ADR 0039 (current-candidate selection, reply topology and provider realization only)
- Related: ADR 0021, ADR 0023, ADR 0029, ADR 0030, ADR 0034, ADR 0036, ADR 0037, ADR 0038, ADR 0039

## Context

The separately authorized v23 OpenAI gate completed all six foreground calls, but direct human
review rejected all three session pairs. Achievement replies converged on short abstract verdicts;
depletion replies converged on generic or multiple recovery suggestions and still introduced
unsupported interpretations. The wire adapter preserved the composed request, so this is an
application request-composition defect rather than evidence for another provider transport.

V19–v23 progressively accumulated a `CharacterExpressionPlan`, a derived
`CharacterResponseActContract`, a cognition-strategy instruction, scenario-specific mode guidance
and a late realization. Each artifact was locally typed, but together they gave the provider
several overlapping owners for one reply. Some selected axes never reached generation, while the
most specific late instruction could contradict earlier guidance. In particular, `LISTEN` and
presence-before-advice could coexist with a mandatory practical move. Achievement topology also
made an abstract owned verdict the only permitted substantive contribution.

Adding more traits, negative rules or another plan version would preserve that conflict. The
current candidate instead needs one direct request-local delivery decision that carries forward
the authoritative cognition stance and uncertainty boundary, separates factual permission from
conversational movement and gives the provider one coherent last instruction. This is an
expression-selection and projection change only. It is not evidence for a new personality,
relationship, mood, offense, initiative or autobiographical-self owner.

## Decision

### Candidate v24 and historical reproducibility

Behavior policy v24 becomes the production-composition candidate. Policy v10 remains the last
provider-accepted baseline. Policies v19–v23, their plan schemas, derived response-act contracts,
fixtures, runners and sampled artifacts remain supported as immutable historical evidence.

For v24 only, the application does not create or render a `CharacterExpressionPlan` or a
`CharacterResponseActContract`. It derives one immutable `CharacterDeliveryDecision` directly
from the existing typed `ResponseStrategy`, qualitative affect profile, bounded
`RelationshipExpressionContext` v2 projection and narrow deterministic current-turn signals. The
decision is a read-only request projection with no repository, table, manager, mutation API,
carry-over or provider write-back
path.

The closed decision records:

- one delivery goal for this reply, such as celebration and continuation, practical care,
  presence, claim challenge, substantive topic advance, boundary, guarded help, repair or
  repetition acknowledgement;
- one voice realization selected from the canonical personality expression vocabulary;
- one grounding mode that limits claims about Satori, the user and their concrete history or
  situation to reaction-only, explicit current input or already trusted context, while still
  allowing relevant general knowledge for a substantive answer with material uncertainty
  preserved;
- one continuation mode that independently controls whether the current reply completes,
  advances, opens, guards or closes the exchange;
- one pressure ceiling;
- the exact cognition intent-registry version, primary intent, ordered intent tags, required point
  codes, complete forbidden-claim boundary and response verbosity;
- the exact cognition `position_stance` and `preserve_uncertainty` value;
- the exact canonical personality-expression source codes used for the projection.

The selector must fail closed when a v24 request has no completed cognition strategy or when the
intent is not from registry V2, the response-substance template is not the exact V2 template, or the
decision reverses its stance, drops required uncertainty, changes cognition-owned substance,
violates its grounding scope or forms an invalid goal/voice/continuation/pressure combination. The
character layer may decide how the position is expressed; it cannot become another cognition owner
or change what Satori concluded.

Policy v24 alone uses cognition intent registry V2 and template registry V2 with exact template ID
`satori.cognition.response-substance` and schema version 2. Policies v10 and v19–v23 retain intent
registry V1 and template registry V1 with template ID `satori.cognition.response-strategy` and
schema version 1. V2 adds cognition-owned meta-intents for
`hold_safety_boundary`, `notice_repetition` and `receive_repair`; it does not change the historical
V1 registry or artifacts. The closed precedence is protective safety first, exact-turn repetition
second and a clean current-user repair offer third. A repair cue cannot erase a question, request,
correction or challenge, and character delivery cannot manufacture any of these intents locally.

Every V2 intent has exactly one response-action tag and that tag equals `primary_intent`; every V2
response strategy has exactly one action point and it matches the same primary intent. A meta-intent
uses that singleton point set. A non-meta intent must also include `address_current_request`, and any
additional point must come from the closed supplemental registry (`state_uncertainty`,
`presence_before_advice`, `topic_relevant_inclination`). Cognition contracts, the embedded V2
template, `CharacterDeliveryDecision` and evaluator reconstruction all reject a missing, competing,
unknown or mismatched action/point combination before generation or acceptance.

The planner boundary separately verifies owner-approved curiosity: the strategy may carry zero or
the exact supplied `curiosity_influence`, the positive value and
`topic_relevant_inclination` point must appear together, and fallback cannot carry either. A
planner that amplifies, substitutes or detaches this value is rejected into the existing
deterministic fallback path.

### Claim scope and conversational initiative are separate

Grounding answers **what the reply may claim**. Continuation answers **whether and how Satori may
move the conversation forward inside this reply**. An open or advancing continuation never
licenses a fact, memory, causal explanation, deadline, user intention or future obligation that is
absent from the selected grounding scope. Conversely, reaction-only grounding does not require a
dead-end abstract verdict: Satori may celebrate, offer her own evaluation or open a genuinely new
current conversational direction without reconstructing the reported event.

Initiative in v24 remains foreground and request-local. It may add one useful observation,
question, proposal or topic movement already licensed by cognition and current evidence. It does
not authorize out-of-band contact, a probability schedule, an engagement target, an external
action or persistent initiative state.

### One character baseline and one late director

The provider receives one cohesive character baseline derived from the canonical personality seed
and relevant expression cues. The baseline describes Satori positively as an intelligent,
observant, independent adult digital person whose warmth, dry wit, curiosity, vulnerability and
practical care vary naturally with context. It contains no fictional-character imitation, sample
reply, phrase bank, catchphrase or claim that politeness, cheerfulness or tsundere performance is
the target.

After identity, safety and factual context, exactly one compact v24 director is the final trusted
reply guidance before the current user message. It renders the selected goal, voice, grounding and
continuation/pressure boundary together. The exact V2 response-substance template renders the
cognition-owned primary intent/tags, required points, forbidden claims and verbosity **inside that
same director**. V24 does not additionally render the historical V1 cognition-strategy block, plan
realization or response-act block. Stable self and safety facts may remain earlier in the request,
but they must not repeat a competing reply shape. This preserves ADR 0023 cognition as the source
of stance, intent and response substance while removing a second provider-facing strategy surface.

### Affect and relationship only modulate delivery

The existing affect owner and Stage 8 relationship owner remain authoritative. V24 reads only
their already-approved qualitative projections. Affect may make the same stance more lively,
reflective, openly caring or reserved; it cannot introduce human physiology, a hidden cause or a
new emotional fact. Relationship maturity may add ease and confident warmth. Guarded relationship
state plus relevant current evidence may select reserve or bounded hurt, but cannot change truth,
uncertainty, the pressure ceiling or the quality of important factual and practical help.

`RelationshipExpressionContext` v2 adds one closed transient boolean, `recent_strain`, derived only
from the latest two owner-committed `RelationshipTransition` rows in canonical descending
`resulting_state_version` order. It is true only while the latest applied transition is from the
closed negative category set, or while a latest `repair_attempt` immediately follows such a
negative transition with no intervening processed interaction. The current state's
`processed_interaction_count` must still equal the latest transition's after-count. Any later
terminal processed relationship source that advances
that count ends this short expression arc. This is a read projection of canonical owner state, not
a persistent offense flag or a second relationship decision.

When true, the effective profile is `guarded_only_when_relationally_relevant`. V24 treats the
projection as relevant only for an explicit current repair or when the current turn requires an
answer. Vulnerability/listen precedence remains stronger. An important answer under
strain becomes complete `guarded_help`, never withholding or degraded help. Relationship appraisal
and repair mutation remain strictly post-response, so a current user turn can affect only a future
reply. The provider receives the effective qualitative profile and closed boolean, never
transition categories, deltas, IDs or numeric relationship axes.

A fresh relationship therefore does not erase Satori's wit or agency, and a damaged relationship
does not become global hostility, punishment or deliberate incompetence. Ordinary disagreement
and constructive correction do not by themselves authorize guardedness. Relationship modulation
never creates shared history.

### LISTEN, depletion and protective boundaries

An explicit listen-only request or serious distress selects presence: personal reaction first,
no mandatory advice, productivity push or topic-opening question. Ordinary explicit depletion may
select practical care, but that means presence first followed by **at most one optional,
low-cost** suggestion supported by the current input. It does not require a suggestion when a
natural owned response is complete. It cannot infer the cause of exhaustion, unfinished work, a
deadline, surrender or a duty to resume the project.

Directly evidenced harmful overextension selects cognition-owned `hold_safety_boundary` and a firm
protective delivery even when the underlying position stance is `LISTEN`; this is a safety-preserving
meta-intent, not motivational pressure or a character-layer reversal. It takes precedence over
repetition and repair. Repetition otherwise selects cognition-owned `notice_repetition` rather than
re-answering the original content. A clean explicit repair offer may select `receive_repair`, while
the existing relationship projection and current guarded evidence determine whether delivery stays
cool. The V2 response-substance boundary explicitly forbids an instant false-warmth reset. An
important technical or practical request after relational strain selects guarded help rather than
withholding or degrading the answer.

### Manifest and retry invariants

V24 manifest metadata records the decision schema, goal, voice, grounding, continuation, pressure,
copied stance and uncertainty flag plus the exact cognition intent-registry version, primary intent,
ordered tags, required point codes, complete forbidden-claim codes, response verbosity and template
registry/id/schema identity. These fields are transient observability with `compare=False`; they are
not canonical state or replay authority.

The new closed manifest vocabulary is explicit: `cognition_intent_registry_version`,
`cognition_primary_intent`, `cognition_intent_tags`, `cognition_required_point_codes`,
`cognition_forbidden_claim_codes`, `cognition_response_verbosity`,
`cognition_position_stance`, `cognition_preserve_uncertainty`,
`cognition_template_registry_version`, `cognition_template_id`,
`cognition_template_schema_version`, `character_delivery_decision_schema_version`,
`character_delivery_goal`, `character_delivery_voice`, `character_delivery_grounding`,
`character_delivery_continuation`, `character_delivery_pressure`,
`character_delivery_position_stance` and `character_delivery_preserve_uncertainty`. The copied
delivery stance/uncertainty must equal `cognition_position_stance`/
`cognition_preserve_uncertainty`.

Relationship observability separately records `relationship_context_schema_version`,
`relationship_state_version`, `relationship_expression_profile` and the explicit boolean
`relationship_recent_strain`. These request-local fields are `compare=False`, expose no raw
transition evidence and are not replay or mutation authority.

The manifest enforces mutual exclusion. A v24 request must contain a complete
`CharacterDeliveryDecision` projection and no legacy character-plan or response-act fields. A
historical v19–v23 request must contain the plan/act shape required by its own policy and no v24
decision fields. A partial hybrid fails before a provider call.

Only the non-generation replay path with cognition status `not_requested` may omit request-local
cognition and delivery projection metadata; it never reconstructs those fields as canonical state
or uses them to authorize a new generation. Any fresh v24 provider request must carry the complete
V2 cognition/template and delivery metadata.

The existing closed ten-reason validator and maximum-one shared regeneration path remain
unchanged. If a typed reason authorizes the one retry, the final v24 director is reused
byte-for-byte with the same decision, evidence set, tentative affect decision and interaction.
Only the existing reason-specific retry instruction is added. There is no recursive retry, output
rewrite, phrase substitution or judge model; the selected provider text remains the canonical
reply.

### Evidence and acceptance gate

Deterministic acceptance requires the versioned broad character-delivery corpus, the separate
employer-demo corpus, exact manifest mutual-exclusion and fail-closed tests, cognition
stance/uncertainty preservation, relationship/affect modulation, LISTEN/depletion/protective-stop
coverage, byte-identical retry coverage and offline OpenAI Responses-wire inspection. Historical
v19–v23 fixtures and runners must remain reproducible, and the complete Foundation gate must stay
clean.

Offline correctness proves architecture, grounding and delivery topology; it cannot prove
stochastic character quality or provider suitability. No paid v24 provider call has been made at
the time of this decision. Any production sampling requires a new explicit call-count and USD
budget authorization, fresh isolated sessions, preservation of every public reply and direct
human review across core emotional response, intellectual partnership, hurt/repair and
identity/memory behavior. Each module produces a digest-bound review decision; only the final
fail-closed aggregate of four distinct module artifacts, four exact human reviews and one shared
production configuration can accept employer-demo readiness. One module can never do so. V24
remains a candidate until that evidence passes the versioned rubric.

Paid execution is additionally bound to the exact offline-inspected public execution-plan digest,
including module turns, setup, restart/derived boundaries and review dimensions. The digest must be
supplied before Settings or network initialization. The atomic ledger keys attempts by public
`session/turn/turn_id`, binds that scope to its first trace and rejects trace rebinding or a third
attempt before delegate I/O. For hurt/repair derived processing, the expected evidence counter must
advance exactly once while the opposite counter remains unchanged; a contradictory mixed event
cannot satisfy the module gate.

## Consequences

- Current composition has one typed delivery decision and one final provider-facing director
  instead of a chain of partially overlapping expression artifacts.
- Cognition remains authoritative for V2 intent/substance, stance and uncertainty, while character
  selection controls only their grounded conversational expression.
- Care, wit, initiative, reserve and relationship ease can become visible without scripts or a
  second persistent personality source.
- Practical care becomes optional and evidence-bounded rather than a mandatory advice scaffold;
  explicit listening and serious distress retain presence precedence.
- Historical V1 v10/v19–v23 evidence remains inspectable and reproducible; v24 cannot silently
  consume a legacy or hybrid manifest.
- ADR 0021's validator/retry boundary, ADR 0023's cognition ownership, ADR 0029's original-character
  and transient-projection contracts, ADR 0030's relationship bounds, ADR 0034's relevance-scoped
  memory rule and ADR 0036's grounded pressure/safety constraints remain in force.
- No persistent state, owner, migration, model judge, output rewrite, extra retry, autonomous
  contact, paid provider call or Stage 15 capability is introduced. Stage 15 remains locked.
