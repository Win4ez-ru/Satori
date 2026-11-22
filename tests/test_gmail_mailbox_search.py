"""Explicit mailbox queries through synthetic HTTP; no network or source storage."""

import json
import socket
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from threading import Event
from typing import Literal, Never
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr, ValidationError

from satori.application.assistant.contracts import LocalPrincipal
from satori.application.assistant.mail import MailAccessCoordinator
from satori.application.assistant.mail_contracts import MailAccessError, MailAccountRef, MailClock
from satori.application.assistant.mail_search_contracts import (
    MAX_MAIL_SEARCH_QUERY,
    MailSearchRequest,
    valid_mail_search_query,
)
from satori.infrastructure.gmail.connector import GmailConnector, _Session
from satori.infrastructure.gmail.contracts import GmailAccount, GmailError, GmailSearchPage
from satori.infrastructure.gmail.http import LIST_FIELDS, GoogleHTTP, _allow_endpoint

ORIGIN = "https://gmail.googleapis.com/gmail/v1/users/me/messages"


class Clock:
    def __init__(self) -> None:
        self.wall = datetime(2026, 11, 5, tzinfo=UTC)
        self.monotonic = 1000.0

    def __call__(self) -> MailClock:
        return MailClock(wall=self.wall, monotonic=self.monotonic)

    def advance(self, seconds: float) -> None:
        self.wall += timedelta(seconds=seconds)
        self.monotonic += seconds


class WireHTTP(GoogleHTTP):
    def __init__(self) -> None:
        self.ids: tuple[str, ...] = ("m1", "m2")
        self.calls: list[str] = []
        self.before: Callable[[str], None] | None = None
        self.list_override: object | None = None
        self.headers: dict[str, object] = {}
        self.more = False

    def request(
        self,
        method: Literal["GET", "POST"],
        url: str,
        *,
        token: SecretStr | None = None,
        form: dict[str, str] | None = None,
        max_bytes: int = 524288,
        cancel: Event,
        timeout: float = 10,
    ) -> bytes:
        assert method == "GET"
        assert form is None
        assert token is not None
        assert 0 < timeout <= 10
        assert max_bytes <= 65536
        assert not cancel.is_set()
        self.calls.append(url)
        if self.before is not None:
            self.before(url)
        parsed = urlsplit(url)
        if parsed.path.endswith("/messages"):
            data = self.list_override
            if data is None:
                listing: dict[str, object] = {
                    "messages": [{"id": identifier} for identifier in self.ids]
                }
                if self.more:
                    listing["nextPageToken"] = "fixture-next-token"
                data = listing
        else:
            assert parse_qs(parsed.query)["format"] == ["metadata"]
            identifier = parsed.path.rsplit("/", 1)[1]
            data = self.headers.get(identifier)
            if data is None:
                data = {
                    "id": identifier,
                    "payload": {"headers": [{"name": "Subject", "value": "unrelated header"}]},
                }
        return json.dumps(data).encode()


def connected() -> tuple[GmailConnector, WireHTTP, Clock, LocalPrincipal]:
    principal = LocalPrincipal(os_uid=501, profile_id=uuid4(), session_id=uuid4())
    clock = Clock()
    wire = WireHTTP()
    connector = GmailConnector(principal, http=wire, clock=clock)
    account = GmailAccount(
        account=MailAccountRef(subject="fixture-subject", client_registration="fixture-client"),
        email_label="fixture@example.invalid",
    )
    connector._session = _Session(
        account,
        SecretStr("fixture-access-token"),
        None,
        clock.wall + timedelta(hours=1),
        clock.monotonic + 3600,
        0,
    )
    return connector, wire, clock, principal


def request_for(
    connector: GmailConnector, clock: Clock, principal: LocalPrincipal, query: str = "билеты"
) -> MailSearchRequest:
    account = connector.account
    assert account is not None
    return MailSearchRequest(
        request_id=uuid4(),
        principal=principal,
        account=account.account,
        query=query,
        created=clock(),
        expires_at=clock.wall + timedelta(seconds=30),
        monotonic_deadline=clock.monotonic + 30,
    )


