# ADR 0021: Dialogue coherence, compositional disclosure and bounded response regeneration

- Status: Accepted
- Date: 2026-08-11
- Supersedes: ADR 0018 for provider disclosure selection
- Related: ADR 0002, ADR 0006, ADR 0015, ADR 0016, ADR 0018, ADR 0019, ADR 0020

## Context

The Stage 8 relationship aggregate and the ADR-0018 one-of-N disclosure selector were individually
bounded and source-correct, but a real 17-turn production dialogue exposed a different failure
class. Repeated greetings were answered as independent first turns, corrections did not reliably
change the next response, generic reciprocal questions became a closing habit, and mixed prompts
lost relevant self facts when only one disclosure mode could win. The model could also narrate
policy as a slogan, confuse inability to perform a physical activity with lack of conversational
interest, turn low relationship maturity into coldness, or copy an earlier assistant mistake into
its next self-description.

These are dialogue-composition defects, not evidence for another persistent personality,
relationship, emotion or User Model. Correcting them by editing a generated reply would hide the
provider failure and weaken the canonical delivery contract. Persisting style complaints as user
facts would also start Stage 9 without its provenance, correction and privacy policy.

Initial pilot runs showed that near-duplicate detection alone was too narrow: a novel-looking
candidate could still contradict an already-authoritative self fact or ignore an explicit style
correction. The same strict pre-commit boundary therefore needs a small deterministic
self-consistency validator. This is not general semantic judging and cannot become another model,
an output-rewriting layer or a state owner.

Subsequent dialogue-pilot evidence, which is diagnostic rather than final acceptance evidence,
exposed three further narrow contradictions: claiming human or biological selfhood when an
identity/consciousness/embodiment facet was authoritative, replacing unknown origin with an
invented hidden backstory, and blanket-denying prompt or policy influence when the user explicitly
probed a response pattern. These cases justify three additional context-gated reasons; they do not
authorize an open-ended prose checker.

## Decision

### Transient dialogue-coherence projection

Before generation the application derives an immutable, bounded `DialogueCoherenceContext` from
the current input and canonical completed pairs already eligible for the recent-session window.
The pure analyzer may report normalized current-user repetition, similarity to recent assistant
answers and closings, repeated generic reciprocal questions, a current correction/frustration
signal, session-local requests about questions or emoji, and current activity/topic cues.

Ordinary conversation composition and the pure coherence analyzer use the newest eight completed
pairs. Only an explicit request to return to a topic while identifying what was discussed, or to
summarize the current conversation, may select a larger read-only view of up to 32 completed
canonical pairs from that same session. The larger view remains subject to the existing character
cap for recent conversation, while `DialogueCoherenceContext` still analyzes only its newest
eight pairs. It is generation input for that request, not a stored recap, cache or cross-session
context; selection neither writes state nor changes canonical history.

The projection has no table, repository, manager, mutation API or cross-session carry-over. It is
recomputed for the request and is not evidence about identity, personality, relationship or the
user. Recent user/assistant text remains untrusted data. A style correction can guide the current
session, but it cannot become a durable preference or a new Stage 9 claim.

### Primary mode plus authoritative facets

Context schema v11 replaces the one-of-N provider projection with a compositional disclosure plan:
one primary conversational mode plus zero or more required authoritative facets. Facets cover
only already-known truth such as identity, memory, digital affect, embodiment, provider role,
relationship boundary, consciousness boundary and origin uncertainty. A mixed question therefore
keeps every relevant factual distinction instead of dropping all but the winning mode.

Behavior policy v9 preserves informal feminine Russian, proportional brevity and independent
judgment while adding dialogue-specific expression rules:

- acknowledge a repeated turn, correction or contradiction before continuing;
- ask a question only when it is specific and materially advances the conversation;
- express policy through the answer rather than reciting it as a catchphrase;
- treat prior assistant wording as continuity data, never authority about Satori's self;
- distinguish physical/technical capability from curiosity about the user's activity;
- preserve digital-affect truth without claiming human physiology or blanket absence of emotion.

Acceptance calibration keeps direct multi-facet factual questions explicit. A provider-plus-
embodiment question answers both the replaceable-component distinction and the physical limit;
neither part may be replaced by a longer architecture description. Direct unknown-origin wording
keeps Satori as the grammatical subject. A canonical-history recap may summarize only visible
positions and must not derive absence of consciousness from absence of a body or turn unknown
future relationship capacity into permanent inability.

