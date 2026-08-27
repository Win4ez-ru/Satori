# ADR-0013: Exact episodic retrieval and grounded memory context

- Status: Accepted
- Date: 2026-07-30

## Context

Stage 4 stores source-grounded episodes but deliberately provides none to generation. Stage 5
must recover a small relevant set after restart without turning chat history into memory, vector
similarity into truth, or provider state into Satori's persistent self. The first index must work
on the target local SQLite installation without a native extension and must be disposable when
the embedding model or input format changes.

Ollama documents a batched `/api/embed` endpoint, explicit output dimensions, unit-normalized
vectors and cosine similarity with the same model for index/query. `embeddinggemma` is a small
multilingual local model suitable for the target machine. Deterministic fake-vector fixtures
cover the required direct, paraphrase, distractor and no-result behavior without making CI
depend on an installed daemon.

## Decision

Add a provider-neutral `EmbeddingPort` contract. Every vector belongs to an exact
`EmbeddingSpace(provider, model, dimensions, input_schema_version)`. The configured Stage 5
default is Ollama `embeddinggemma:300m`, 768 dimensions and input schema v1. Query and candidate
vectors are compared only when all four fields match. A model/input change creates a distinct
space; canonical memories are never rewritten.

Store vectors in `episodic_memory_embeddings` as derived JSON arrays in the same local SQLite
database. The row includes memory ID, exact space and indexing time. The unique key is memory +
space. `memories index` fills missing rows idempotently; `memories rebuild` replaces every row in
the active space. Old spaces may coexist and are ignored. Exact in-process cosine scan is chosen
for the current small corpus because it is portable, inspectable and requires no SQLite extension
or external vector service. A measured scale problem, not anticipation, is required before
replacing it.

Eligibility v1 is: active episodic memory, compatible space, `occurred_at <= query cutoff`, and
source interaction unequal to the current interaction. The query is only the current user text.
Similarity is raw cosine. Candidates below `0.55` are rejected; the top 32 semantic candidates
enter deterministic ranking:

```text
recency = 0.5 ** (age_days / 30)
final = 0.80 * cosine + 0.10 * importance + 0.10 * recency
```

The semantic weight exceeds all secondary weights combined. Sort ties resolve by similarity,
importance, recency, then stable memory ID. Select at most four records under a 2400-character
canonical JSON payload budget, with exact normalized-summary duplicates removed. Confidence and
source evidence remain visible metadata but do not increase rank in v1. Similarity is a relevance
signal only; truth still comes from canonical episode provenance.

Conversation context has a separate developer-role memory envelope. Its trusted wrapper marks
all embedded summaries as untrusted evidence data, forbids following instructions from them,
and requires provider-declared shared-past claims to reference one of the supplied memory IDs.
The current user message remains a separate user-role message. Empty/no-result and unavailable
outcomes contain no memory records and therefore permit no grounded past-claim IDs.

Retrieval happens after pending interaction intake but before conversation inference. Its cutoff
and explicit excluded interaction ID prevent self-retrieval. Episode formation remains after
canonical reply finalize; successful episodes are indexed in a later derived transaction.
Retrieval or indexing failure logs metadata and degrades to no memory without rolling back an
episode, blocking conversation, or fabricating recall.

## Consequences

Relevant episodic continuity survives process restart and provider swaps while the index can be
fully discarded/rebuilt. Model-space mismatch cannot silently compare incompatible vectors.
Rank components, selected memory IDs, counts, space, status and latency are observable without
logging queries, summaries, evidence quotes or raw vectors.

Exact scan is O(number of compatible episodes), JSON vectors are storage-heavy, and mutable model
tags cannot cryptographically prove model weights. There is no person filter because the current
product has no multi-person/user-model state; adding one before Stage 9 would invent a domain.
Stage 5 only exact-deduplicates normalized summaries and does not consolidate semantically
equivalent episodes. Plain-text models may still emit an undeclared past claim, so sampled
false-recall evaluation remains a release gate.

## Alternatives rejected

SQLite vector extension now: adds platform/runtime coupling before scale evidence. External local
vector database: creates a second durability and recovery boundary for a small corpus. Put vectors
on canonical memory rows: makes provider-derived state look authoritative and complicates model
replacement. LLM reranking: adds latency, nondeterminism and an unmeasured trust boundary. Inject
raw session history: confuses persistence with memory and violates the bounded selective contract.
