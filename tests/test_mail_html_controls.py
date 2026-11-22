"""Synthetic-only v5 projection compatibility; no claim about any actual rejected message."""

import base64
import hashlib
import json
from copy import deepcopy
from html import unescape

import pytest
from pydantic import ValidationError

from satori.application.assistant.mail_contracts import (
    MAX_MAIL_DECODED_BYTES,
    MailAccessError,
    MailAccountRef,
    MailProjection,
    MailSourceRef,
)
from satori.application.assistant.mail_projection import project_gmail_batch, project_gmail_message

SOURCE = MailSourceRef(
    account=MailAccountRef(subject="synthetic-html", client_registration="synthetic-client"),
    message_id="synthetic-selected-message",
)
OTHER = MailSourceRef(account=SOURCE.account, message_id="synthetic-other-message")
ALLOWED = ("\u00ad", "\u200b", "\u200c", "\u200d", "\u2060", "\ufeff")
BIDI = (
    "\u061c",
    "\u200e",
    "\u200f",
    "\u202a",
    "\u202b",
    "\u202c",
    "\u202d",
    "\u202e",
    "\u2066",
    "\u2067",
    "\u2068",
    "\u2069",
    "\u206a",
    "\u206b",
    "\u206c",
    "\u206d",
    "\u206e",
    "\u206f",
)


def leaf(text: str, mime: str = "text/html", *, part_id: str = "") -> dict[str, object]:
    raw = text.encode("utf-8")
    return {
        "mimeType": mime,
        "partId": part_id,
        "headers": [],
        "body": {
            "data": base64.urlsafe_b64encode(raw).decode("ascii").rstrip("="),
            "size": len(raw),
        },
    }


def wire(payload: dict[str, object], source: MailSourceRef = SOURCE) -> bytes:
    return json.dumps({"id": source.message_id, "payload": payload}).encode("utf-8")


def container(*parts: dict[str, object], mime: str = "multipart/mixed") -> dict[str, object]:
    return {"mimeType": mime, "headers": [], "body": {"size": 0}, "parts": list(parts)}


def project(html: str) -> MailProjection:
    return project_gmail_message(wire(leaf(html)), SOURCE)


@pytest.mark.parametrize("char", ALLOWED)
@pytest.mark.parametrize(
    "wrapper",
    [
        "<script>{}</script>",
        "<style>{}</style>",
        "<head>{}</head>",
        "<span hidden>{}</span>",
        '<div aria-hidden="true">{}</div>',
        '<span style="display: none">{}</span>',
        '<span style="visibility:hidden">{}</span>',
        '<span style="opacity:0">{}</span>',
    ],
)
def test_only_literal_allowlisted_data_already_discarded_is_omitted(
    char: str, wrapper: str
) -> None:
    payload = wire(leaf("<p>First</p>" + wrapper.format("discard" + char) + "<p>Last</p>"))
    result = project_gmail_message(payload, SOURCE)
    assert result.text == "First\n\nLast"
    assert result.completeness == "partial"
    assert not result.processing_eligible
    assert {item.reason for item in result.omissions} == {
        "html_lossy",
        "html_discarded_format_controls",
    }
    assert all(item.part is not None and item.part.path == () for item in result.omissions)
    assert result.raw_payload_digest == hashlib.sha256(payload).hexdigest()
    assert result.spans[0].text_digest == hashlib.sha256(result.text.encode()).hexdigest()
    assert result.projection_version == "gmail-local-text-v5"


@pytest.mark.parametrize("char", ALLOWED)
def test_visible_literal_and_plain_controls_are_never_removed(char: str) -> None:
    for payload in (leaf("<p>left" + char + "right</p>"), leaf("left" + char, "text/plain")):
        with pytest.raises(MailAccessError, match="invalid_body"):
            project_gmail_message(wire(payload), SOURCE)


@pytest.mark.parametrize("char", [*BIDI, "\u0600", "\u2061", "\ufff9"])
def test_bidi_and_nonallowlisted_format_controls_fail_even_in_discarded_data(char: str) -> None:
    with pytest.raises(MailAccessError) as raised:
        project("<p>Visible</p><span hidden>" + char + "</span>")
    assert raised.value.code == "invalid_body"
    assert raised.value.diagnostic == "body_html_format_controls"


