# ADR-0112: Grounded literal anchors for conversational source search

- Status: Accepted for the current search-correctness repair; actual requalification remains separate
- Date: 2026-11-06
- Authority: the user's request for useful conversational mail/Telegram search and continued installation, after the first explicitly authorized real GPT qualification exposed a recall defect
- Supersedes: ADR-0110 only for permitting free-form model-written provider queries
- Preserves: one Core, eligible canonical context, original request/window/deadline/provider/budget owners, existing host ports and no source learning/effects

## Evidence

The first real GPT public run uses five HTTP attempts and an estimated0.423602RUB of the
authorized5RUB/six-attempt limit. It correctly summarizes the supplied invitation and asks a
missing-context clarification without reading sources. Both search plans, however, require
company, role and generic words such as «собеседование ответ». The positive fixture lacks
some of those words. Injection of that fixture after planning proves summarization, not that
the generated query can retrieve it. Preserve the original responses and partial verdict.

Do not ship a claim of accepted end-to-end search from this evidence. The original App24
source/bundle/gate remain immutable; repair in `work-search-anchor-r2` and requalify it.

## Decision

The model proposes one short literal search anchor and its eligible canonical provenance,
or asks a clarification. Prefer a known company/person/address over generic task words;
do not conjunctively add a role, «ответ» or other terms merely describing what the user wants.
The unchanged Core still resolves context and produces the answer; no NER service or extra
provider call is introduced.

The application admits an anchor only when its normalized literal text is present in the
actual owner request or the exact cited eligible canonical user/context-memory projection.
Do not use arbitrary system/personality/provider text, generated assistant prose or external
observations as anchor authority. Missing/ambiguous context remains a natural clarification;
an invented address/handle cannot become a valid anchor.

The deterministic host compiles the admitted literal into a quoted Gmail phrase and literal
Telegram text. The model does not emit executable provider query syntax. Keep the existing
public SourceSearchPlan wire, per-source selection and host-owned30-day window, observation/
byte limits, clocks, source leases, related-body reads and coverage rules. No automatic
broadening, fallback search, pagination, extra model call or domain write is added.

Query-aware public tests must actually apply the generated/compiled query to the source
fixture before returning a hit. Include the exact recorded failing overconstrained query,
company-only mail that omits role/«ответ», Telegram text, current-request and canonical-memory
anchors, invented/assistant-only/external anchors, missing context and operator injection.
Preserve all prior case identities and qualify one coherent frozen source before delivery.

## Existing bounded paid-test authority

The user's current allowance is at most5RUB and six HTTP attempts for the same three public
scenarios. Five attempts are settled and no unknown liability remains. A separately reviewed,
explicit post-fix planner check may use only the remaining sixth attempt for the same positive
public scenario, same model/data/namespace/period and original unexpired authorization. It must
verify the prior five records and atomically consume one new supplement marker. It may not reset
the ledger, original one-shot marker or allowance, create a new quota, repeat an unchanged failed
sample, run a seventh attempt or add private-account data. This is an explicit engineering
requalification after a code repair, not an automatic application/model retry. If those bounds
cannot be proved, do not run the paid supplement. No new spending is authorized by this ADR.

Summarization and missing-context output from the first run remain scoped evidence. Natural
character expression is not proved by a fixed joke, and no provider prose is rewritten.
