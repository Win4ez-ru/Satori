"""Whole-message metadata omissions using bounded in-memory fake HTTP only."""

import json
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pytest
from pydantic import SecretStr, ValidationError

from satori.application.assistant.contracts import LocalPrincipal
from satori.application.assistant.mail_contracts import MailAccountRef, MailClock
from satori.infrastructure.gmail.connector import GmailConnector, _Session
from satori.infrastructure.gmail.contracts import (
    GmailAccount,
    GmailError,
    GmailHeaderOmission,
    GmailInboxPage,
)
from satori.infrastructure.gmail.http import METADATA_FIELDS, METADATA_HEADERS, GoogleHTTP

RAW: Literal["metadata_header_value_controls"] = "metadata_header_value_controls"
DECODED: Literal["metadata_header_decoded_controls"] = "metadata_header_decoded_controls"


def message(identifier: str, value: str = "safe fixture") -> dict[str, object]:
    return {
        "id": identifier,
        "threadId": "fixture-thread",
        "payload": {
            "headers": [
                {"name": "Subject", "value": value},
            ]
        },
    }


class FakeHTTP(GoogleHTTP):
    def __init__(self, rows: dict[str, dict[str, object]]) -> None:
        self.rows = rows
        self.calls: list[str] = []
        self.more = False
        self.before: Callable[[str], None] | None = None
        self.failure: GmailError | None = None

    def request(
        self,
        method: Literal["GET", "POST"],
        url: str,
        *,
        token: SecretStr | None = None,
        form: dict[str, str] | None = None,
        max_bytes: int = 524288,
        cancel: threading.Event,
        timeout: float = 10,
    ) -> bytes:
        assert method == "GET"
        assert token is not None
        assert form is None
        assert not cancel.is_set()
        assert 0 < timeout <= 10
        parsed = urlsplit(url)
        assert parsed.netloc == "gmail.googleapis.com"
        self.calls.append(parsed.path)
        if self.before is not None:
            self.before(parsed.path)
        if self.failure is not None:
            raise self.failure
        if parsed.path == "/gmail/v1/users/me/messages":
            query = parse_qs(parsed.query)
            assert query["maxResults"] == ["20"]
            assert "pageToken" not in query
            result: dict[str, object] = {"messages": [{"id": item} for item in self.rows]}
            if self.more:
                result["nextPageToken"] = "synthetic-next-page"
        else:
            query = parse_qs(parsed.query)
            assert query["format"] == ["metadata"]
            assert query["fields"] == [METADATA_FIELDS]
            assert query["metadataHeaders"] == list(METADATA_HEADERS)
            result = self.rows[parsed.path.rsplit("/", 1)[1]]
        raw = json.dumps(result).encode()
        assert len(raw) <= max_bytes
        return raw


def connected(rows: dict[str, dict[str, object]]) -> tuple[GmailConnector, FakeHTTP]:
    principal = LocalPrincipal(os_uid=501, profile_id=UUID(int=1), session_id=UUID(int=2))
    now = MailClock(wall=datetime(2026, 10, 30, tzinfo=UTC), monotonic=100.0)
    http = FakeHTTP(rows)
    connector = GmailConnector(principal, http=http, clock=lambda: now)
    connector._session = _Session(
        account=GmailAccount(
            account=MailAccountRef(
                subject="fixture-subject", client_registration="fixture.apps.googleusercontent.com"
            ),
            email_label="fixture@example.test",
        ),
        access=SecretStr("NOT-A-REAL-TOKEN"),
        refresh=None,
        expires_at=now.wall + timedelta(seconds=3600),
        deadline=3700.0,
        generation=0,
    )
    return connector, http


