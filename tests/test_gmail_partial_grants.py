"""Adversarial partial-catalog capabilities using synthetic HTTP and the real owner."""

import base64
import json
import threading
from dataclasses import dataclass
from email.header import decode_header
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest

from satori.application.assistant.mail import MailAccessCoordinator
from satori.application.assistant.mail_contracts import (
    MAX_MAIL_MESSAGE_BYTES,
    MailAccessError,
    MailReadGrant,
    MailReadRequest,
    MailSelectionProof,
    MailSourceRef,
)
from satori.infrastructure.gmail import connector as connector_module
from satori.infrastructure.gmail.connector import GmailConnector
from satori.infrastructure.gmail.contracts import GmailError, GmailInboxPage
from tests.test_gmail_connector import (
    Call,
    Clock,
    FakeHTTP,
    FakeLoopback,
    configuration,
    encoded,
    principal,
)

_LIST = "/gmail/v1/users/me/messages"
_OMITTED_ID = "omitted-private-resource"
_PRIVATE_HEADER = "OMITTED-HEADER-MUST-NOT-BECOME-A-CLAIM"
_RAW_CONTROL = {"name": "Subject", "value": "synthetic\u200bsubject"}
_ENCODED_CONTROL = {
    "name": "Subject",
    "value": "=?UTF-8?B?" + base64.b64encode("synthetic\u200bsubject".encode()).decode() + "?=",
}


@dataclass
class Harness:
    connector: GmailConnector
    http: FakeHTTP
    clock: Clock


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> Harness:
    monkeypatch.setattr(connector_module, "OAuthLoopback", FakeLoopback)
    http, clock = FakeHTTP(), Clock()
    connector = GmailConnector(principal(), http=http, clock=clock)
    start = connector.begin_login(configuration())
    connector.finish_login(start.attempt_id, cancel=threading.Event())
    return Harness(connector, http, clock)


def _metadata(
    message_id: str,
    headers: object | None = None,
    *,
    thread_id: object = "synthetic-thread",
) -> dict[str, object]:
    return {
        "id": message_id,
        "threadId": thread_id,
        "payload": {
            "headers": [{"name": "Subject", "value": "synthetic accepted"}]
            if headers is None
            else headers
        },
    }


def _set_metadata(h: Harness, message_id: str, value: object) -> None:
    h.http.responses[f"{_LIST}/{message_id}"] = encoded(value)


def _mixed(h: Harness) -> GmailInboxPage:
    h.http.ids = ("retained", _OMITTED_ID)
    _set_metadata(h, "retained", _metadata("retained"))
    _set_metadata(
        h,
        _OMITTED_ID,
        _metadata(
            _OMITTED_ID,
            [{"name": "From", "value": _PRIVATE_HEADER}, _RAW_CONTROL],
        ),
    )
    return h.connector.list_inbox(cancel=threading.Event())


def _request(
    h: Harness, page: GmailInboxPage, ids: tuple[str, ...]
) -> tuple[MailAccessCoordinator, MailReadRequest]:
    owner = MailAccessCoordinator(page.catalog.principal, clock=h.clock)
    owner.admit_catalog(page.catalog)
    proof = owner.select(ids, confirmed=True)
    owner.issue(proof, confirmed=True)
    return owner, owner.prepare_read()


def _forge_request(
    request: MailReadRequest, message_id: str, *, catalog_id: UUID | None = None
) -> MailReadRequest:
    original = request.grant.selection
    selection = MailSelectionProof(
        principal=original.principal,
        account=original.account,
        catalog_id=original.catalog_id if catalog_id is None else catalog_id,
        selection_id=uuid4(),
        generation=original.generation,
        revision=original.revision,
        resources=(MailSourceRef(account=original.account, message_id=message_id),),
    )
    grant = MailReadGrant(
        grant_id=uuid4(),
        selection=selection,
        issued=request.grant.issued,
        expires_at=request.grant.expires_at,
        monotonic_deadline=request.grant.monotonic_deadline,
    )
    return MailReadRequest(
        request_id=uuid4(),
        grant=grant,
        created=request.created,
        expires_at=request.expires_at,
        monotonic_deadline=request.monotonic_deadline,
    )


def _blocked_before_http(h: Harness, request: MailReadRequest) -> None:
    before = len(h.http.calls)
    with pytest.raises(GmailError, match="request_invalid"):
        h.connector.read_selected(request, cancel=threading.Event())
    assert len(h.http.calls) == before


