# ADR-0089: Format controls in deterministically discarded HTML data

- Status: Accepted for the bounded correction below; engineering delivery and actual acceptance pending
- Date: 2026-10-20
- Authority: the user’s request for working Gmail and authorization to continue necessary bounded work
- Supersedes: ADR-0087 only for discarded-HTML format-control admission/projection version; ADR-0088 only for projection version, with header-value HTAB unchanged
- Preserves: all source, account, grant, transport, size and lifecycle boundaries

## Evidence

Installed bundle 7 has a successful nonempty partial selected-mail preview. A different fresh
exact-one selected read rejected `invalid_body / body_html_format_controls`. The diagnostic
establishes a Unicode `Cf` rejection in a decoded HTML part, before text extraction. No body,
codepoint, exact hidden/visible position or original MIME branch was retained. This proposal
does not identify the actual character, prove harmless content or establish that this message
will become readable. A fresh human-controlled read would be separate acceptance evidence.

Synthetic examples independently show that a literal zero-width non-joiner in a locally
discarded HTML data node rejects even when the admitted text would be unchanged. The v3
extractor already discards a recognized equivalent entity in such a node. Preserve that
compatibility rather than making valid entity representations newly unsupported.

## Decision

Use `gmail-local-text-v4`. The only newly admissible literal characters are U+00AD, U+200B,
U+200C, U+200D, U+2060 and U+FEFF. These characters may be omitted only in a data callback whose
existing deterministic local stack state already discards its output. Clean complete named
or numeric references, with an exact original token and terminating semicolon, preserve v3's
admission of all non-bidi `Cf` in the same already discarded data context. This deliberate
literal/entity asymmetry preserves established entity compatibility without opening every
literal format character. U+00AD and its `&shy;` / `&#173;` forms are explicitly paired.
Other literal `Cf`, visible controls, and bidi controls remain rejected. The bidi deny set
includes ALM/LRM/RLM, embeddings/overrides, isolates and deprecated U+206A–U+206F directives.
Rejecting encoded bidi and decoded attribute controls is an intentional tightening of v3;
those representations could previously disappear with discarded HTML or attributes. It is
not a claim about any actual rejected email's contents.

The existing `_DROP` tags and existing hidden/aria-hidden/CSS predicate remain unchanged.
These predicates describe this lossy local text projection; they are not proof of actual
browser invisibility, CSS correctness, harmlessness or authenticated semantics. No CSS engine,
remote resource, script execution or browser rendering is introduced.

Preserve exact raw data positions by disabling automatic reference conversion and explicitly
decoding reference callbacks. Every literal `Cf` position must belong to a validated dropped
data interval. Raw `Cf` in tag/attribute syntax, comments, declarations, processing instructions
or malformed reference tokens cannot receive that authority. Decoded controls in tag names
and attributes also reject. Linear raw token fences prevent a lenient tokenizer from turning
a malformed `<...>` or `&...;` suffix into allowed data. Unrelated raw `<` or `&` elsewhere
does not itself reject a valid omission. Literal angle-like/reference-like rawtext containing
`Cf` can still be conservatively unsupported; this is a known compatibility limit.

The raw body still consumes the existing decode budget before extraction, including every
discarded byte. The complete MIME tree, identifiers, headers, shape, depth, part and wire bounds
remain unchanged. The HTML stack/depth and output bounds remain unchanged. No malformed
selected plain candidate may fall back to HTML. Unused MIME alternatives remain unvalidated
under ADR-0087 and carry their existing partial omission.

To preserve existing selection behavior, HTML without raw controls is not parsed early solely
because a selected mixed branch also contains plain text. A decoded HTML candidate with raw
format characters must pass positional extraction before it can receive the new exception.
The final selected HTML fallback is parsed once; a cached positional extraction is reused.

## Output and provenance

Add closed typed omission `html_discarded_format_controls`, attributed to the original MIME
part. Display its static label with the existing omission list. No actual character, value,
position, account identifier or body is logged. Every such result remains `partial` and
`processing_eligible=False`. HTML remains lossy and external/untrusted. The raw payload digest
binds all original bytes; text spans and projection digests bind source, raw digest and v4.
An old v2/v3 DTO cannot masquerade as v4. Canonical text is never rewritten.

No OAuth/account access, read selection, expiry/revoke policy, Core/model input, memory,
relationship/self evidence, persistence, TTS, draft, sending or remote write is introduced.
Character remains paused and Stage 15 closed. The existing installed app and frozen evidence
are unchanged by this private candidate.

## Verification and acceptance

Synthetic paired raw/entity cases cover dropped versus visible data; all bidi families;
attributes/names/comments/declarations/CDATA; malformed token fallback; nested stack/focus of
discard; unchanged ordinary entities; independent plain preference and malformed-plain refusal;
wire/decode/text/depth bounds; exact provenance/version; partial/unsupported results and atomic
batches. Independent adversarial review is separate from owner tests. A fresh noneditable wheel
must match source, and focused tests run with private HOME/XDG/source-policy/database paths,
no network or normal profile access. Any later integration still needs the complete frozen
Foundation/build/artifact/manual gates. No synthetic pass fixes the actual rejected mail.
