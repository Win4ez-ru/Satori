# ADR-0094: Partial Gmail catalog for rejected control-bearing headers

- Status: Accepted for the bounded availability correction below; actual reading is unverified
- Date: 2026-10-30
- Authority: the user's continued request for working Gmail and the actual closed diagnostic
  obtained from the user-driven candidate-13 list
- Extends: ADR-0086/0087/0092/0093 with explicit partial metadata catalogs
- Supersedes: only whole-page failure on otherwise valid messages whose header values fail
  the two existing control-character checks; unsafe headers remain rejected
- Preserves: all budgets, source identity, account/grant owners, full-body parsing/projection,
  session-only OAuth, canonical exclusion, Character pause and Stage 15 closure

## Evidence

In the uninstalled candidate 13, the user completes Gmail login and requests one inbox page.
The existing closed UI diagnostic identifies `metadata_header_value_controls` and zero
displayed headers. This proves the rejecting check, not the actual header name, message ID,
character or affected-message count. None of those values is requested or recorded. The
installed filesystem is still bundle 12; private candidate evidence is not installed acceptance.

## Decision

Return an explicitly partial metadata page when a message's otherwise valid metadata contains
rejected raw or decoded header-value controls. Do not accept, strip, normalize away or display
the rejected text. Discard all header claims for that message and exclude its ID from the
readable catalog. Keep the remaining eligible rows and a typed, finite omission summary with
exact integer counts, at most 20 received messages and at most one omission per message.
Boolean counts are invalid. Summary totals must match the eligible/omitted partition; when
both control categories occur within one message, choose a deterministic raw-before-decoded
category. A bounded set of observed closed categories is sufficient; separate per-category
counts are optional and, if present, sum to the omitted-message count. Do not
include omitted IDs, ordinals, header names, values or characters in the returned summary.

Only `metadata_header_value_controls` and `metadata_header_decoded_controls` can produce this
omission. Validate message and thread identity before it. Inspect all received headers before
classifying a message as controls-only: an earlier control must not conceal later or same-field
type, name, duplicate, count, length, charset/decoding or other structural failures. Those
failures, malformed HTTP/JSON/payloads, mismatched identity and original aggregate limits still
reject the page. Keep the existing header length/count and wire, batch, request/deadline bounds.
Omitted responses still consume every original byte/RPC budget; no extra request or retry is
introduced. Commit a page only after all fetched metadata is fully checked.
Recheck the existing list deadline, cancellation, session and operation epoch after pure
header validation and immediately before assigning the catalog; parsing cannot extend it.

Invalidate the connector's previous readable catalog at the beginning of an explicit list,
while retaining the current valid account session. Assign a new catalog only after complete
validation. A failed refresh cannot leave an old catalog usable; an all-omitted page assigns
a new empty readable catalog. Old, omitted, cross-account or forged resource requests must
fail before a body HTTP request. Existing owner selection/proofs and generation/cancel fences
remain authoritative; an older operation cannot restore a superseded catalog. This is not a
permanent blacklist: a later fresh valid row and new explicit selection may admit that ID.

The UI and content-free home projection distinguish complete, partial, all-headers-unavailable
and genuinely empty pages. An unavailable page does not claim an empty inbox. Disclose bounded
omission counts; never fabricate missing headers or claim complete inbox coverage. There is
no new raw/full-body tolerance, MIME projection version, automatic pagination, background read,
login/refresh/retry, Send, canonical USER/history, provider/TTS, memory or relationship path.

## Verification and acceptance

Use synthetic none/mixed/all-control pages, both control categories and deterministic ordering;
controls followed or preceded by every hard failure, including same-field decoding failures;
invalid identity, malformed shapes and all original count/byte/deadline limits. Verify exact
readable catalog membership, forged omitted requests before HTTP, stale grants after failed
refresh and all-omitted results, later healthy re-admission, and cancellation/late operations.
Revalidate forged/copy-constructed DTOs, summary arithmetic and exact count types at boundaries.
Test worker/controller/receipt/UI/home propagation, honest empty/partial wording, clearing and
no source values in diagnostic or audit output.

Root integrates the frozen correction, runs the mandatory noneditable Foundation workflow
with complete current testcase coverage, qualifies build/runtime/native behavior and preserves
the previous installed app in a reversible app-only update. Candidate-13 diagnostic/source
qualification remains separately retained. Actual acceptance requires a fresh user-driven
list and one explicitly selected readable message in the resulting installed correction.
Unsupported messages and the exact omitted-header cause remain explicit limits.
