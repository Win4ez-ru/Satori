# ADR 0048: Selective coverage in ordinary conversation

- Status: Accepted
- Date: 2026-09-13
- Supersedes: ADR-0047 only where disclosure completeness was interpreted as answering every
  casual question; canonical truth, ownership, explicit task obligations and historical policies remain intact

## Context

The user clarified that ordinary conversation is not a questionnaire. Similar check-ins can
receive one answer, and Satori may respond to one of several distinct questions. The desired
character should decide what to engage with without announcing omissions or promising a later
answer to each unanswered clause. The user delegates implementation of this behavior to the agent.

The diagnostic audit also found that “чем ты сейчас увлечена” was not recognized as an interest
topic. Recognition and response coverage are separate: making an interest available to selection
does not make speaking about that interest obligatory.

## Decision

In active, unaccepted V29, the current peer renderer treats personal disclosure facets as the
topics present and the authoritative limits on any claims made about those topics. Existing
field names (`required_facets`, `required_disclosure_facets`, `disclosure_facets`) remain for
historical compatibility; their presence is not an instruction to produce one answer per field
in casual conversation. No additional persistent state or owner is introduced.

The integrated V29 policy explicitly permits selecting one conversational question or combining
similar questions. The personal-facet line no longer commands coverage of all topics. Silence
about another casual question needs no apology, explanation, follow-up promise or background task.
No random omission rule, fixed quota, question-count heuristic, phrase bank or output rewrite is
added. The provider realizes this request-local conversational freedom within canonical state.

An explicit request to answer all points still requires complete task delivery. Material task
conditions, truthfulness, safety, identity/memory boundaries and available evidence remain binding.
Selective coverage cannot justify ignoring an explicit correction or pretending an unknown fact
is known. The existing cognition boundary remains intact; a general instruction to address the
current conversational request does not expand into exhaustive coverage of every social clause.

Interest recognition gains versioned direct current-interest forms and keeps the existing quote,
negation, hypothetical and third-person boundaries. V10–V28 request rendering remains unchanged.

## Verification and gate

Tests cover the exact mixed interest/affect prompt, nearby forms, excluded references, deduplicated
check-ins, the actual provider-facing selective instruction, and canonical commitment of a short
single-topic reply without a retry or rewrite. These tests establish permission and routing, not
whether a real reply is recognizable as Satori. Local samples are retained as technical evidence.

The planned V27/V29 target-model run is a small diagnostic with equivalent initial familiar state.
It is not the historical frozen A/B, a human score, character acceptance or a stronger-model trial.
The user may provide free-form impressions without filling another numerical rubric. Paid
execution needs the new concrete one-shot plan and its separately approved cost/call ceiling.
Checkpoint 14.3 Phase A remains active; no later phase or stage opens.
