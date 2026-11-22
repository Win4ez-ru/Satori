# ADR-0073: Contextual Repair Targeting Before Agency

- Status: Accepted experimental design; scoped verification complete, inherited global failures retained; behavioral acceptance open
- Date: 2026-10-13
- Authority: explicit user instruction to investigate the interpretation boundary
- Supersedes: ADR-0023 and ADR-0045 only for this isolated optional transient interpretation
- Related: ADR-0053, ADR-0058, ADR-0064, ADR-0069

## Evidence and hypothesis

The state-chain diagnostic demonstrates that canonical affect survives later turns and changes
reaction selection. It does not demonstrate model semantics or recognizable Character expression.
A private 28-case contrast corpus preserves missed natural repairs and a false target: the same
apology is treated as repair toward Satori even after an explicit invitation to submit a draft
letter to a colleague. Authored labels are design expectations, not a model verdict.

Hypothesis: a bounded interpretation of the current speech act and its addressee can make the
existing repair route reachable for natural paraphrases and avoid appropriating third-party
apologies. The operation determines targeting, not offense, sincerity, forgiveness or intimacy.
Object-distress false positives, compound repair plus requested work and generic expression
remain separate open limitations. No phrase list or prescribed Satori reply is added.

## Isolated decision

Add `conversation_repair_interpretation_enabled=false`, restricted to V35 with current-position,
prior-dialogue-relation and previous-exchange-appraisal candidates disabled. Disabled requests,
policy versions, persisted metadata and all state-owner contracts remain unchanged.

```text
canonical current input → preliminary deterministic intake and existing owner reads
→ complete actual-input receipt and protected-route eligibility
→ at most one bounded semantic repair-targeting operation
→ schema, source reachability and fresh source admission
→ one effective request-local repair cue in evidence AND rebuilt cognition intake
→ unchanged retrieval plan → final agency → completed cognition → existing expression
```

The operation receives only the current canonical USER text, the optional exact latest completed
USER/ASSISTANT pair and Satori's canonical name. Emotion and relationship are not evidence of
user intention. They remain downstream inputs to existing appraisal, selection and expression.
A scoped latest header prevents substituting an older available exchange for a filtered, erased
or oversized latest slot. Current-only interpretation is allowed only when no prior slot exists.
No history search, truncation, additional retrieval, second model or persistent pending thread.

Strict schema v1 proposes `repair_toward_satori`, `other` or `abstain`, with `supported` or
`material` uncertainty. A non-abstain proposal requires a unique exact current quote of at most
512 characters. Prior quotes are optional as a pair; when supplied, both must be unique exact
excerpts of the supplied pair. The application assigns IDs and half-open Unicode offsets.
Duplicate JSON keys, extra fields, non-unique or unreachable excerpts and invalid types fail.
No raw reasoning, explanation, response draft, motive or provider-assigned identity is requested.
Quote reachability establishes a dependency, never semantic correctness.

Supported `repair_toward_satori` sets the effective cue true. Supported `other` sets it false,
including clearing a lexical false positive. Abstain, material uncertainty, invalid output,
ordinary typed provider failure or bounded refusal preserves the lexical baseline. Rebuilding
intake uses the same canonical input and all previous dialogue flags; retrieval-plan inequality
is fatal. The effective cue reaches both existing agency selection and cognition completion.
Neither classification nor assistant history becomes fresh affect or relationship evidence.

Recognized questions, tasks, correction, challenge, repetition, distress, listening, motivation,
task abandonment, self-disclosure and explicit topic closure retain their current obligations.
Eligibility also requires the existing cognition owner's applied ANSWER route. Its pure position
selection is shared by preview and completion, retaining exactly the previous precedence and
summaries. Conversation copies no uncertainty or need thresholds. Material uncertainty, attentive
presence, collaboration and fallback are preserved before semantic dispatch: they cannot admit
the existing repair intent, and forcing the agency alone would violate the delivery contract.
Lexical repair remains eligible within that route so contextual false positives can be cleared.
Unrecognized obligations remain an intake limitation; this candidate does not broaden compound
arbitration. A mixed request must abstain semantically; this is not a deterministic guarantee.

## Ownership, source admission and failure

No state owner, mutation path, migration, technical permission or expression rewrite is added.
The independent EmotionManager still owns appraisal proposals, policy, decay and atomic commit.
Relationship remains separately owned. This operation cannot forgive, create hurt, clear an
emotional state or approve an action. Final expression remains free to react according to the
actual character, relationship and affect; detecting repair does not force warmth or a phrase.

Use the existing foreground ConversationGenerationPort/model with a maximum of 384 output
tokens and temperature zero for classification. Foreground native settings remain unchanged.
Current input is bounded at 8000 characters, prior pair at 4096, without truncation; trusted
context accounting also applies. At most one semantic call, no format retry or fallback model.

The full receipt includes all already-read roots and payloads, even uncited recent/owner data.
Actual transport admits sources immediately before dispatch; Talk re-admits after the operation
and the existing foreground/finalize path retains fresh admission. A source error is fatal even
after abstention or ordinary provider failure. Unexpected errors fail the interaction. Existing
max-one foreground retry reuses the frozen effective interpretation. Canonical replay performs
no interpretation and cannot reconstruct its transient diagnostic trace.

Numeric latency, usage and metrics are separate reply fields. A closed content-free trace reports
status/reason/kind/uncertainty and baseline/effective repair flags. It is transient like cognition
trace; no raw semantic output or excerpt is persisted, and no new manifest schema is introduced.
The existing manifest records the resulting intake, intent and agency. Raw dialogue is absent
from logs. Diagnostic source receipts remain the durable dependency record.

## Verification and acceptance

Test strict proposals and actual managed Talk: natural repair, same apology in a colleague draft,
current-only input, uncertainty, protected routes, invalid/material/abstain/outage baseline parity,
exact latest-slot and counterparty isolation, uncited footprint, source withdrawal at every gate,
shared retry and replay. Compare calm and residual strain without prescribing emotional prose.

Scripted proposals establish plumbing and admissible behavior only. Real semantic accuracy and
expression require a new frozen target-API plan, a fresh bounded grant and preserved results;
previous paid claims remain consumed. Human feedback must precede blind mapping disclosure.
No Character acceptance, global version winner or completion follows from offline checks.
Parallel Foundations continues independently; Stage 15 remains closed.
