"""Synthetic branch discrimination; no account, browser, mail payload or network I/O."""

import json
import os
import ssl
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.message import Message
from pathlib import Path
from threading import Event
from typing import Literal, cast, get_args
from urllib.parse import urlencode, urlsplit
from urllib.request import Request
from uuid import uuid4

import pytest
from pydantic import SecretStr

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from satori.application.assistant.contracts import LocalPrincipal
from satori.application.assistant.mail_contracts import (
    MailAccessDiagnostic,
    MailAccountRef,
    MailClock,
)
from satori.desktop.mail_controller import (
    MailActionReceipt,
    MailController,
    _release_worker,
    _Worker,
)
from satori.desktop.mail_ui import _RESPONSE_DIAGNOSTICS, _receipt_text
from satori.infrastructure.gmail.connector import GmailConnector, _header_text, _Session
from satori.infrastructure.gmail.contracts import (
    GmailAccount,
    GmailError,
    GmailResponseDiagnostic,
    gmail_response_diagnostic,
)
from satori.infrastructure.gmail.http import LIST_FIELDS, GoogleHTTP, GoogleResponse

NOW = datetime(2026, 10, 29, tzinfo=UTC)
LIST_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages?" + urlencode(
    {"labelIds": "INBOX", "maxResults": "20", "includeSpamTrash": "false", "fields": LIST_FIELDS}
)
MARKER = "SYNTHETIC-PRIVATE-CONTENT-MUST-NOT-LEAK"


def wire(value: object) -> bytes:
    return json.dumps(value).encode()


def metadata(headers: object = None, *, payload: object = None) -> bytes:
    return wire(
        {"id": "m1", "payload": payload if payload is not None else {"headers": headers or []}}
    )


class InjectedHTTP:
    def __init__(self, list_raw: bytes, metadata_raw: bytes) -> None:
        self.list_raw = list_raw
        self.metadata_raw = metadata_raw
        self.requests = 0

    def request(self, method: str, url: str, **kwargs: object) -> bytes:
        del kwargs
        assert method == "GET"
        self.requests += 1
        if urlsplit(url).path.endswith("/messages"):
            return self.list_raw
        assert urlsplit(url).path.endswith("/messages/m1")
        return self.metadata_raw


def connector(http: InjectedHTTP) -> GmailConnector:
    principal = LocalPrincipal(os_uid=501, profile_id=uuid4(), session_id=uuid4())
    account = GmailAccount(
        account=MailAccountRef(
            subject="synthetic", client_registration="synthetic.apps.googleusercontent.com"
        ),
        email_label="synthetic@example.test",
    )
    instance = GmailConnector(
        principal, http=cast(GoogleHTTP, http), clock=lambda: MailClock(wall=NOW, monotonic=1000.0)
    )
    instance._session = _Session(
        account, SecretStr("synthetic-token"), None, NOW + timedelta(hours=1), 4600.0, 0
    )
    return instance


CASES: list[tuple[bytes, bytes, GmailResponseDiagnostic]] = [
    (b"{", metadata(), "list_json"),
    (b'{"messages":[],"messages":[]}', metadata(), "list_json"),
    (wire({"messages": {}}), metadata(), "list_rows_shape"),
    (wire({"messages": [{"id": "m1"}] * 21}), metadata(), "list_rows_count"),
    (wire({"messages": ["bad"]}), metadata(), "list_row_shape"),
    (wire({"messages": [{"id": "\n"}]}), metadata(), "list_identifier"),
    (wire({"messages": [{"id": "m1"}, {"id": "m1"}]}), metadata(), "list_duplicate_identifier"),
    (wire({"messages": [], "nextPageToken": "\n"}), metadata(), "list_cursor"),
]
LIST = wire({"messages": [{"id": "m1"}]})
CASES += [
    (LIST, b"{", "metadata_json"),
    (LIST, wire({"id": "m1", "threadId": "\n"}), "metadata_identifier"),
    (LIST, metadata(payload="bad"), "metadata_payload_shape"),
    (LIST, wire({"id": "m1", "payload": {"headers": {}}}), "metadata_headers_shape"),
    (LIST, metadata([{"name": "Subject", "value": "a"}] * 8), "metadata_header_count"),
    (LIST, metadata(["bad"]), "metadata_header_shape"),
    (LIST, metadata([{"name": "Bcc", "value": "a"}]), "metadata_header_name"),
    (LIST, metadata([{"name": "Sub\nject", "value": "a"}]), "metadata_header_name"),
    (
        LIST,
        metadata([{"name": "Subject", "value": "a"}, {"name": "subject", "value": "b"}]),
        "metadata_header_duplicate",
    ),
    (LIST, metadata([{"name": "Subject", "value": None}]), "metadata_header_value_type"),
    (
        LIST,
        metadata([{"name": "To", "value": "recipient@example.test, " * 100}]),
        "metadata_header_value_limit",
    ),
    (LIST, metadata([{"name": "Subject", "value": "a\u200bb"}]), "metadata_header_value_controls"),
    (
        LIST,
        metadata([{"name": "Subject", "value": "=?unknown-charset?b?YQ==?="}]),
        "metadata_header_decoding",
    ),
    (
        LIST,
        metadata([{"name": "Subject", "value": "=?utf-8?b?Cg==?="}]),
        "metadata_header_decoded_controls",
    ),
]