def _metadata_calls(calls: list[Call]) -> list[Call]:
    return [
        call for call in calls if parse_qs(urlsplit(call.url).query).get("format") == ["metadata"]
    ]


def test_omitted_message_has_no_claim_identity_or_capability_in_partial_page(
    harness: Harness,
) -> None:
    page = _mixed(harness)
    assert page.completeness == "partial"
    assert page.total_rows == 2
    assert page.omitted_count == 1
    assert tuple(item.source.message_id for item in page.messages) == ("retained",)
    assert page.catalog.message_ids == ("retained",)
    assert [(item.reason, item.count) for item in page.omissions] == [
        ("metadata_header_value_controls", 1)
    ]
    for output in (page.model_dump_json(), repr(page), repr(page.catalog)):
        assert _OMITTED_ID not in output
        assert _PRIVATE_HEADER not in output
        assert "\u200b" not in output

    owner = MailAccessCoordinator(page.catalog.principal, clock=harness.clock)
    owner.admit_catalog(page.catalog)
    before = len(harness.http.calls)
    with pytest.raises(MailAccessError, match="selection_invalid"):
        owner.select((_OMITTED_ID,), confirmed=True)
    assert owner.grant is None
    assert owner.snapshot is None
    assert len(harness.http.calls) == before


@pytest.mark.parametrize("catalog", ["current", "foreign"])
def test_forged_typed_request_for_omitted_id_never_dispatches_http(
    harness: Harness, catalog: str
) -> None:
    page = _mixed(harness)
    _, legitimate = _request(harness, page, ("retained",))
    forged = _forge_request(
        legitimate,
        _OMITTED_ID,
        catalog_id=page.catalog.catalog_id if catalog == "current" else uuid4(),
    )
    _blocked_before_http(harness, forged)


def test_remaining_retained_message_still_passes_real_coordinator_and_projection(
    harness: Harness,
) -> None:
    page = _mixed(harness)
    owner, request = _request(harness, page, ("retained",))
    harness.http.responses.clear()
    before = len(harness.http.calls)
    result = harness.connector.read_selected(request, cancel=threading.Event())
    snapshot = owner.accept_result(request, result)
    assert tuple(item.source.message_id for item in snapshot.messages) == ("retained",)
    assert len(harness.http.calls) == before + 1
    assert urlsplit(harness.http.calls[-1].url).path == f"{_LIST}/retained"
    assert parse_qs(urlsplit(harness.http.calls[-1].url).query)["format"] == ["full"]
    assert all(item.trust == "external_untrusted" for item in snapshot.messages)


def test_retained_unicode_and_tabs_are_preserved_without_control_sanitization(
    harness: Harness,
) -> None:
    harness.http.ids = ("retained", _OMITTED_ID)
    retained = "synthetic café\tretain spacing"
    _set_metadata(
        harness, "retained", _metadata("retained", [{"name": "Subject", "value": retained}])
    )
    _set_metadata(harness, _OMITTED_ID, _metadata(_OMITTED_ID, [_RAW_CONTROL]))
    page = harness.connector.list_inbox(cancel=threading.Event())
    assert page.completeness == "partial"
    assert page.messages[0].subject == retained
    assert [(claim.name, claim.value, claim.index) for claim in page.messages[0].headers] == [
        ("subject", retained, 0)
    ]


def test_all_omitted_is_unsupported_and_never_empty_inbox(harness: Harness) -> None:
    harness.http.ids = (_OMITTED_ID,)
    _set_metadata(harness, _OMITTED_ID, _metadata(_OMITTED_ID, [_RAW_CONTROL]))
    unsupported = harness.connector.list_inbox(cancel=threading.Event())
    assert unsupported.completeness == "unsupported"
    assert unsupported.total_rows == unsupported.omitted_count == 1
    assert unsupported.messages == ()
    assert unsupported.catalog.message_ids == ()
    owner = MailAccessCoordinator(unsupported.catalog.principal, clock=harness.clock)
    owner.admit_catalog(unsupported.catalog)
    with pytest.raises(MailAccessError, match="selection_invalid"):
        owner.select((_OMITTED_ID,), confirmed=True)
    with pytest.raises(MailAccessError, match="selection_invalid"):
        owner.select((), confirmed=True)
    assert owner.grant is None

    harness.http.ids = ()
    empty = harness.connector.list_inbox(cancel=threading.Event())
    assert empty.completeness == "complete"
    assert empty.total_rows == empty.omitted_count == 0
    assert empty.omissions == ()
    assert empty.messages == ()
    assert empty.catalog.catalog_id != unsupported.catalog.catalog_id


