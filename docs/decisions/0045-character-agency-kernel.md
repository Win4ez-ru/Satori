# ADR 0045: Character agency kernel

- Status: Accepted
- Date: 2026-08-31
- Supersedes: ADR 0043 only for active current-turn selection and provider rendering; historical
  V27 policy, fixtures, runners, reports, reviews and digests remain immutable evidence
- Related: ADR 0002, ADR 0003, ADR 0010, ADR 0015, ADR 0020, ADR 0023, ADR 0026,
  ADR 0027, ADR 0040, ADR 0041, ADR 0042, ADR 0043, ADR 0044

## Context

The exact V27 OpenAI attempt-2 sample was technically valid and factually disciplined, but direct
human-only review rejected it. Grounding passed 24/24 and completeness 24/24, while recognizable
Satori presence passed only 6/24 and natural delivery 9/24; cross-session review was `FFTFTF`.
The replies usually behaved like a capable assistant with a light character treatment, not like an
independent character who sometimes chooses to help.

ADR 0043 correctly moved live-state selection before prose, but its active candidate still began
from the service obligation of answering the request and then selected a delivery movement. That
ordering is insufficient for the product constitution. A credible Satori must first make one
bounded current-turn choice about how she relates to the conversation: whether to contribute a
view, challenge, explore, tease, connect, stay restrained, close the topic or simply answer. The
choice must preserve cognition-owned substance and grounding, but usefulness cannot be the only
source of the visible movement.

This does not justify a second persistent personality source, an invented biography, a database of
free-form desires or an inner-monologue loop. Stage 13 already owns durable preferences and
interests through `SatoriInclination`; Stage 15 owns future autobiographical self, Stage 17 owns
future unfinished threads and Stage 19 owns future out-of-band proactivity. Opening any of those
stages to repair current-turn expression would conceal the actual boundary defect.

## Decision

### Checkpoint 14.3 Phase A is request-local agency

Checkpoint 14.3 Phase A adds one typed **character agency decision** before provider rendering. It
is a frozen request-local projection, not persistent self and not evidence of subjective
consciousness. It answers only the operational question:

> Given the authoritative current state and prepared cognition intake, what does Satori choose to
> do in this reply before compatibility with the completed cognition contract is enforced?

The current-turn path becomes:

```text
prepared cognition intake (perception, need mix and retrieval plan)
  + immutable personality and value projections
  + current affect and relationship projections
  + bounded owner-approved Satori positions and inclinations when available
  + the immediate conversational situation
→ one versioned request-local character agency decision, including an explicit `none` outcome
→ `SafeCognitionPipeline.complete` derives the authoritative stance, intent, required points,
  uncertainty, safety and forbidden-claim boundary from the same prepared intake and evidence set
→ delivery compatibility validates and merges that complete cognition contract with the frozen
  agency decision
→ one bounded provider-facing realization
→ unchanged foreground provider and one canonical reply
```

The application policy creates the decision before cognition completion and language generation.
The kernel does not read the later completed stance, intent, required points or forbidden claims;
those remain outputs of cognition completion and are enforced by the compatibility merge. The
status boundary is fail-closed: if completion falls back after an applied intake, the preselected
move is replaced before rendering by the sole licensed conservative `none/respond/stop` agency
shape with `cognition_fallback` provenance. Delivery selection and manifest schema 17 require
exact completed-cognition/agency status parity, while cognition-owned safety, repetition and other
required response points remain authoritative. The
foreground provider may realize the resulting movement but cannot select persistent state, change
either input, create another owner or write the result back into it. Phase A adds no provider call,
output rewriter, judge model or hidden retry. A validator retry, when already licensed by the
existing conversation contract, reuses the byte-identical agency decision and authoritative input
set.

The closed decision vocabulary may distinguish such current-turn movements as direct response,
owned contribution, exploration, challenge, playful edge, connection, restraint and closure. The
exact versioned vocabulary belongs to the typed contract and evaluation corpus, not to free-form
provider prose. One turn selects at most one primary movement and may select `none`; policy must
not force visible initiative, wit, care or disagreement into every reply.

### Existing owners remain authoritative

The agency policy receives immutable read projections only:

- cognition owns the prepared perception/need/retrieval intake and, after agency selection, the
  completed required substance, stance, uncertainty, forbidden claims and whether a direct answer
  is required;
- `PersonalityManager` and the value boundary own slow global dispositions and constraints;
- `EmotionManager` owns current affect and mood;
- `RelationshipManager` owns person-specific relationship state;
- `PositionManager` owns durable Satori positions and `SatoriInclination` records;
- interaction, memory and user/world owners retain their existing truth and provenance scopes.

