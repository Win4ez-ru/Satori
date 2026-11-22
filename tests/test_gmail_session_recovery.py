"""Regression cases use only synthetic OAuth/HTTP fixtures, never a mailbox."""

import base64
import threading
from collections.abc import Callable
from datetime import timedelta

import pytest

from satori.infrastructure.gmail.contracts import GmailError
from tests.test_gmail_connector import connected, encoded, selected
from tests.test_gmail_connector import synthetic_loopback as synthetic_loopback


@pytest.mark.parametrize("value", ["hello\tworld", "=?utf-8?q?hello=09world?="])
def test_inbox_header_tab_reaches_typed_claim_losslessly(value: str) -> None:
    connector, http, _ = connected()
    http.ids = ("m1",)
    http.responses["/gmail/v1/users/me/messages/m1"] = encoded(
        {"id": "m1", "payload": {"headers": [{"name": "Subject", "value": value}]}}
    )
    page = connector.list_inbox(cancel=threading.Event())
    assert page.messages[0].subject == "hello\tworld"


@pytest.mark.parametrize("value", ["a\nb", "a\rb", "a\x00b", "a\x7fb", "a\u200bb"])
@pytest.mark.parametrize("encoded_value", [False, True])
def test_inbox_header_controls_are_omitted_without_admitting_claims(
    value: str, encoded_value: bool
) -> None:
    connector, http, _ = connected()
    if encoded_value:
        value = "=?utf-8?b?" + base64.b64encode(value.encode()).decode() + "?="
    http.ids = ("m1",)
    http.responses["/gmail/v1/users/me/messages/m1"] = encoded(
        {"id": "m1", "payload": {"headers": [{"name": "Subject", "value": value}]}}
    )
    with pytest.raises(GmailError, match="response_invalid"):
        connector._header_claims([{"name": "Subject", "value": value}])
    page = connector.list_inbox(cancel=threading.Event())
    assert page.completeness == "unsupported"
    assert page.messages == ()
    assert page.catalog.message_ids == ()
    assert page.omitted_count == 1
    assert page.omissions[0].reason == (
        "metadata_header_decoded_controls" if encoded_value else "metadata_header_value_controls"
    )


@pytest.mark.parametrize("operation", ["list", "read"])
def test_403_preserves_live_session_and_requires_new_explicit_action(operation: str) -> None:
    connector, http, clock = connected()
    identity = connector.account
    _, request = selected(connector, clock)
    path = "/gmail/v1/users/me/messages" + ("/m1" if operation == "read" else "")
    http.responses[path] = GmailError("permission_denied")
    before = len(http.calls)
    action: Callable[[], object] = (
        (lambda: connector.list_inbox(cancel=threading.Event()))
        if operation == "list"
        else (lambda: connector.read_selected(request, cancel=threading.Event()))
    )
    with pytest.raises(GmailError, match="permission_denied"):
        action()
    assert len(http.calls) == before + 1  # no hidden OAuth, refresh or retry
    assert connector.account == identity
    del http.responses[path]
    assert len(connector.list_inbox(cancel=threading.Event()).messages) == 2
    assert connector.account == identity


def test_exposed_connection_deadline_is_local_and_clears_on_expiry() -> None:
    connector, http, clock = connected()
    before = len(http.calls)
    assert connector.connection_expires_at is not None
    assert connector.connection_expires_at == clock.wall + timedelta(hours=1)
    assert len(http.calls) == before
    clock.advance(3600)
    assert connector.connection_expires_at is None
    assert connector.account is None
    assert len(http.calls) == before