def test_partial_catalog_contains_only_admitted_claims_and_resources() -> None:
    connector, http = connected(
        {
            "safe": message("safe"),
            "raw-dropped": message("raw-dropped", "PRIVATE-CONTROL\x00"),
            "decoded-dropped": message("decoded-dropped", "=?utf-8?q?PRIVATE=00TEXT?="),
        }
    )
    http.more = True
    page = connector.list_headers(cancel=threading.Event())
    assert page.completeness == "partial"
    assert page.total_rows == 3
    assert page.omitted_count == 2
    assert page.catalog.message_ids == ("safe",)
    assert tuple(item.source.message_id for item in page.messages) == ("safe",)
    assert {item.reason: item.count for item in page.omissions} == {RAW: 1, DECODED: 1}
    assert page.more_available
    assert len(http.calls) == 4
    assert connector.account is not None
    for text in ("raw-dropped", "decoded-dropped", "PRIVATE-CONTROL", "PRIVATE", "TEXT"):
        assert text not in page.model_dump_json()
        assert text not in repr(page)
    assert "total_rows" not in page.model_dump()
    assert "omitted_count" not in page.model_dump()
    assert GmailInboxPage.model_validate(page.model_dump()) == page


def test_all_control_bad_is_unsupported_not_a_complete_empty_inbox() -> None:
    connector, http = connected(
        {f"dropped-{index}": message(f"dropped-{index}", "bad\x00") for index in range(20)}
    )
    page = connector.list_headers(cancel=threading.Event())
    assert page.completeness == "unsupported"
    assert page.catalog.message_ids == ()
    assert page.messages == ()
    assert page.total_rows == page.omitted_count == 20
    assert page.omissions == (GmailHeaderOmission(reason=RAW, count=20),)
    assert len(http.calls) == 21
    assert connector._catalog == page.catalog
    empty_connector, empty_http = connected({})
    empty = empty_connector.list_headers(cancel=threading.Event())
    assert empty.completeness == "complete"
    assert empty.total_rows == empty.omitted_count == 0
    assert empty.omissions == ()
    assert len(empty_http.calls) == 1


@pytest.mark.parametrize(
    "value", ["fixture\ttext", "fixture\r\n\ttext", "=?utf-8?q?fixture=09text?="]
)
def test_allowed_horizontal_whitespace_never_causes_omission(value: str) -> None:
    connector, _ = connected({"safe": message("safe", value)})
    page = connector.list_headers(cancel=threading.Event())
    assert page.completeness == "complete"
    assert page.omissions == ()
    assert len(page.messages) == 1


@pytest.mark.parametrize(
    "value", ["bad\x00", "bad\rtext", "bad\ntext", "bad\u202etext", "bad\ud800text"]
)
def test_raw_controls_omit_entire_message_without_claim_objects(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import satori.infrastructure.gmail.connector as module

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("unsafe_message_must_not_construct_claims")

    monkeypatch.setattr(module, "MailHeaderClaim", forbidden)
    connector, _ = connected({"dropped": message("dropped", value)})
    page = connector.list_headers(cancel=threading.Event())
    assert page.completeness == "unsupported"
    assert page.omissions == (GmailHeaderOmission(reason=RAW, count=1),)


def test_one_message_with_both_control_classes_counts_once_with_raw_precedence() -> None:
    connector, _ = connected(
        {
            "dropped": {
                "id": "dropped",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "=?utf-8?q?decoded=00?="},
                        {"name": "To", "value": "raw\x00"},
                    ]
                },
            }
        }
    )
    page = connector.list_headers(cancel=threading.Event())
    assert page.total_rows == page.omitted_count == 1
    assert page.omissions == (GmailHeaderOmission(reason=RAW, count=1),)


