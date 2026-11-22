# ADR 0034: Relevance-scoped memory and literal character delivery

- Status: Accepted
- Date: 2026-08-27
- Supersedes: ADR 0033 (provider-rendering and no-recall wording only)
- Related: ADR 0005, ADR 0013, ADR 0021, ADR 0029, ADR 0030, ADR 0033

## Context

The first three-session local production sample for policy v17 selected the correct typed
character plans but failed human review at 0/3 complete pairs. Two responses reached the output
limit, several used malformed metaphors, and one fresh-session achievement invented a previous
exchange: `Вспомнила, как ты впервые сказал...`.

The false memory did not come from retrieved evidence. Production composition included
`NO_RELEVANT_MEMORY` on the ordinary achievement turn, and the v16/v17 provider guidance taught
natural first-person absence wording such as `не вспомнила` even when memory was not the subject.
The small local model treated that conditional wording as reply content. The v17 realization brief
also remained abstract enough for the model to turn register guidance into decorative metaphors.

The failure must not be repaired with output rewriting, a scripted reply or another persistent
style source. Missing recall must remain explicit when a user asks about the past, while an
unrelated ordinary turn should preserve the same grounding boundary without inviting a memory
statement.

## Decision

Behavior policy v18 preserves the complete v17 constitution and activates two narrower projection
changes.

First, memory status wording is relevance-scoped by the existing deterministic disclosure plan.
When the request requires the memory facet, `NO_RELEVANT_MEMORY` and `UNAVAILABLE` retain natural
first-person uncertainty wording, including `помню`, `вспомнила` and `был похожий разговор` where
appropriate. When memory is not a required facet, the provider receives only the grounding rule:
invent no shared past and do not mention remembering, forgetting or an outage. Retrieved records
remain untrusted data and their JSON, provenance and grounding contract are unchanged. This adds no
new classifier, state, owner or write path.

Second, v18 uses a shorter literal realization of the same `CharacterExpressionPlan` v2. It asks
for one or two complete conversational sentences, renders the selected owned reaction and semantic
move as observable response behavior, and combines wit with bounded initiative. It forbids style
explanation and decorative metaphor without prescribing generated wording. Relationship ease
remains qualitative and request-local. Historical v17 rendering and its sampled artifact remain
unchanged.

The application-visible output allowances for the two public calibration turns increase narrowly
from 64 to 80 tokens for a completed achievement and from 80 to 96 for listen-before-advice. This
does not request verbosity; it prevents a noncompliant draft from being canonically committed as a
truncated sentence. Existing incomplete-output diagnostics and provider-specific wire enforcement
remain authoritative.

## Consequences

- An irrelevant no-recall result can no longer seed a fabricated first-person memory phrase.
- The v18 local sample completed 6/6 turns without truncation or invented shared history and used
  fewer input tokens than v17.
- The local 4B model still repeated the semantic direction and produced generic achievement
  formulas, so v18 character quality is not accepted from local evidence.
- Further phrase-level tuning against these six samples is rejected because it would create a
  brittle script for one model. A separately authorized OpenAI/Yandex sample must decide provider
  suitability through direct human review.
- The ten-reason validator, max-one retry, canonical state and Stage 15 boundary remain unchanged.
