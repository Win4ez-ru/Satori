# ADR 0036: Owned contribution and bounded motivational posture

- Status: Accepted
- Date: 2026-08-27
- Supersedes: ADR 0035 (provider rendering and current-turn contribution/motivation selection only)
- Related: ADR 0021, ADR 0023, ADR 0029, ADR 0030, ADR 0031, ADR 0032, ADR 0035

## Context

The policy-v19 OpenAI gate proved that a single late character realization can make wit and owned
concern visible, but all three required session pairs still failed human review. Several replies
used most of their content to restate the user's completion/depletion contrast. Others converted
unknown causes, remaining work or project consequences into facts. Adding more personality prose
would not repair that structural failure: the provider needs a typed decision about what Satori
adds after, at most, a brief acknowledgement.

The user's target also includes practical care and motivation. Satori may suggest a useful action
when it is grounded and proportionate, but ordinary depletion does not prove why the user feels
that way, whether the project is unfinished or whether work should continue. Serious distress or
an explicit request to be heard must not be converted into productivity pressure. Conversely,
explicitly harmful overextension may require a firm protective stop even when the same message is
repeated.

These choices concern one reply only. They are not a new personality source, a persistent user
preference, a relationship reward or a license to optimize engagement.

## Decision

Behavior policy v20 becomes the production-composition candidate. Policy v10 remains the last
provider-accepted baseline. Policy v19 and its schema-v2 runners, fixtures and artifacts remain
immutable historical evidence with a rejected provider-fit verdict.

`CharacterExpressionPlan` gains schema v3 while schema v2 remains supported for historical v19
evaluation. Policy v20 requires a complete schema-v3 plan. The plan remains frozen, request-local
and provider-safe and adds three closed axes:

- `contribution_mode`: the substantive move Satori adds beyond minimal acknowledgement;
- `motivational_posture`: whether the current evidence permits a supportive push, playful
  challenge, explicitly requested mobilization or protective stop;
- `pressure_level`: a hard upper bound of none, gentle, moderate or firm.

In v20, the existing `semantic_move` is a factual and continuity anchor. It constrains which facts
may be used but is not itself the substantive contribution. The new contribution axis prevents a
request to “connect the contrast” from becoming an instruction to paraphrase it.

The planner enforces valid posture combinations rather than allowing arbitrary enum products:

- no motivational posture requires no pressure;
- supportive push requires grounded direction and gentle pressure;
- playful challenge requires playful reframe and at most moderate pressure;
- explicitly requested firm mobilization requires grounded direction and moderate pressure;
- protective stop requires a protective boundary and firm pressure.

Current-turn evidence is derived by narrow deterministic, negation- and quotation-aware checks.
It is not persisted and never becomes evidence about a durable user preference. Selection follows
these precedence rules:

1. technical identity and acknowledgement/repair retain their exact factual purpose;
2. immediate repetition remains visible as repetition rather than being answered again;
3. explicitly harmful overextension permits a protective stop, including on a repeated turn;
4. an explicit listen-only request or serious distress disables ordinary motivation and wit;
5. a direct motivation request may select moderate mobilization within that explicit request;
6. otherwise the canonical completion/depletion contrast permits one gentle recovery step;
7. explicit task retreat, ordinary achievement, uncertainty and collaboration receive their own
   contribution.

The recovery license does not prove a hidden cause, surrender, deadline, remaining workload or
project consequence. Continuing the project may be mentioned only when explicit current evidence
establishes unfinished work. Relationship familiarity may change ease of expression but cannot
raise the pressure ceiling.

Exactly one v20 realization remains the last trusted character guidance before the current user
turn. It renders the selected contribution first and the factual anchor separately, then applies
the existing reaction, wit, care, openness, initiative and relationship bounds. Ordinary target
replies are limited to at most two short complete sentences; the v20 achievement/listen-sensitive
paths use a 128-token visible cap. It contains no sample reply, phrase bank, catchphrase or enum
label. Provider output remains canonical and unrewritten; Ollama length-limited output fails
before canonical commit.

The context manifest observes the three new axes only through `compare=False` fields. A max-one
typed consistency retry preserves the exact same final realization, contribution, posture and
pressure ceiling. No validator reason, second retry, LLM judge, persistence adapter, migration or
state owner is added.

Deterministic acceptance uses `checkpoint142_character_expression_v3.json`. Sampled human review
uses the separate `checkpoint142_character_sampling_v2.json` three-by-two corpus. The v20 provider
gate requires separate explicit authorization and must preserve every public sampled reply rather
than tune the implementation to one preferred sentence.

## Consequences

- Acknowledgement and Satori's own contribution are independently inspectable decisions.
- Practical care can motivate without inventing future work or equating human value with output.
- Negated, hypothetical or quoted cues fail closed and cannot authorize pressure.
- Severe vulnerability disables ordinary motivational pressure; a firm posture is reserved for a
  directly evidenced protective stop.
- Schema v2 remains reproducible for v19 while policy v20 cannot silently receive a partial v3
  plan.
- Offline correctness establishes architecture and safety boundaries, not provider character fit.
  V20 remains a candidate until its separately authorized human-reviewed provider gate passes.
- A technically complete local run may still receive a rejected provider-fit verdict. The final
  free 3 × 2 Qwen run completed all turns but failed human review at 0/3 pairs; sampled failures do
  not authorize changing the typed axes into scripts.
- Stage 15, persistent initiative, autonomous contact and all personality, relationship, mood,
  emotion, memory and user-model ownership contracts remain unchanged.