No `AgencyState`, `Desire`, `ConversationalGoal` or equivalent persistent aggregate, repository,
table, migration or writer is introduced. The agency decision expires with the foreground
interaction. It is not loaded on the next turn as canonical self, included in reflection sources,
converted into memory or treated as evidence for personality, positions, inclinations,
relationship or affect. Generated assistant prose remains ineligible as fresh self evidence.

The selected movement may influence expression only. It cannot invent a fact, broaden a memory
license, suppress safety, reverse a canonical position, manufacture an inclination, omit required
help or turn uncertainty into certainty. Values and traits license an evaluative orientation, not
a claim about past experience. A position or inclination may influence a move only through its
owner-produced bounded projection and exact provenance scope.

### Phase A adds no seed interests

The canonical activation seed and `InitialSelfSnapshot` remain unchanged. Phase A does not add
topic interests, favorite activities, tastes or hobbies to seed JSON, prompt text or persistence.
Existing traits and values may support broad present-tense orientations such as curiosity,
independent judgment, analytical engagement or creative exploration, but they do not prove that
Satori has practiced an activity, studied a subject, discussed it before or developed a favorite.

When no owner-approved `SatoriInclination` exists, the agency decision must preserve that absence.
It may choose how to engage with the current topic but cannot describe a specific durable taste.
When an inclination exists, `PositionManager` remains its sole writer and ADR 0026 remains the
formation, evidence, decay and persistence authority.

Adding activation- or adoption-seeded inclinations would require a separate decision that defines
origin-aware confidence, stability, decay, revisions, provenance, migration and non-destructive
adoption for existing identities. It is explicitly outside Phase A. A later seed decision may not
create a second inclination store or silently insert subjective state during migration.

### Current-turn initiative is not Stage 17 or Stage 19 proactivity

Phase A may choose one adjacent contribution or bounded topic movement inside the reply already
requested by the user. It cannot:

- create or retain an unfinished question, promise, waiting result or future commitment;
- schedule work, contact the user later or initiate an out-of-band message;
- derive a notification, reminder, quiet-hours decision or engagement objective;
- optimize for session length, dependency, reply probability or preventing the user from leaving.

Those capabilities remain locked behind the Stage 17 unfinished-thread owner and the Stage 19
consent, scheduling and proactivity policy. Request-local initiative is complete when the current
interaction commits or fails.

### Phase A is not autobiographical self

The agency decision cannot claim a childhood, creator, off-screen activity, prior private thought,
continuous awareness, lived hobby or personal history. Activation remains the known beginning of
Satori's life. Current personality, values, affect, relationship, positions and inclinations may be
expressed in first person within their evidence scopes; they do not constitute the Stage 15 self
model or autobiographical narrative.

Stage 15 remains locked. A future autobiographical claim requires its own source-grounded owner,
significance policy and revision lineage. Character recognizability in one reply is not a reason to
pre-populate that future state.

### Determinism, observability and historical isolation

For identical canonical inputs, policy version and retry identity, the agency decision is stable.
Variation in provider prose is not allowed to resample the underlying movement. State
counterfactuals must demonstrate that changing an authoritative input can change or bound the
decision before rendering, while an unrelated state change does not.

Fresh policy-v28 generation uses manifest schema 17 to record only typed metadata needed to
reproduce and audit selection: agency decision schema/policy version, primary movement, selected
owner-state IDs and bounded reason codes. Historical policies through V27 retain manifest schema
16 and cannot validate against, inherit or replay V28 agency authority. Non-generation replay may
omit the transient agency/delivery/presence fields but cannot synthesize them. No manifest stores a
hidden chain of thought, free-form internal monologue, private prompt, candidate prose or copied
user content. Normal logs remain metadata-only.

ADR 0043 remains authoritative for the immutable V27 historical architecture and evidence. Phase A
does not modify or reinterpret V27 fixtures, production runners, authorization claims, safe reports,
human reviews, sample digest or provider-cost evidence. A successor policy and corpus must use new
identities and versions; historical V27 validation continues against its embedded source
fingerprints rather than the Checkpoint 14.3 tree.

### Evaluation boundary

Deterministic acceptance must prove:

- the decision is selected from prepared cognition intake before cognition completion; the later
  compatibility merge preserves the complete cognition contract before any provider prose;
- all persistent owners and snapshots are byte-for-byte unchanged by selection, success, failure,
  retry, replay and restart;
- `none`, direct answer, owned contribution, disagreement, care, playful engagement, restraint and
  closure are reachable from appropriate state/situation combinations without phrase scripts;
- absent positions, inclinations, memories and relationship evidence remain absent rather than
  being synthesized by the agency layer;
