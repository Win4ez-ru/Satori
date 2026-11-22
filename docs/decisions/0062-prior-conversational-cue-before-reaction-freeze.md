# ADR-0062: Prior conversational cue before reaction freeze

- Status: Accepted implementation design; V36 behavior remains unaccepted
- Date: 2026-10-06
- Authority: the user's Character continuation after complete V34/V35 target-API feedback
- Supersedes: ADR-0045 only for the versioned V36 ordinary HELP lead described below;
  ADR-0058 remains the affect-selection baseline and all persistent-owner boundaries remain
- Related: ADR-0051, ADR-0053, ADR-0058

## Evidence and bounded hypothesis

The completed target-API review has mixed relative preferences and rejects the common history's
joyful paraphrase. In pair 4, Satori previously asks a question, then answers a new technical task
without a visible transition. The previous question is present in the foreground request. This
does not establish lost history, offense, or a failure to complete the current task.

Current cognition and Character evidence do not represent uptake of a prior assistant move
before selection. Full semantic uptake would require a broader interpretation contract.
Extending the existing affect call could change affect sampling as well as dialogue interpretation;
a separate normal-turn call would add inference. Neither is needed for this narrower test.

V36 tests only **availability of a grounded previous conversational form influencing reaction
selection**. It does not implement or claim semantic answered/declined/redirected classification.
The foreground model still interprets that relationship inside the already selected response.
This is an explicitly weaker component hypothesis, not completion of the original semantic goal.

## Decision

Add pure `character_dialogue_cue.py`. It inspects only the last available complete canonical
recent turn. A balanced-quote-aware final question-like span produces an immutable
`PriorConversationalCue` schema 1: `question_candidate`, exact prior interaction/user/assistant
IDs, and Unicode code-point offsets `[start,end)`. The full span is at most 512 characters and
IDs are at most 128 characters. A newline cannot truncate an oversized span. Missing, unusable,
quoted or ambiguously quoted context yields no cue; older turns are never searched instead.

This is a structural observation. Rhetorical question form can also be a candidate. No cue
asserts a motive, curiosity, an expectation of an answer, intentional disregard, offense or a
pending thread. Offsets and IDs carry no raw text. Previous generated statements remain
untrusted about self and cannot create fresh affect or relationship evidence. No prior agency
manifest is reconstructed from today's state or added to storage.

### Reaction and compatibility

Policy V36 supplies the cue before agency freeze. Ordinary direct HELP may retain
`help/help/user_request/stay_on_topic` but choose `fused` lead, rather than `obligation_first`.
This eligibility uses an existing interest activation or established relationship, the existing
half-readiness cutoff from canonical mixed-affect projection, and absence of an active routine-
question correction. Safety, distress, listening, repetition, correction, repair, guardedness,
closure, creative/challenge work and fallback keep their prior precedence and discard the cue.
Requested help is never withheld when optional contextual expression lacks readiness.

Schema-2 agency licenses this exact new HELP/FUSED topology only with the supplementary
`prior_conversational_cue` reason and typed cue. Cue provenance stays separate from
`agency.source_refs`, which remain current cognition/evidence references. The assembler checks
exact cue recomputation against its supplied recent history before rendering. Manifest schema
18 reconstructs and validates the same decision and separately checks the prior user root's
presence in recent context. Historical schema-1 agency cannot carry the new reason or cue.

The existing selected precise-answer goal, factual bounds, completion and requested brevity
remain authoritative. One peer block permits an optional contextual transition within that same
answer. A silent yield is valid. No grievance block, repeated question, answer demand, additional
sentence, forced tease or visible emotion is required. Quoted/rhetorical content creates no
expectation. Explicit stop precludes returning to the prior question.

This is an inspectable selection change before prose, not only another generic prompt instruction.
Whether the chosen lead produces better character behavior remains a target-API human question.

### Stable interfaces and execution

V36 is opt-in; default runtime remains V35 pending evaluation. Historical policies through V35
retain their wire requests/native parameters and agency schema 1 / manifest schema 17.
New optional Python metadata fields default to `None`; generic dataclass serialization may
include that null field, while prior logical metadata and persistent schemas are unchanged.
V36 uses agency schema 2 and manifest schema 18; delivery/presence/cognition stay 5/3/2–3.

The existing max-one retry retains the same tentative affect, evidence, cue and reaction.
Completion fallback removes the cue. Replay omits transient agency/cue and makes no new call;
restart reconstructs context only from canonical eligible recent history. Failure adds no cue
state or pending work. Appraisal, model, provider adapter, caps, owner writes and permissions are
unchanged. No new normal-turn inference, migration, background job or output rewriting is added.

## Verification and decision gate

Freeze V35 before edits: 393 exact successful request/native-parameter records and the inherited
assembly failure. Use controlled prior-question/no-question contrasts with identical current
task, affect and relationship. Check selected lead before rendering, complete cognition parity,
readiness/relationship controls, answer/decline/shift without a false uptake claim, quotes,
rhetorical form, unsupported context, protected routes, forged provenance, retry, replay,
restart and failure. Version the public corpus and preserve all failed development checks.

Run the required rebuilt noneditable Foundation toolchain in an immutable V35 workspace plus
this Character delta. Preserve live Foundations changes and report the isolated gate's scope.
Diagnose actual foreground source admission separately if an input surface changes; this design
adds no new raw provider input beyond already-admitted canonical recent dialogue.

Ollama output may establish local runtime compatibility, not target character quality. A new
bounded target-API comparison needs a fresh inspect-first frozen plan and explicit spending grant;
the V34/V35 claim is consumed. Freeze small human feedback before mapping disclosure, retain
failed/missing cells, and do not infer a version winner or character acceptance from selection
metadata. Semantic uptake and recognition of the previous own move remain separate open gates.

## Parallel workstream

Character stays in Checkpoint 14.3 Phase A. No persistent owner, history/recovery schema,
source-policy contract or Foundation roadmap slice changes. Stage 15 and persistent unfinished
threads remain closed. This iteration cannot accept or block the independently moving
Foundations workstream.