@pytest.mark.parametrize(("list_raw", "metadata_raw", "expected"), CASES)
def test_list_rejections_keep_code_and_add_closed_branch(
    list_raw: bytes, metadata_raw: bytes, expected: GmailResponseDiagnostic
) -> None:
    http = InjectedHTTP(list_raw, metadata_raw)
    if expected in {"metadata_header_value_controls", "metadata_header_decoded_controls"}:
        page = connector(http).list_inbox(cancel=Event())
        assert page.completeness == "unsupported"
        assert page.messages == ()
        assert page.catalog.message_ids == ()
        assert page.omitted_count == 1
        assert page.omissions[0].reason == expected
        with pytest.raises(GmailError) as rejected:
            GmailConnector._header_claims(json.loads(metadata_raw)["payload"]["headers"])
        assert rejected.value.response_diagnostic == expected
        assert str(rejected.value) == "response_invalid"
        assert http.requests == 2
        return
    with pytest.raises(GmailError) as result:
        connector(http).list_inbox(cancel=Event())
    error = result.value
    assert error.code == "response_invalid"
    assert error.response_diagnostic == expected
    assert str(error) == "response_invalid"
    assert MARKER not in repr(error)
    assert http.requests <= 2


def test_long_google_deliverable_recipient_field_still_rejected_without_cap_change() -> None:
    recipient = "Synthetic Recipient <recipient@example.test>, " * 55
    assert 2048 < len(recipient) < 32000
    with pytest.raises(GmailError) as result:
        GmailConnector._header_claims([{"name": "To", "value": recipient}])
    assert result.value.response_diagnostic == "metadata_header_value_limit"


def test_clean_metadata_and_horizontal_tabs_remain_accepted() -> None:
    http = InjectedHTTP(LIST, metadata([{"name": "Subject", "value": "safe\tvalue"}]))
    page = connector(http).list_inbox(cancel=Event())
    assert len(page.messages) == 1
    assert page.messages[0].subject == "safe\tvalue"
    assert http.requests == 2


def test_decoded_limit_has_a_closed_branch_with_unchanged_limit() -> None:
    with pytest.raises(GmailError) as result:
        _header_text("a" * 2049, decoded=True)
    assert result.value.response_diagnostic == "metadata_header_decoded_limit"


class MemoryResponse:
    def __init__(self) -> None:
        self.status = 200
        self.headers = Message()
        self.bad_body_type = False
        self._read = False

    def geturl(self) -> str:
        return LIST_URL

    def read1(self, size: int) -> bytes:
        assert size > 0
        if self._read:
            return b""
        self._read = True
        return cast(bytes, "bad") if self.bad_body_type else b"{}"

    def close(self) -> None:
        pass


class InjectedOpener:
    def __init__(self, response: MemoryResponse) -> None:
        self.response = response
        self.calls = 0

    def open(self, request: Request, *, timeout: float) -> GoogleResponse:
        assert request.full_url == LIST_URL
        assert timeout > 0
        self.calls += 1
        return self.response


def header(name: str, value: str, twice: bool = False) -> Callable[[MemoryResponse], None]:
    def mutate(response: MemoryResponse) -> None:
        response.headers[name] = value
        if twice:
            response.headers[name] = value

    return mutate


HTTP_CASES: list[tuple[Callable[[MemoryResponse], None], GmailResponseDiagnostic]] = [
    (lambda r: setattr(r, "status", True), "http_status_type"),
    (header("Content-Range", "bytes 0-1/10"), "http_content_range"),
    (header("Content-Encoding", "gzip"), "http_content_encoding"),
    (header("Content-Length", "2", twice=True), "http_content_length_duplicate"),
    (header("Transfer-Encoding", "other"), "http_transfer_encoding"),
    (header("Content-Length", "invalid"), "http_content_length_syntax"),
    (lambda r: setattr(r, "bad_body_type", True), "http_body_type"),
    (header("Content-Length", "3"), "http_body_length_mismatch"),
]