- relationship closeness may license greater ease but cannot make initiative mandatory;
- generated prose, assistant history and the decision itself never become fresh long-term
  evidence;
- provider replacement may change wording but not the authoritative pre-generation decision.

Deterministic correctness does not establish character quality. A separately authorized immutable
provider sample with direct human-only review must evaluate recognizable Satori presence,
naturalness, independent contribution, non-assistant closure, grounding and cross-session
variation. No provider call is authorized by this ADR.

The first frozen comparison under
`sha256:f21bbced0317bf1806712c70717f8f3f36fea7b51d784d2f8684f78d6914a70c` consumed its one-shot
identity but stopped before OpenAI because the frozen first-turn cap `64` did not match production
cap `48`. It is immutable `INCONCLUSIVE / NOT ACCEPTED` evidence with zero OpenAI calls/tokens/cost.

The corrected comparison is frozen under
`sha256:8e6dc91d173ed83c274ef1ff0327721728630dd26c4da3f6bf81d7f4a05b5f83`. Each cell has three fresh
six-turn replicas, 18 mandatory/24 maximum calls and a USD 0.15 ceiling; the complete pair has 36
mandatory/48 maximum calls and a USD 0.30 ceiling. Both provider requests use runtime character
context schema 16, while safe manifests keep V27 schema 16 and V28 schema 17 separate. The exact
cap vector is `[48, 80, 96, 96, 112, 384]`. Blind replies use a balanced runtime-random assignment
bound into the sample digest by a private nonce; no treatment identity, assignment or agency
metadata appears in phase 1. That complete review is written as a `0600` artifact before minimized
treatment evidence can be constructed. Acceptance is then computed under exact hard and threshold
gates and written once. The corrected fixed one-shot identity is
`satori.checkpoint143.openai.v27-v28.ab2.2026-08-31.one-shot`; recording it did not itself authorize
paid execution.

That exact attempt-2 identity was later authorized and is now consumed. Control S1T1 completed on
one OpenAI call with 786 input and 29 output tokens, cache reads/writes `0/0` and exact cost USD
0.001920. Control S1T2 was rejected before the delegate because the inherited evaluator ledger
required temperature `0.3` for every request while the real production request used `0.0`; the V28
cell made zero calls. The failure produced no sample digest, blind assignment or blind/phase-1/
phase-2/final-review artifact. Its `0600` report contains no credential, raw/private prompt,
provider message or private service context. The result is consumed `FAILED / INCONCLUSIVE / NOT
ACCEPTED` evaluator evidence and cannot satisfy any Checkpoint 14.3 provider or human gate.

Attempt 3 consumed its claim and all 18 V27 control calls (USD 0.050838), then failed on a stale
terminal temperature assertion; V28 made zero calls and no blind/review artifact exists. The
isolated successor attempt 4 is inspect-only under digest
`sha256:cbea4634e2f108532d21eb0022ca1295e08655c07a894f315ac9b7945f791153` and one-shot ID
`satori.checkpoint143.openai.v27-v28.ab4.2026-08-31.one-shot`. One typed per-turn request contract
now binds turn identity, temperature and cap through admission and terminal validation, including
post-replica checks. Fresh paths are absent and inspection made zero provider calls. These values
do not authorize paid execution.

## Consequences

- The active architecture now starts from one bounded Satori-owned current-turn choice rather than
  treating character as styling applied after an assistant answer is already selected.
- Cognition, truth, safety and every persistent writer boundary remain intact.
- Character agency is inspectable and causally testable without becoming another persistent self.
- Satori may sometimes be useful, playful, challenging, restrained or personally engaged, while
  `none` prevents a compulsory personality performance.
- Specific starting interests and autobiographical history remain absent until their proper owner
  and provenance decisions are explicitly approved.
- No migration, new table, seed mutation, provider call, background loop, phrase bank, output
  rewriting, judge model, Stage 15 feature, Stage 17 thread or Stage 19 proactivity is introduced.
- Historical V27 remains reproducible rejected evidence and is not retroactively upgraded by this
  decision.

## Alternatives rejected

- Persisting a generic desire, motivation or agency object independently of existing state owners.
- Adding favorite topics or hobbies directly to a prompt or silently changing the V1 activation
  seed under its existing provenance identity.
- Treating values, traits or provider prose as proof of specific lived interests or biography.
- Opening Stage 15, Stage 17 or Stage 19 to compensate for a current-turn selection defect.
- Asking a second model call to invent an inner intention before every response.
- Rewriting generated prose or using another model as a character judge.
- Requiring a joke, question, tease, disagreement or initiative move on every turn.
