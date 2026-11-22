# ADR-0097: Exact supported zero-opacity in the local mail projection

- Status: Accepted for bounded implementation and qualification; real-message acceptance separate
- Date: 2026-10-31
- Authority: the daily-assistant request and root's bounded mail-projection assignment
- Partially supersedes: ADR-0087/0089 only for the opacity predicate and projection version
- Preserves: ADR-0088 header admission and all source, account, grant, transport, control,
  lifecycle, canonical-state and model-disclosure contracts

## Problem and evidence boundary

The current local HTML extractor searches for the substring `opacity:0` after removing
whitespace. Consequently `opacity:0.5` and `opacity:0.8` discard otherwise extractable text.
The same dropped state supplies authority for ADR-0089's narrow discarded-data format-control
exception. This is a source-backed parser defect. Synthetic examples can reproduce it without
retaining the user's personal email. They do not establish the complete contents, exact MIME
branch or readability of any actual message.

## Decision

Use `gmail-local-text-v5`. Change only the opacity component of the existing local hidden
predicate. Inspect the decoded raw `style` attribute after the unchanged attribute/body
control validation and existing CR normalization. Do not use the earlier Unicode-whitespace
stripped string for this component.

Recognize exactly one whole, semicolon-delimited opacity declaration. Its ASCII property
name is case-insensitive `opacity`, its value is the literal `0`, and an optional `!important`
suffix may follow. Only ASCII CSS whitespace (space, tab, LF, CR, FF) may separate these
tokens. Existing upstream control validation still rejects unsupported controls such as FF;
this grammar introduces no new raw-body/attribute character permission. Use ASCII-only case
matching; Unicode lookalikes are not the property name.

No opacity declaration may authorize discard if the style contains a quote, escape,
slash/star, parenthesis, brace or square bracket. This deliberately excludes strings,
comments, functions and nested CSS blocks without implementing a CSS tokenizer. Count
opacity-looking declarations whose name is followed by ASCII CSS whitespace, a colon or
end-of-declaration; duplicate, conflicting or malformed such declarations cannot authorize
discard. Unsupported forms such as `.0`, `0.0`, `0%`, signs, exponents, escapes, variables,
comments or function values do not count as the supported literal zero. They remain subject
to the normal HTML/control checks. In particular, nonzero `0.5` and `0.8` retain text and
never supply discarded-data authority.

The existing `hidden`, `aria-hidden`, `display:none`, `visibility:hidden`, ancestor-discard and
drop-tag rules remain unchanged. Another existing rule can still discard the same node.
No CSS cascade, browser visibility or authenticated meaning is claimed. A style containing a
quoted font family and literal zero is intentionally outside the new opacity subset; a
future compatibility expansion needs its own bounded decision and tests.

## Admission, provenance and isolation

The exact-zero case retains the existing literal/entity format-control treatment of an
already discarded data node. A nonzero or unsupported opacity form cannot gain that
exception: visible `Cf` is rejected, bidi remains rejected even when discarded, and controls
in attributes, tag syntax, comments or malformed tokens remain rejected. Existing stack,
shape, MIME, part, depth, wire/decode/text and batch budgets are unchanged. Discarded bytes
still consume the original budgets. No source text is rewritten to pass validation.

Version v5 binds span identifiers and projection digests to the corrected algorithm. Text
digests still hash the admitted text. Old-version DTOs cannot masquerade as v5;
raw-payload digest and exact source binding remain intact. HTML
continues to be lossy, partial, external/untrusted and `processing_eligible=False`.

This proposal adds no source-to-model/canonical path, OAuth, stored login, account read,
automatic request, UI redesign, persistence, TTS, draft, Send or remote write. Persistent
login is a separate gated plan, not implementation authority from this ADR.

## Verification and delivery

Pair zero/nonzero text extraction and literal/entity controls. Cover whitespace, case,
`!important`, complete token boundaries, duplicate/malformed declarations, comments,
quoted semicolons, escapes, functions, nested brackets, Unicode whitespace/lookalikes and
existing independent hidden predicates. Preserve bidi/control/shape/depth/byte tests and
versioned source/span/digest, stale-version and atomic-batch checks.

Run relevant checks in a private fresh noneditable environment, starting with the frozen
sync/reinstall command; keep dependency/environment effects separate from the shared runtime.
Owner prepares a frozen patch/pins and synthetic evidence in its scratch directory. Root
accepts the ADR before shared application and owns the complete Foundation/build/native and
reversible delivery gates. Actual selected-message reading remains a fresh, explicit user
acceptance step and is never inferred from synthetic success.