def test_omission_counts_are_per_message_and_raw_reason_precedes_decoded(harness: Harness) -> None:
    harness.http.ids = ("retained", "raw", "decoded", "both")
    _set_metadata(harness, "raw", _metadata("raw", [_RAW_CONTROL]))
    _set_metadata(harness, "decoded", _metadata("decoded", [_ENCODED_CONTROL]))
    _set_metadata(
        harness,
        "both",
        _metadata("both", [_RAW_CONTROL, {**_ENCODED_CONTROL, "name": "From"}]),
    )
    page = harness.connector.list_inbox(cancel=threading.Event())
    assert page.catalog.message_ids == ("retained",)
    assert page.total_rows == 4
    assert page.omitted_count == 3
    assert {item.reason: item.count for item in page.omissions} == {
        "metadata_header_value_controls": 2,
        "metadata_header_decoded_controls": 1,
    }


@pytest.mark.parametrize("replacement", ["partial", "all_omitted"])
def test_old_valid_grant_cannot_read_a_now_excluded_id(harness: Harness, replacement: str) -> None:
    harness.http.ids = (_OMITTED_ID,)
    old = harness.connector.list_inbox(cancel=threading.Event())
    _, request = _request(harness, old, (_OMITTED_ID,))
    if replacement == "partial":
        page = _mixed(harness)
    else:
        _set_metadata(harness, _OMITTED_ID, _metadata(_OMITTED_ID, [_RAW_CONTROL]))
        page = harness.connector.list_inbox(cancel=threading.Event())
    assert _OMITTED_ID not in page.catalog.message_ids
    _blocked_before_http(harness, request)
    if replacement == "partial":
        _, current = _request(harness, page, ("retained",))
        _blocked_before_http(
            harness, _forge_request(current, _OMITTED_ID, catalog_id=old.catalog.catalog_id)
        )


@pytest.mark.parametrize("failure", ["transport", "list_json", "metadata_json"])
def test_failed_refresh_invalidates_old_catalog_before_any_reuse(
    harness: Harness, failure: str
) -> None:
    harness.http.ids = ("old",)
    old = harness.connector.list_inbox(cancel=threading.Event())
    _, request = _request(harness, old, ("old",))
    if failure == "transport":
        harness.http.responses[_LIST] = GmailError("http_unavailable")
    elif failure == "list_json":
        harness.http.responses[_LIST] = b"{"
    else:
        harness.http.ids = ("control", "broken")
        _set_metadata(harness, "control", _metadata("control", [_RAW_CONTROL]))
        harness.http.responses[f"{_LIST}/broken"] = b"{"
    with pytest.raises(GmailError):
        harness.connector.list_inbox(cancel=threading.Event())
    assert harness.connector.account is not None
    _blocked_before_http(harness, request)


def test_refresh_invalidates_old_catalog_before_first_list_exchange(harness: Harness) -> None:
    harness.http.ids = ("old",)
    old = harness.connector.list_inbox(cancel=threading.Event())
    _, request = _request(harness, old, ("old",))
    checked: list[bool] = []

    def during_first_get(call: Call) -> None:
        if urlsplit(call.url).path == _LIST:
            _blocked_before_http(harness, request)
            checked.append(True)

    harness.http.on_request = during_first_get
    _mixed(harness)
    assert checked == [True]


