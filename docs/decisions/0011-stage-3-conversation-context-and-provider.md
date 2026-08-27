# ADR-0011: Stateless Stage 3 conversation context and local provider

- Status: Accepted
- Date: 2026-07-28

## Context

Stage 3 must prove that persistent Satori state can drive replaceable natural-language
generation without making a model, prompt or provider thread the identity. It must also
separate trusted policy/state from user content, avoid invented memory, fail safely when
local inference is unavailable and stay within the privacy gates of later stages.

The target roadmap previously placed persistent interaction records in Stage 3, while the
explicit Stage 3 task permits a stateless single-turn core and the raw retention/redaction
decision remains gated for Stage 4. Persisting messages now would pre-empt that privacy
decision. The development machine is Apple Silicon with 8 GB memory and did not have
Ollama installed during implementation.

## Decision

Stage 3 uses stateless Option A: one current user message produces one reply. There is no
session window, message/interaction table, provider conversation thread or idempotent
interaction-finalize transaction. Restart loses no conversation state because none is
retained. This is a deliberately smaller validation slice; ADR-0008's transactional
interaction target remains applicable once interaction persistence is introduced after
the Stage 4 retention decision.

`CharacterContextComposer` projects the authoritative Stage 2 snapshot into immutable
runtime context schema v1. It includes only the identity name, all 15 small constitutional
trait records, all 9 value records and explicit capability-absence flags. It excludes
identity ID, activation timestamp, seed provenance/hash, audit and ORM/DB metadata. The
projection is bounded by a configured character limit and is rejected rather than silently
truncated.

The provider-neutral request has three structurally distinct messages:

1. trusted versioned behavioral policy;
2. trusted application-generated character-context data;
3. untrusted current user content.

The behavioral policy contains stable constitutional constraints—independence, no
automatic agreement, uncertainty honesty, no invented memory/backstory, natural style and
user-content distrust—but no numeric personality source. Numeric traits and values always
come from persistence. Ollama has no separate developer role in the selected API surface,
so its adapter maps the application `developer` layer to a second `system` message while
keeping user content in the `user` role.

The concrete Stage 3 adapter is local Ollama [`/api/chat`](https://docs.ollama.com/api/chat), implemented in infrastructure
using stdlib HTTP rather than a vendor SDK dependency. It is non-streaming, disables model
thinking output, uses an explicit timeout and maps HTTP/transport/schema failures to typed
provider-neutral errors. Model/base URL/timeout/generation limits are configuration. There
is one provider and no router, retry graph or cloud escalation.

The initial configured model is `qwen3:4b-instruct`. The official [Ollama registry](https://ollama.com/library/qwen3/tags) lists the
Q4 model at approximately 2.5 GB and describes Qwen3 as multilingual and conversation
capable, making it a plausible 8 GB local baseline. It is not a permanent identity choice:
fake providers and any future compatible adapter receive the same request contract.
Because Ollama was unavailable locally, deterministic HTTP contract tests are mandatory
and the real smoke test is optional behind `SATORI_RUN_OLLAMA_INTEGRATION=1`.

Provider output is plain text plus provider/model/finish/optional usage metadata. The
application rejects blank and oversize text and wraps untyped adapter failures. Stage 3
uses an explicit no-memory capability contract and deterministic request inspection; it
does not pretend to have a complete semantic past-claim grounding classifier.

## Consequences

Provider replacement leaves identity/personality/values unchanged and receives the same
character basis. User injection cannot enter trusted request roles. Normal logs contain
trace/provider/model/latency/context version/counts but not message, reply or full prompt.
No migration or persistent mutation path is added.

The CLI is one-turn `satori talk "..."`; each invocation has no knowledge of earlier turns.
Ollama and the configured model must be installed separately for real generation. Real
behavior/latency on the selected hardware still needs the optional sampled smoke before a
release claim. Full interaction logging, idempotent finalize, recent windows, past-claim
grounding against evidence and memory formation remain later work.

## Alternatives rejected

A giant personality prompt duplicates authoritative state. Provider-hosted history makes
the provider part of identity. Persisting raw messages before privacy policy resolves the
wrong gate. An in-memory session window adds ambiguous pseudo-continuity without helping
the Stage 3 provider-is-not-Satori proof. A complex router or cloud fallback expands scope.
CI depending on a local Ollama daemon would be non-reproducible. Trait-to-style `if` rules
would turn probabilistic traits into scripted switches.