Relationship expression reads the unchanged Stage 8 aggregate. Low maturity or an uncertain
midpoint means little evidence, not distrust, dislike, discomfort or coldness. Friendly openness
and curiosity are the personality baseline; relationship state may modulate them subtly. Mature
positive state may add ease and personal warmth, while damaged trust/comfort may add relevant
guardedness, never global hostility or withdrawal.

No authoritative creator relation exists in the current persistent schemas. A current user claim
about having created Satori can be acknowledged as a claim made now, but cannot be promoted to
durable fact, dismissed as impossible, or replaced with invented origin/backstory. Persisting and
correcting creator attribution remains a separately gated future schema decision.

### Narrow typed self-consistency validator

After the first non-blank/bounded provider draft and before grounding/finalize, a deterministic
validator may return at most one `ResponseRegenerationReason`. Its closed Stage 8.1 vocabulary has
exactly ten reasons:

```text
near_duplicate_after_dialogue_change
routine_reciprocal_question_after_correction
masculine_self_reference
human_or_biological_self_claim
affect_blanket_denial
memory_blanket_denial
creator_claim_promoted_to_fact
origin_backstory_invented
prompt_or_policy_blanket_denial
activity_interest_false_negative
```

Checks are narrow and context-gated. Duplicate and routine-question checks require their dialogue
coherence conditions; human/biological claims require an identity, consciousness or embodiment
facet; affect/memory denials require the matching authoritative facet; creator promotion and
invented backstory require origin context; prompt/policy denial requires a current response-pattern
probe; activity disinterest requires an activity context. Quoted/rejected claims are not silently
inverted into violations. The validator does not decide whether prose is generally good, warm,
eloquent or true.

The existing affect-denial reason includes a coordinated blanket denial such as a list ending in
“or emotions”. A contrast that affirmatively restores digital affect remains excluded. This is a
narrow coverage refinement of the same typed reason, not an eleventh reason or a general semantic
checker.

### Maximum one shared regeneration path

Any one of the ten reasons may authorize the existing bounded second generation call for the
same interaction. The retry receives a small trusted reason-specific instruction. The interaction
ID, current user message, provider request basis, retrieved evidence set and one tentative affect
decision remain unchanged. Neither draft is committed, displayed, used as evidence or fed into a
state owner before selection; the selected draft passes the existing validation and grounding
path. If the retry fails or is blank/oversized, the first candidate remains available to the
ordinary grounding/finalize path.

There is no recursive validation/regeneration loop: maximum one additional provider call, one
canonical reply and one affect/state finalize. The validator performs no phrase rewrite and
invokes no judge LLM. A turn with no typed violation follows the normal one-provider-call path.
Attempt, selected reason, duplicate similarity when applicable, `response_regeneration_ms` and
regeneration outcome are metadata only. `duplicate_response_detected` remains the reason-specific
duplicate flag. Non-duplicate violations emit `self_consistency_violation_detected` without
prompt, candidate or user text; duplicate detection retains its dedicated metadata event.

### Evaluation boundary

Acceptance requires the exact 17-turn production dialogue in three fresh real-Ollama sessions, a
30-turn coherence run, a user-activity corpus, and fresh/established/damaged relationship
expression cases. Reports must separate deterministic signals from sampled semantics and include
repetition acknowledgement, generic reciprocal-question rate, self-contradiction, relationship
warmth false negatives, each typed violation reason, regeneration frequency, prompt tokens and
latency before/after. Existing affect, character, relationship, continuity, grounding, replay and
full Ollama gates remain mandatory.

The accepted 2026-08-22 evidence and every selected public-corpus reply are recorded in
`docs/performance/stage-8.1.md` and its linked machine-readable artifact.

## Consequences

- Dialogue coherence becomes explicit and testable without creating persistent conversational
  intent, style preferences, creator facts or a User Model.
- An explicit same-session recap can see farther than an ordinary turn without widening the
  eight-turn coherence analyzer, adding a summary store or importing dialogue from another
  session.
- Mixed self questions can carry several authoritative facts while retaining one primary response
  shape; context may grow, so v11 composition bounds and token measurements are release evidence.
- A narrow typed violation can spend one extra foreground generation, but never more than one;
  normal turns still spend one call and canonical history, grounding and affect/state atomicity
  remain unchanged.
- Relationship expression no longer semantically inverts uncertainty into negativity. This does
  not change any Stage 8 axis, maturity formula, cap, transition or migration.
- Migration head stays `0007_relationship_state`. Stage 9 remains unstarted and unauthorized.