@pytest.mark.parametrize(
    ("later", "diagnostic"),
    [
        ({"name": "subject", "value": "duplicate"}, "metadata_header_duplicate"),
        ({"name": "From", "value": "=?unknown-fixture-codec?q?word?="}, "metadata_header_decoding"),
        ({"name": "To", "value": "a" * 2049}, "metadata_header_value_limit"),
        ({"name": "X-Unselected", "value": "synthetic"}, "metadata_header_name"),
        ({"name": "From", "value": None}, "metadata_header_value_type"),
        (17, "metadata_header_shape"),
    ],
)
@pytest.mark.parametrize("control", [_RAW_CONTROL, _ENCODED_CONTROL])
def test_control_omission_never_hides_a_later_hard_header_failure(
    harness: Harness, later: object, diagnostic: str, control: dict[str, str]
) -> None:
    harness.http.ids = ("first_good",)
    old = harness.connector.list_inbox(cancel=threading.Event())
    _, old_request = _request(harness, old, ("first_good",))
    harness.http.ids = ("first_good", "control_then_invalid")
    _set_metadata(
        harness, "control_then_invalid", _metadata("control_then_invalid", [control, later])
    )
    before = len(harness.http.calls)
    with pytest.raises(GmailError, match="response_invalid") as caught:
        harness.connector.list_inbox(cancel=threading.Event())
    assert caught.value.response_diagnostic == diagnostic
    assert len(_metadata_calls(harness.http.calls[before:])) == 2
    assert harness.connector.account is not None
    _blocked_before_http(harness, old_request)


@pytest.mark.parametrize("field", ["id", "threadId"])
@pytest.mark.parametrize("invalid", [None, 3, "foreign\u200bidentifier"])
def test_identity_and_thread_rejections_are_never_hidden_as_control_omissions(
    harness: Harness, field: str, invalid: object
) -> None:
    harness.http.ids = ("good",)
    old = harness.connector.list_inbox(cancel=threading.Event())
    _, old_request = _request(harness, old, ("good",))
    harness.http.ids = ("good", "bad")
    malformed = _metadata("bad", [_RAW_CONTROL])
    malformed[field] = invalid
    _set_metadata(harness, "bad", malformed)
    with pytest.raises(GmailError) as caught:
        harness.connector.list_inbox(cancel=threading.Event())
    assert caught.value.code == ("account_mismatch" if field == "id" else "response_invalid")
    if field == "threadId":
        assert caught.value.response_diagnostic == "metadata_identifier"
    _blocked_before_http(harness, old_request)


def test_omitted_rows_still_use_one_original_rpc_and_deadline_budget(harness: Harness) -> None:
    harness.http.ids = tuple(f"row{index}" for index in range(20))
    for message_id in harness.http.ids[:-1]:
        _set_metadata(harness, message_id, _metadata(message_id, [_RAW_CONTROL]))
    harness.http.next_page = True

    def consume_time(call: Call) -> None:
        if parse_qs(urlsplit(call.url).query).get("format") == ["metadata"]:
            harness.clock.advance(1.49)

    harness.http.on_request = consume_time
    before = len(harness.http.calls)
    start = harness.clock.monotonic
    page = harness.connector.list_inbox(cancel=threading.Event())
    calls = harness.http.calls[before:]
    metadata = _metadata_calls(calls)
    assert len(calls) == 21
    assert len(metadata) == 20
    assert page.more_available
    assert page.omitted_count == 19
    assert page.catalog.message_ids == ("row19",)
    assert page.catalog.observed.monotonic == pytest.approx(start + 29.8)
    assert metadata[-1].timeout == pytest.approx(30 - 19 * 1.49)
    assert all("pageToken" not in parse_qs(urlsplit(call.url).query) for call in calls)


def test_omissions_cannot_reset_original_list_deadline(harness: Harness) -> None:
    harness.http.ids = tuple(f"row{index}" for index in range(20))
    for message_id in harness.http.ids:
        _set_metadata(harness, message_id, _metadata(message_id, [_RAW_CONTROL]))

    def consume_time(call: Call) -> None:
        if parse_qs(urlsplit(call.url).query).get("format") == ["metadata"]:
            harness.clock.advance(2)

    harness.http.on_request = consume_time
    before = len(harness.http.calls)
    with pytest.raises(GmailError, match="timeout"):
        harness.connector.list_inbox(cancel=threading.Event())
    assert len(_metadata_calls(harness.http.calls[before:])) == 15


