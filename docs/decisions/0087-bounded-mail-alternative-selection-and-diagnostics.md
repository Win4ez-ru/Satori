# ADR-0087: Bounded mail alternative selection and closed diagnostics

- Status: Superseded in part by [ADR-0088](0088-mail-header-horizontal-whitespace.md) for header-value whitespace/version and [ADR-0089](0089-discarded-html-format-controls.md) for discarded-HTML format-control admission/version; other decisions retained
- Date: 2026-10-20
- Authority: the user's continued request for working Calendar/Gmail access and report of real
  selected-mail rejection
- Supersedes: ADR-0085 and ADR-0086 only for MIME-alternative decoding and projection version
- Preserves: scoped read owner, session-only OAuth, transport bounds and source isolation

## Evidence and problem

The installed revision connects to Gmail and lists a bounded INBOX page. Selected reading has
also failed closed with `invalid_body` and `invalid_header`. A separate diagnostic-only source
window, with no Core or normal profile, exposed `body_encoded_limit` for the earlier newsletter.
That proves an encoded-body budget rejection; it does not identify the exact MIME branch.
Another selected message read successfully in that window. Neither result repairs or erases
the earlier failures, and source-window evidence is separate from installed-app acceptance.

The existing parser decodes every alternative before preferring plain text. Consequently an
unused HTML representation can exhaust the decoded-body budget even when an adequate plain
representation fits. Correct that independently reproducible defect without raising limits.

## Decision

Use `gmail-local-text-v2`. First validate a bounded MIME structure once: every traversed part,
depth, headers, MIME metadata, identifier, child shape and global part-ID uniqueness. Keep
the existing limits of 64 parts, depth 8, 512 KiB wire per message and 1 MiB per batch. A body
must have the expected object shape; declared sizes retain their primitive/nonnegative checks
and container-size rule. No attachment, nested message or unsupported type becomes readable.

Then choose a deterministic usable plain-text representation in `multipart/alternative`
before decoding unused HTML alternatives. Empty text or an unsupported charset is an explicit
omission and may allow the existing HTML fallback. A malformed or oversized selected plain
candidate remains a rejection; do not conceal it by silently switching representations.
Every decoded candidate, including an empty candidate tried before fallback, consumes the same
per-message budget. Keep 64 KiB decoded per message, 128 KiB per batch, 16,000 text characters
per message and 24,000 per batch. Selection must not reset these counters.

An alternative whose body was not decoded receives a source-attributed
`alternative_not_decoded` omission. Its encoding, decoded-size equality, charset bytes and text
are unvalidated. Even when the selected text is usable, this omission makes the projection
`partial`; it must not inherit the earlier `complete` treatment of validated alternatives.
The raw-payload digest remains bound to the whole received message; versioned text/span and
projection digests describe only the admitted projection. No previous snapshot is replayed.

Expose optional runtime-validated closed diagnostic reasons for header/body rejection. Carry
only the allowed reason through parser, existing coordinator and current controller lifecycle
to static UI text. Keep the broad audit/error code and exception string unchanged. Clear the
transient reason on new actions, success, cancellation, disconnect and profile/generation change.
Never include source values, header names, addresses, IDs, MIME paths, actual sizes, characters,
callback URLs, credentials, exception payloads or traceback text in those diagnostics.

This correction does not relax header validation, plain-text controls, HTML extraction, OAuth
scope, account/resource identity, one-use selection, deadlines or batch atomicity. No source
payload enters canonical Send, Core/model context, memory, self/relationship evidence or TTS.
There is no persistence, background sync, remote write, draft, Send or new source owner.
Character remains paused and Stage 15 closed.

## Verification and acceptance

Use synthetic paired cases: small plain plus oversized unused HTML in either order; selected
oversized/malformed plain; empty/unsupported plain fallback; nested alternatives; malformed
unused structure and duplicate IDs; exact part/depth/wire/decode/text bounds; source spans,
versioned digests, partial omissions and atomic batches. Verify diagnostic propagation and
clearing through the owner/controller/UI, not only direct parser exceptions.

Run fresh noneditable Foundation quality, complete frozen coverage, artifact/source parity and
the installed UI checks. A private source-panel test may establish a scoped actual-message
result but cannot stand in for the final installed revision. Re-read the previously rejected
newsletter explicitly with a fresh request after the correction. Preserve every earlier failed
attempt. The unrelated `invalid_header` remains unresolved until its own safe diagnostic and
actual evidence identify the cause; this decision does not declare it fixed.