@pytest.mark.parametrize(
    "entity", ["&shy;", "&#173;", "&zwnj;", "&zwj;", "&#8203;", "&#x200c;", "&#xFEFF;"]
)
@pytest.mark.parametrize("hidden", [False, True])
def test_recognized_format_entities_preserve_hidden_v3_admission_with_typed_omission(
    entity: str, hidden: bool
) -> None:
    attr = " hidden" if hidden else ""
    if hidden:
        result = project(f"<p>Visible</p><span{attr}>{entity}</span>")
        assert result.text == "Visible"
        assert result.completeness == "partial"
        assert not result.processing_eligible
        assert {item.reason for item in result.omissions} == {
            "html_lossy",
            "html_discarded_format_controls",
        }
    else:
        with pytest.raises(MailAccessError, match="invalid_body"):
            project(f"<p>Visible</p><span{attr}>{entity}</span>")


@pytest.mark.parametrize("char", BIDI)
def test_numeric_bidi_entities_are_rejected_inside_hidden_data(char: str) -> None:
    with pytest.raises(MailAccessError, match="invalid_body"):
        project(f"<p>Visible</p><span hidden>&#{ord(char)};</span>")


@pytest.mark.parametrize("char", ["\u0600", "\u2061", "\ufff9"])
def test_existing_clean_hidden_entity_route_does_not_gain_literal_permission(char: str) -> None:
    encoded = project(f"<p>Visible</p><span hidden>&#{ord(char)};</span>")
    assert encoded.text == "Visible"
    assert encoded.completeness == "partial"
    assert not encoded.processing_eligible
    assert {item.reason for item in encoded.omissions} == {
        "html_lossy",
        "html_discarded_format_controls",
    }
    with pytest.raises(MailAccessError, match="invalid_body"):
        project(f"<p>Visible</p><span hidden>{char}</span>")


@pytest.mark.parametrize(
    "template",
    [
        "<di{}v hidden>x</div>",
        '<div tit{}le="x" hidden>x</div>',
        '<div title="x{}" hidden>x</div>',
        "<div hidden>x</di{}v>",
        "<!--{}--><p>Visible</p>",
        "<div hidden><!--{}--></div><p>Visible</p>",
        "<!DOCTYPE html{}><p>Visible</p>",
        "<?x {}?><p>Visible</p>",
        "<![CDATA[{}]]><p>Visible</p>",
        "<div hidden><{}></div><p>Visible</p>",
        "<div hidden>< /x{}></div><p>Visible</p>",
        "<div hidden>&na{}me;</div><p>Visible</p>",
        "<div hidden>&#x20{}b;</div><p>Visible</p>",
        "<div hidden>&#82{}03;</div><p>Visible</p>",
    ],
)
def test_literal_format_controls_in_markup_comments_declarations_or_broken_refs_reject(
    template: str,
) -> None:
    with pytest.raises(MailAccessError):
        project(template.format("\u200c"))


@pytest.mark.parametrize("entity", ["&#8203;", "&zwnj;", "&lrm;", "&#x202e;"])
def test_entity_decoded_attribute_controls_are_rejected(entity: str) -> None:
    with pytest.raises(MailAccessError, match="invalid_body"):
        project(f'<p title="{entity}" hidden>discarded</p><p>Visible</p>')


def test_nested_local_discard_state_never_leaks_to_later_visible_data() -> None:
    result = project("<div hidden><span>discard\u200c</span></div><p>visible &amp; literal</p>")
    assert result.text == "visible & literal"
    with pytest.raises(MailAccessError, match="invalid_body"):
        project("<div hidden>discard\u200c</div><p>visible\u200c</p>")
    with pytest.raises(MailAccessError, match="unsupported_structure"):
        project("<div hidden><span>discard\u200c</div><p>visible</p>")