@pytest.mark.parametrize(
    ("later", "diagnostic"),
    [
        (None, "metadata_header_shape"),
        ({"name": "Private", "value": "x"}, "metadata_header_name"),
        ({"name": "subject", "value": "x"}, "metadata_header_duplicate"),
        ({"name": "To", "value": []}, "metadata_header_value_type"),
        ({"name": "To", "value": "x" * 2049}, "metadata_header_value_limit"),
        ({"name": "To", "value": "=?not-a-supported-charset?q?x?="}, "metadata_header_decoding"),
        (
            {"name": "To", "value": "\u0400" * 400 + " =?utf-8?q?x?="},
            "metadata_header_decoded_limit",
        ),
    ],
)
def test_control_first_cannot_hide_later_structural_codec_or_size_failure(
    later: object, diagnostic: str
) -> None:
    connector, _ = connected(
        {
            "dropped": {
                "id": "dropped",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "bad\x00"},
                        later,
                    ]
                },
            }
        }
    )
    with pytest.raises(GmailError) as error:
        connector.list_headers(cancel=threading.Event())
    assert error.value.code == "response_invalid"
    assert error.value.response_diagnostic == diagnostic
    assert connector._catalog is None


@pytest.mark.parametrize(
    ("value", "diagnostic"),
    [
        ("\x00 =?not-a-supported-charset?q?x?=", "metadata_header_decoding"),
        ("\u200f" * 400 + " =?utf-8?q?x?=", "metadata_header_decoded_limit"),
    ],
)
def test_raw_control_cannot_hide_codec_or_decoded_limit_in_same_field(
    value: str, diagnostic: str
) -> None:
    connector, _ = connected({"dropped": message("dropped", value)})
    with pytest.raises(GmailError) as error:
        connector.list_headers(cancel=threading.Event())
    assert error.value.response_diagnostic == diagnostic
    assert connector._catalog is None


@pytest.mark.parametrize("thread_id", [None, "", [], "unsafe*thread", "thread\x00"])
def test_thread_identity_is_validated_before_control_omission(thread_id: object) -> None:
    bad = message("dropped", "bad\x00")
    bad["threadId"] = thread_id
    connector, _ = connected({"dropped": bad})
    with pytest.raises(GmailError) as error:
        connector.list_headers(cancel=threading.Event())
    assert error.value.response_diagnostic == "metadata_identifier"
    assert connector._catalog is None


def test_message_identity_mismatch_is_not_an_omission() -> None:
    connector, _ = connected({"requested": message("foreign", "bad\x00")})
    with pytest.raises(GmailError, match="account_mismatch"):
        connector.list_headers(cancel=threading.Event())
    assert connector._catalog is None


@pytest.mark.parametrize("count", [0, 21, True])
def test_omission_counts_are_strict_bounded(count: object) -> None:
    with pytest.raises(ValidationError):
        GmailHeaderOmission.model_validate({"reason": RAW, "count": count})


@pytest.mark.parametrize("mutation", ["count", "state", "duplicate", "catalog_ids", "account"])
def test_page_revalidates_counts_completeness_and_exact_catalog_binding(mutation: str) -> None:
    connector, _ = connected({"safe": message("safe"), "dropped": message("dropped", "bad\x00")})
    page = connector.list_headers(cancel=threading.Event())
    data = page.model_dump()
    if mutation == "count":
        data["omissions"] = ({"reason": RAW, "count": 20},)
    elif mutation == "state":
        data["completeness"] = "complete"
    elif mutation == "duplicate":
        data["omissions"] = ({"reason": RAW, "count": 1}, {"reason": RAW, "count": 1})
    elif mutation == "catalog_ids":
        data["catalog"]["message_ids"] = ("dropped",)
    else:
        data["messages"][0]["source"]["account"]["subject"] = "foreign"
    with pytest.raises(ValidationError):
        GmailInboxPage.model_validate(data)


def test_new_list_clears_previous_catalog_before_any_http_even_on_failure() -> None:
    connector, http = connected({"safe": message("safe")})
    old = connector.list_headers(cancel=threading.Event())
    assert connector._catalog == old.catalog
    http.before = lambda path: assert_cleared(connector)
    http.failure = GmailError("http_unavailable")
    with pytest.raises(GmailError, match="http_unavailable"):
        connector.list_headers(cancel=threading.Event())
    assert connector._catalog is None
    assert connector.account is not None


def assert_cleared(connector: GmailConnector) -> None:
    assert connector._catalog is None