def test_final_header_decoding_after_deadline_cannot_publish_catalog(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness.http.ids = ("last",)
    _set_metadata(harness, "last", _metadata("last"))

    def decode_after_budget(value: str) -> list[tuple[bytes | str, str | None]]:
        result = decode_header(value)
        harness.clock.advance(30)
        return result

    monkeypatch.setattr(connector_module, "decode_header", decode_after_budget)
    before = len(harness.http.calls)
    with pytest.raises(GmailError, match="timeout"):
        harness.connector.list_inbox(cancel=threading.Event())
    assert len(harness.http.calls) == before + 2


def _budgeted_page(h: Harness, total: int) -> None:
    h.http.ids = tuple(f"row{index}" for index in range(20))
    list_wire = encoded(
        {"messages": [{"id": item, "threadId": "synthetic-thread"} for item in h.http.ids]}
    )
    h.http.responses[_LIST] = list_wire
    values = [
        {**_metadata(item, [_RAW_CONTROL] if index < 19 else None), "opaque_padding": ""}
        for index, item in enumerate(h.http.ids)
    ]
    remaining = total - len(list_wire) - sum(len(encoded(value)) for value in values)
    assert remaining > 0
    wire_lengths: list[int] = []
    for message_id, value in zip(h.http.ids, values, strict=True):
        # Unknown JSON fields remain untrusted wire bytes; they cannot become a budget bypass.
        padding = min(remaining, 60000 - len(encoded(value)))
        value["opaque_padding"] = "x" * padding
        remaining -= padding
        raw = encoded(value)
        h.http.responses[f"{_LIST}/{message_id}"] = raw
        wire_lengths.append(len(raw))
        assert len(raw) <= 65536
    assert remaining == 0
    assert len(list_wire) + sum(wire_lengths) == total


@pytest.mark.parametrize("excess", [0, 1])
def test_aggregate_bytes_include_every_omitted_response_and_exact_boundary(
    harness: Harness, excess: int
) -> None:
    _budgeted_page(harness, MAX_MAIL_MESSAGE_BYTES + excess)
    before = len(harness.http.calls)
    if excess:
        with pytest.raises(GmailError, match="response_too_large"):
            harness.connector.list_inbox(cancel=threading.Event())
    else:
        page = harness.connector.list_inbox(cancel=threading.Event())
        assert page.omitted_count == 19
        assert page.catalog.message_ids == ("row19",)
    assert len(harness.http.calls) == before + 21


def test_one_omitted_response_cannot_exceed_per_exchange_byte_limit(harness: Harness) -> None:
    harness.http.ids = ("oversized", "never_fetched")
    value = {**_metadata("oversized", [_RAW_CONTROL]), "opaque_padding": ""}
    value["opaque_padding"] = "x" * (65537 - len(encoded(value)))
    _set_metadata(harness, "oversized", value)
    before = len(harness.http.calls)
    with pytest.raises(GmailError, match="response_too_large"):
        harness.connector.list_inbox(cancel=threading.Event())
    assert len(harness.http.calls) == before + 2


def test_newer_list_wins_over_an_older_delayed_metadata_result(harness: Harness) -> None:
    entered, release = threading.Event(), threading.Event()
    older_errors: list[GmailError] = []
    older_pages: list[GmailInboxPage] = []
    harness.http.ids = ("older",)
    _set_metadata(harness, "older", _metadata("older", [_RAW_CONTROL]))

    def delay_older(call: Call) -> None:
        if urlsplit(call.url).path == f"{_LIST}/older":
            entered.set()
            assert release.wait(5), "synthetic older exchange was not released"

    def run_older() -> None:
        try:
            older_pages.append(harness.connector.list_inbox(cancel=threading.Event()))
        except GmailError as error:
            older_errors.append(error)

    harness.http.on_request = delay_older
    worker = threading.Thread(target=run_older)
    worker.start()
    try:
        assert entered.wait(5), "synthetic older exchange did not start"
        harness.http.ids = ("newer",)
        newer = harness.connector.list_inbox(cancel=threading.Event())
        owner, request = _request(harness, newer, ("newer",))
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert not older_pages
    assert [error.code for error in older_errors] == ["cancelled"]
    _blocked_before_http(harness, _forge_request(request, "older"))
    harness.http.responses.clear()
    harness.http.on_request = None
    result = harness.connector.read_selected(request, cancel=threading.Event())
    snapshot = owner.accept_result(request, result)
    assert tuple(item.source.message_id for item in snapshot.messages) == ("newer",)


def test_partial_page_dto_contains_only_aggregates_for_unavailable_rows(harness: Harness) -> None:
    page = _mixed(harness)
    serialized = json.loads(page.model_dump_json())
    assert set(serialized["omissions"][0]) == {"reason", "count"}
    assert serialized["omissions"][0]["count"] == 1
    assert _OMITTED_ID not in page.model_dump_json()