def search_url(query: str = "билеты", **extra: str) -> str:
    return (
        ORIGIN
        + "?"
        + urlencode(
            {
                "q": query,
                "maxResults": "20",
                "includeSpamTrash": "true",
                "fields": LIST_FIELDS,
                **extra,
            }
        )
    )


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> Never:
        raise AssertionError("network_forbidden")

    monkeypatch.setattr(socket, "create_connection", forbidden)


@pytest.mark.parametrize("query", ["", " ", "a\nb", "\t", "\x00", "a\u202eb", "a\ud800", "x" * 513])
def test_search_contract_rejects_empty_controls_and_excess(query: str) -> None:
    connector, _, clock, principal = connected()
    assert not valid_mail_search_query(query)
    with pytest.raises(ValidationError):
        request_for(connector, clock, principal, query)


def test_explicit_query_searches_all_mail_and_fetches_only_provider_matches_headers() -> None:
    connector, wire, clock, principal = connected()
    request = request_for(connector, clock, principal, 'from:(person@example.invalid) "билеты"')
    result = connector.search_headers(request, cancel=Event())
    assert result.request == request
    assert result.page.catalog.account == request.account
    assert result.page.catalog.principal == principal
    assert [item.subject for item in result.page.messages] == [
        "unrelated header",
        "unrelated header",
    ]
    assert parse_qs(urlsplit(wire.calls[0]).query) == {
        "q": [request.query],
        "maxResults": ["20"],
        "includeSpamTrash": ["true"],
        "fields": [LIST_FIELDS],
    }
    assert len(wire.calls) == 3
    assert all("format=metadata" in url for url in wire.calls[1:])
    assert "fixture-access-token" not in repr(result)
    assert request.query not in repr(request)
    assert request.query not in repr(result)


def test_inbox_legacy_route_remains_exact_and_has_no_query() -> None:
    connector, wire, _, _ = connected()
    connector.list_headers(cancel=Event())
    assert parse_qs(urlsplit(wire.calls[0]).query) == {
        "labelIds": ["INBOX"],
        "maxResults": ["20"],
        "includeSpamTrash": ["false"],
        "fields": [LIST_FIELDS],
    }


@pytest.mark.parametrize(
    "field", ["request_id", "expires_at", "monotonic_deadline", "lifetime_mismatch"]
)
def test_request_contract_rejects_invalid_identity_or_lifetime(field: str) -> None:
    connector, _, clock, principal = connected()
    values = request_for(connector, clock, principal).model_dump()
    if field == "request_id":
        values[field] = UUID(int=0)
    elif field == "expires_at":
        values[field] = clock.wall + timedelta(seconds=31)
    elif field == "monotonic_deadline":
        values[field] = float("inf")
    else:
        values["monotonic_deadline"] = clock.monotonic + 29
    with pytest.raises(ValidationError):
        MailSearchRequest.model_validate(values)


@pytest.mark.parametrize("wrong", ["principal", "account", "query", "type"])
def test_invalid_or_forged_request_makes_no_http(wrong: str) -> None:
    connector, wire, clock, principal = connected()
    request = request_for(connector, clock, principal)
    if wrong == "principal":
        request = request.model_copy(
            update={"principal": principal.model_copy(update={"session_id": uuid4()})}
        )
    elif wrong == "account":
        request = request.model_copy(
            update={"account": request.account.model_copy(update={"subject": "other"})}
        )
    elif wrong == "query":
        request = request.model_copy(update={"query": "x\n"})
    else:
        request = request.model_dump()  # type: ignore[assignment]
    with pytest.raises(GmailError, match=r"account_mismatch|request_invalid"):
        connector.search_headers(request, cancel=Event())
    assert not wire.calls


@pytest.mark.parametrize("expiry", ["wall", "mono", "both", "future"])
def test_request_freshness_checked_before_network(expiry: str) -> None:
    connector, wire, clock, principal = connected()
    request = request_for(connector, clock, principal)
    if expiry in {"wall", "both"}:
        clock.wall += timedelta(seconds=30)
    if expiry in {"mono", "both"}:
        clock.monotonic += 30
    if expiry == "future":
        request = request.model_copy(
            update={
                "created": MailClock(
                    wall=clock.wall + timedelta(seconds=1), monotonic=clock.monotonic + 1
                ),
                "expires_at": clock.wall + timedelta(seconds=31),
                "monotonic_deadline": clock.monotonic + 31,
            }
        )
    with pytest.raises(GmailError, match=r"timeout|request_invalid"):
        connector.search_headers(request, cancel=Event())
    assert not wire.calls