@pytest.mark.parametrize(("mutate", "expected"), HTTP_CASES)
def test_http_framing_rejections_keep_code_and_add_closed_branch(
    mutate: Callable[[MemoryResponse], None], expected: GmailResponseDiagnostic
) -> None:
    response = MemoryResponse()
    mutate(response)
    opener = InjectedOpener(response)
    http = GoogleHTTP(opener=opener, context=ssl.create_default_context())
    with pytest.raises(GmailError) as result:
        http.request("GET", LIST_URL, token=SecretStr("synthetic-token"), cancel=Event())
    assert result.value.code == "response_invalid"
    assert result.value.response_diagnostic == expected
    assert str(result.value) == "response_invalid"
    assert opener.calls == 1


@pytest.fixture(scope="session")
def qt_app() -> QApplication:
    instance = QApplication.instance()
    if instance is None:
        return QApplication([])
    assert isinstance(instance, QApplication)
    return instance


@pytest.mark.parametrize("reason", get_args(GmailResponseDiagnostic))
def test_worker_receipt_ui_propagation_and_clearing(
    qt_app: QApplication, tmp_path: Path, reason: GmailResponseDiagnostic
) -> None:
    del qt_app
    controller = MailController(tmp_path)

    def fail(cancel: Event) -> object:
        assert not cancel.is_set()
        raise GmailError("response_invalid", response_diagnostic=reason)

    worker = _Worker("list", 0, controller.principal, fail)
    worker.run()
    assert worker.error == "response_invalid"
    assert worker.response_diagnostic == reason
    controller._failure("list", worker.error, response_diagnostic=worker.response_diagnostic)
    assert controller.last_receipt is not None
    assert controller.last_receipt.response_diagnostic == reason
    text = _receipt_text(controller.last_receipt)
    assert _RESPONSE_DIAGNOSTICS[reason] in text
    assert MARKER not in text
    controller._notify("listing")
    assert controller.response_diagnostic is None
    assert controller.last_receipt.response_diagnostic is None
    assert _RESPONSE_DIAGNOSTICS[reason] not in _receipt_text(controller.last_receipt)
    _release_worker(worker)
    assert worker.response_diagnostic is None
    controller.shutdown()


def test_foreign_diagnostic_is_dropped_and_receipt_rejects_manual_injection() -> None:
    unknown = cast(GmailResponseDiagnostic, MARKER)
    assert gmail_response_diagnostic("response_invalid", MARKER) is None
    assert gmail_response_diagnostic("http_error", "list_json") is None
    assert GmailError("response_invalid", response_diagnostic=unknown).response_diagnostic is None
    assert GmailError("http_error", response_diagnostic="list_json").response_diagnostic is None
    with pytest.raises(ValueError, match="mail_receipt_invalid"):
        MailActionReceipt("list", "response_invalid", NOW, response_diagnostic=unknown)
    receipt = MailActionReceipt("list", "response_invalid", NOW)
    object.__setattr__(receipt, "response_diagnostic", MARKER)
    assert MARKER not in _receipt_text(receipt)


def test_native_panel_shows_closed_diagnostic_and_clears_it(
    qt_app: QApplication, tmp_path: Path
) -> None:
    del qt_app
    from satori.desktop.mail_ui import MailPanel

    controller = MailController(tmp_path)
    panel = MailPanel(tmp_path, controller=controller)
    controller._failure(
        "list", "response_invalid", response_diagnostic="metadata_header_value_limit"
    )
    expected = _RESPONSE_DIAGNOSTICS["metadata_header_value_limit"]
    assert expected in panel.last_action.text()
    controller._notify("listing")
    assert expected not in panel.last_action.text()
    controller.shutdown()
    panel.deleteLater()


def test_worker_accepts_generic_error_without_diagnostic_attribute(
    qt_app: QApplication, tmp_path: Path
) -> None:
    del qt_app
    controller = MailController(tmp_path)
    error = GmailError("response_invalid")
    del error.response_diagnostic

    def fail(cancel: Event) -> object:
        assert not cancel.is_set()
        raise error

    worker = _Worker("list", 0, controller.principal, fail)
    worker.run()
    assert worker.error == "response_invalid"
    assert worker.response_diagnostic is None
    _release_worker(worker)
    controller.shutdown()