def test_multiline_positions_and_rawtext_are_checked_without_rescanning_suffixes() -> None:
    result = project("<p>Before</p>\n<div hidden>\n\u200c\u200b\n</div>\n<p>After</p>")
    assert "Before" in result.text
    assert "After" in result.text
    assert "\u200c" not in result.text
    with pytest.raises(MailAccessError, match="invalid_body"):
        project("<script>if (x < 1) 'hidden\u200c'</script><p>Visible</p>")


def test_unrelated_raw_data_delimiters_do_not_reject_clean_hidden_references() -> None:
    result = project(
        "<style>.x{background:url('?a=1&b=2')}</style><p>2 < 3</p><span hidden>&zwnj;</span>"
    )
    assert result.text == "2 < 3"
    assert {item.reason for item in result.omissions} == {
        "html_lossy",
        "html_discarded_format_controls",
    }


@pytest.mark.parametrize(
    "value",
    [
        "A&B=1",
        "&copy",
        "&notit;",
        "&unknown",
        "&#65",
        "&#x41",
        "&NotEqualTilde;",
        "A&amp;B=1",
        "&bogus;",
        "trailing&",
        "2 < 3",
    ],
)
def test_ordinary_html_entity_output_is_not_rewritten(value: str) -> None:
    result = project("<p>" + value + "</p>")
    assert result.text == unescape(value)


def test_decoded_size_and_depth_limits_still_apply_to_dropped_content() -> None:
    with pytest.raises(MailAccessError, match="invalid_body"):
        project("<div hidden>" + "\u200c" * MAX_MAIL_DECODED_BYTES + "</div><p>Visible</p>")
    with pytest.raises(MailAccessError, match="unsupported_structure"):
        project("<div hidden>" * 65 + "\u200c" + "</div>" * 65 + "<p>Visible</p>")


def test_selected_html_validation_and_selected_plain_failure_do_not_fallback() -> None:
    bad_plain = leaf("malformed\u200c", "text/plain", part_id="plain")
    good_html = leaf("<p>Visible</p><span hidden>\u200c</span>", part_id="html")
    with pytest.raises(MailAccessError, match="invalid_body"):
        project_gmail_message(
            wire(container(bad_plain, good_html, mime="multipart/alternative")), SOURCE
        )
    # The unchosen HTML alternative stays opaque under the existing v2 contract.
    opaque = project_gmail_message(
        wire(
            container(
                leaf("Plain", "text/plain"),
                leaf("<p>visible\u200c</p>"),
                mime="multipart/alternative",
            )
        ),
        SOURCE,
    )
    assert opaque.text == "Plain"
    assert {item.reason for item in opaque.omissions} == {"alternative_not_decoded"}
    # Decoded HTML in the selected mixed branch is validated even if plain is displayed.
    with pytest.raises(MailAccessError, match="invalid_body"):
        project_gmail_message(
            wire(container(leaf("Plain", "text/plain"), leaf("<p>visible\u200c</p>"))), SOURCE
        )


def test_raw_digest_and_versioned_spans_distinguish_equal_visible_output() -> None:
    plain_html = project("<p>Visible</p><div hidden>discard</div>")
    formatted_html = project("<p>Visible</p><div hidden>discard\u200c</div>")
    assert plain_html.text == formatted_html.text == "Visible"
    assert plain_html.raw_payload_digest != formatted_html.raw_payload_digest
    assert plain_html.projection_digest != formatted_html.projection_digest
    assert plain_html.spans[0].span_id != formatted_html.spans[0].span_id
    for version in ("gmail-local-text-v3", "gmail-local-text-v2"):
        with pytest.raises(ValidationError):
            MailProjection.model_validate(
                {**formatted_html.model_dump(), "projection_version": version}
            )


def test_batch_admission_stays_atomic_and_never_returns_first_body_on_second_rejection() -> None:
    valid = wire(leaf("<p>Visible</p><div hidden>discard\u200c</div>"))
    invalid = wire(leaf("<p>visible\u200c</p>"), OTHER)
    original = deepcopy((valid, invalid))
    with pytest.raises(MailAccessError, match="invalid_body"):
        project_gmail_batch((valid, invalid), (SOURCE, OTHER))
    assert (valid, invalid) == original