def test_cancelled_before_start_does_not_access_provider() -> None:
    connector, wire, clock, principal = connected()
    cancel = Event()
    cancel.set()
    with pytest.raises(GmailError, match="cancelled"):
        connector.search_headers(request_for(connector, clock, principal), cancel=cancel)
    assert not wire.calls


@pytest.mark.parametrize("change", ["cancel", "disconnect", "deadline", "rollback", "supersede"])
def test_inflight_changes_fence_result_before_metadata(change: str) -> None:
    connector, wire, clock, principal = connected()
    request = request_for(connector, clock, principal)
    cancel = Event()

    def change_after_list(url: str) -> None:
        wire.before = None
        if change == "cancel":
            cancel.set()
        elif change == "disconnect":
            connector.disconnect()
        elif change == "deadline":
            clock.advance(30)
        elif change == "rollback":
            clock.advance(-1)
        else:
            connector.list_headers(cancel=Event())

    wire.before = change_after_list
    with pytest.raises(GmailError, match=r"cancelled|timeout|auth_expired"):
        connector.search_headers(request, cancel=cancel)
    assert connector._catalog is None or change == "supersede"
    assert len(wire.calls) == (4 if change == "supersede" else 1)


@pytest.mark.parametrize("mode", ["empty", "partial", "unsupported", "more"])
def test_empty_partial_and_more_are_reported_without_fabricated_hits(mode: str) -> None:
    connector, wire, clock, principal = connected()
    if mode == "empty":
        wire.ids = ()
    if mode in {"partial", "unsupported"}:
        wire.headers["m1"] = {
            "id": "m1",
            "payload": {"headers": [{"name": "Subject", "value": "bad\u202e"}]},
        }
    if mode == "unsupported":
        wire.headers["m2"] = {
            "id": "m2",
            "payload": {"headers": [{"name": "Subject", "value": "bad\u202e"}]},
        }
    wire.more = mode == "more"
    result = connector.search_headers(request_for(connector, clock, principal), cancel=Event()).page
    assert result.more_available == (mode == "more")
    assert result.omitted_count == {"empty": 0, "partial": 1, "unsupported": 2, "more": 0}[mode]
    assert len(result.messages) == {"empty": 0, "partial": 1, "unsupported": 0, "more": 2}[mode]
    assert (
        result.completeness
        == {
            "empty": "complete",
            "partial": "partial",
            "unsupported": "unsupported",
            "more": "complete",
        }[mode]
    )


@pytest.mark.parametrize(
    "bad",
    [
        {"messages": [{"id": "m1"}] * 21},
        {"messages": [{"id": "m1"}] * 2},
        {"messages": {}},
        {"messages": [{"id": "x/other"}]},
        {"nextPageToken": []},
    ],
)
def test_malformed_provider_list_cannot_publish_search_catalog(bad: object) -> None:
    connector, wire, clock, principal = connected()
    wire.list_override = bad
    with pytest.raises(GmailError, match="response_invalid"):
        connector.search_headers(request_for(connector, clock, principal), cancel=Event())
    assert len(wire.calls) == 1
    assert connector._catalog is None


def test_search_results_do_not_grant_body_access_to_other_mail() -> None:
    connector, _, clock, principal = connected()
    page = connector.search_headers(request_for(connector, clock, principal), cancel=Event()).page
    owner = MailAccessCoordinator(principal, clock=clock)
    owner.admit_catalog(page.catalog)
    with pytest.raises(MailAccessError, match="selection_invalid"):
        owner.select(("outside-results",), confirmed=True)