class NoneEqualitySpoof:
    def __eq__(self, other: object) -> bool:
        return other is None

    def __ne__(self, other: object) -> bool:
        return other is not None

    def __repr__(self) -> str:
        return MARKER


class StringSubclass(str):
    pass


BAD_DIAGNOSTICS: list[object] = [NoneEqualitySpoof(), StringSubclass("list_json"), MARKER, {}, []]


@pytest.mark.parametrize("value", BAD_DIAGNOSTICS)
@pytest.mark.parametrize("field", ["diagnostic", "response_diagnostic"])
def test_receipt_rejects_nonliteral_reasons_without_equality_dispatch(
    value: object, field: Literal["diagnostic", "response_diagnostic"]
) -> None:
    if field == "diagnostic":
        candidate = (
            StringSubclass("header_structure") if isinstance(value, StringSubclass) else value
        )
        with pytest.raises(ValueError, match="mail_receipt_invalid"):
            MailActionReceipt(
                "list", "invalid_header", NOW, diagnostic=cast(MailAccessDiagnostic, candidate)
            )
    else:
        with pytest.raises(ValueError, match="mail_receipt_invalid"):
            MailActionReceipt(
                "list",
                "response_invalid",
                NOW,
                response_diagnostic=cast(GmailResponseDiagnostic, value),
            )


@pytest.mark.parametrize("field", ["diagnostic", "response_diagnostic"])
def test_receipt_rejects_reason_under_wrong_code(
    field: Literal["diagnostic", "response_diagnostic"],
) -> None:
    if field == "diagnostic":
        with pytest.raises(ValueError, match="mail_receipt_invalid"):
            MailActionReceipt("list", "http_error", NOW, diagnostic="header_structure")
    else:
        with pytest.raises(ValueError, match="mail_receipt_invalid"):
            MailActionReceipt("list", "http_error", NOW, response_diagnostic="list_json")


@pytest.mark.parametrize("value", BAD_DIAGNOSTICS)
def test_foreign_response_reason_is_filtered_at_worker_controller_and_ui(
    qt_app: QApplication, tmp_path: Path, value: object
) -> None:
    del qt_app
    controller = MailController(tmp_path)
    error = GmailError("response_invalid")
    object.__setattr__(error, "response_diagnostic", value)

    def fail(cancel: Event) -> object:
        assert not cancel.is_set()
        raise error

    worker = _Worker("list", 0, controller.principal, fail)
    worker.run()
    assert worker.error == "response_invalid"
    assert worker.response_diagnostic is None
    controller._failure(
        "list", "response_invalid", response_diagnostic=cast(GmailResponseDiagnostic, value)
    )
    assert controller.response_diagnostic is None
    assert controller.last_receipt is not None
    assert controller.last_receipt.response_diagnostic is None
    object.__setattr__(controller.last_receipt, "response_diagnostic", value)
    rendered = _receipt_text(controller.last_receipt)
    assert MARKER not in rendered
    assert "Причина:" not in rendered
    controller._notify("listing")
    assert controller.last_receipt.response_diagnostic is None
    _release_worker(worker)
    controller.shutdown()


def test_choose_and_forget_clear_response_reason_preserving_live_session_and_history(
    qt_app: QApplication, tmp_path: Path
) -> None:
    del qt_app
    from satori.desktop.mail_ui import MailPanel

    http = InjectedHTTP(LIST, metadata())
    live = connector(http)
    account = live.account
    assert account is not None
    controller = MailController(tmp_path)
    controller._connector = live
    controller._account = account
    panel = MailPanel(
        tmp_path, controller=controller, file_picker=lambda: tmp_path / "synthetic.json"
    )
    for action in (panel._choose_other_configuration, panel._forget_configuration):
        controller.configuration_ready = True
        controller._failure(
            "list", "response_invalid", response_diagnostic="metadata_header_value_limit"
        )
        assert controller.last_receipt is not None
        operation, code, occurred = (
            controller.last_receipt.operation,
            controller.last_receipt.code,
            controller.last_receipt.occurred_at,
        )
        action()
        assert controller.account is account
        assert live.account is account
        assert controller.configuration_ready is False
        assert controller.response_diagnostic is None
        assert controller.last_receipt is not None
        assert controller.last_receipt.response_diagnostic is None
        assert (
            controller.last_receipt.operation,
            controller.last_receipt.code,
            controller.last_receipt.occurred_at,
        ) == (operation, code, occurred)
        assert "Причина:" not in panel.last_action.text()
        assert http.requests == 0
    live.disconnect()
    controller._connector = None
    controller._account = None
    controller.shutdown()
    panel.deleteLater()
