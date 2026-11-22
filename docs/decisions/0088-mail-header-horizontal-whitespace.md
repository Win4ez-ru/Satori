# ADR-0088: Mail header horizontal whitespace

- Status: Superseded in part by [ADR-0089](0089-discarded-html-format-controls.md) only for projection version; header-value HTAB and other decisions retained
- Date: 2026-10-20
- Authority: the user's request for working Gmail reading and the separate observed header rejection
- Supersedes: ADR-0087 only for header-value horizontal whitespace and projection version

## Evidence

The private source-panel v2 read the previously rejected visible newsletter selection with
nonempty text, explicit `partial` completeness and the unvalidated-alternative omission. A
different single selected notification still failed `invalid_header / header_controls`.
This identifies a control-character rejection, not the actual character. These source-panel
checks do not establish final installed-app acceptance.

The parser and `MailHeaderClaim` currently reject horizontal tab with all other controls.
[RFC 5322 section 2.2](https://www.rfc-editor.org/rfc/rfc5322.html#section-2.2) permits horizontal
tab in header field bodies. Rejecting that whitespace is an independently reproducible
compatibility defect. Whether it caused this notification's failure requires a new actual read.

## Decision

Use `gmail-local-text-v3`. Permit only literal horizontal tab in raw and decoded header **values**
and the corresponding typed claim. Retain the character losslessly; do not rewrite the header
or treat its sender claims as authenticated. Existing bounded CRLF-plus-whitespace unfolding
remains unchanged. Do not enable multiline text admission for header values: bare CR/LF, NUL,
DEL, other control characters, Unicode format/surrogate characters, forbidden duplicates and
invalid MIME metadata still reject. Header names, account/resource identifiers and all other
single-line fields retain their existing strict validation. Header count/length, whole-tree,
wire/decode/text bounds, scoped owner and atomic batch admission are unchanged.

This is local untrusted display only. No new permission, source fetch, Core/model context,
memory, TTS, persistent login, remote write or sending is introduced. ADR-0087's alternative
selection, conservative partial omissions and closed diagnostics remain in force. Character
stays paused and Stage 15 closed.

## Verification

Use synthetic paired raw/encoded header-value tab cases, typed-claim retention, and negative
CR/LF/NUL/DEL/other-control/format/surrogate cases. Tabs in names and identifiers still fail.
Rerun the alternative/budget regressions, fresh noneditable Foundation quality, full frozen
coverage and artifact checks. Re-read both the visible newsletter and the separately rejected
notification with fresh requests. Preserve prior failed attempts; do not infer the exact
earlier character or claim the installed revision repaired before that evidence exists.