def test_result_wrapper_rejects_other_account_or_late_observation() -> None:
    connector, _, clock, principal = connected()
    result = connector.search_headers(request_for(connector, clock, principal), cancel=Event())
    bad_request = result.request.model_copy(
        update={"account": result.request.account.model_copy(update={"subject": "other"})}
    )
    with pytest.raises(ValidationError, match="gmail_search_page_invalid"):
        GmailSearchPage(request=bad_request, page=result.page)
    old = result.request.model_copy(
        update={
            "created": MailClock(
                wall=clock.wall - timedelta(seconds=30), monotonic=clock.monotonic - 30
            ),
            "expires_at": clock.wall,
            "monotonic_deadline": clock.monotonic,
        }
    )
    with pytest.raises(ValidationError, match="gmail_search_page_invalid"):
        GmailSearchPage(request=old, page=result.page)


@pytest.mark.parametrize(
    "query",
    [
        "билеты",
        'from:(fixture@example.invalid) "予約"',
        "&fields=evil",
        "📬" * MAX_MAIL_SEARCH_QUERY,
    ],
)
def test_http_allows_only_closed_encoded_utf8_query_variant(query: str) -> None:
    assert _allow_endpoint("GET", search_url(query)) == "read"
    assert parse_qs(urlsplit(search_url(query)).query)["q"] == [query]


@pytest.mark.parametrize(
    "url",
    [
        search_url(" "),
        search_url("x\n"),
        search_url("x\u202e"),
        search_url("x" * 513),
        search_url(extra="no"),
        search_url(labelIds="INBOX"),
        search_url(maxResults="21"),
        search_url(includeSpamTrash="false"),
        search_url(fields="*"),
        search_url() + "&q=second",
        search_url() + "&pageToken=unapproved",
        ORIGIN + "?q=%FF&maxResults=20&includeSpamTrash=true&fields=" + LIST_FIELDS,
        ORIGIN + "?q=%F0%80%80%80&maxResults=20&includeSpamTrash=true&fields=" + LIST_FIELDS,
    ],
)
def test_http_rejects_broader_or_malformed_search_routes(url: str) -> None:
    with pytest.raises(GmailError, match="request_invalid"):
        _allow_endpoint("GET", url)


def test_http_search_remains_read_only() -> None:
    with pytest.raises(GmailError, match="request_invalid"):
        _allow_endpoint("POST", search_url())
    with pytest.raises(GmailError, match="request_invalid"):
        _allow_endpoint("GET", ORIGIN + "/send?" + urlencode({"q": "билеты"}))


def test_twenty_returned_matches_remain_bounded_and_show_more_without_extra_fetches() -> None:
    connector, wire, clock, principal = connected()
    wire.ids = tuple(f"m{index}" for index in range(20))
    wire.more = True
    result = connector.search_headers(request_for(connector, clock, principal), cancel=Event())
    assert len(result.page.messages) == 20
    assert result.page.more_available
    assert len(wire.calls) == 21


def test_search_retires_prior_body_membership_even_while_provider_list_is_inflight() -> None:
    connector, wire, clock, principal = connected()
    old_page = connector.list_headers(cancel=Event())
    owner = MailAccessCoordinator(principal, clock=clock)
    owner.admit_catalog(old_page.catalog)
    proof = owner.select(("m1",), confirmed=True)
    owner.issue(proof, confirmed=True)
    old_read = owner.prepare_read()
    checked: list[bool] = []

    def during_search(url: str) -> None:
        wire.before = None
        with pytest.raises(GmailError, match="request_invalid"):
            connector.read_selected(old_read, cancel=Event())
        checked.append(True)

    wire.before = during_search
    connector.search_headers(request_for(connector, clock, principal), cancel=Event())
    assert checked == [True]
    with pytest.raises(GmailError, match="request_invalid"):
        connector.read_selected(old_read, cancel=Event())
    assert len(wire.calls) == 6
    assert all("format=full" not in url for url in wire.calls)


def test_expiry_during_last_header_fetch_never_publishes_catalog() -> None:
    connector, wire, clock, principal = connected()
    request = request_for(connector, clock, principal)

    def expire_last(url: str) -> None:
        if urlsplit(url).path.endswith("/m2"):
            clock.advance(30)

    wire.before = expire_last
    with pytest.raises(GmailError, match="timeout"):
        connector.search_headers(request, cancel=Event())
    assert connector._catalog is None
    assert len(wire.calls) == 3
