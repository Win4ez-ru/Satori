"""Omission catches revalidate error code and exact closed diagnostic types."""

import threading
from typing import Literal

import pytest

from satori.application.assistant.mail_contracts import MailHeaderClaim
from satori.infrastructure.gmail import connector as module
from satori.infrastructure.gmail.connector import GmailConnector
from satori.infrastructure.gmail.contracts import (
    GmailError,
    GmailHeaderOmissionReason,
)
from tests.test_gmail_partial_catalog import DECODED, RAW, connected, message

Site = Literal["outer", "raw", "decoded"]
_TRIGGER = "SYNTHETIC-BOUNDARY-TRIGGER"
_LIST_PATH = "/gmail/v1/users/me/messages"


class StringSubclass(str):
    """A string-compatible diagnostic must still fail an exact-type boundary."""


class EqualitySpoof:
    def __init__(self, target: str) -> None:
        self.target = target
        self.comparisons = 0
        self.hashes = 0

    def __eq__(self, other: object) -> bool:
        self.comparisons += 1
        return True

    def __hash__(self) -> int:
        self.hashes += 1
        return hash(self.target)


def _forged_error(
    variant: str, reason: GmailHeaderOmissionReason
) -> tuple[GmailError, tuple[EqualitySpoof, ...]]:
    error = GmailError("response_invalid", response_diagnostic=reason)
    attributes = vars(error)
    spies: tuple[EqualitySpoof, ...] = ()
    if variant == "wrong_code":
        attributes["code"] = "timeout"
    elif variant == "reason_string_subclass":
        attributes["response_diagnostic"] = StringSubclass(reason)
    elif variant == "reason_equality_spoof":
        spoof = EqualitySpoof(reason)
        attributes["response_diagnostic"] = spoof
        spies = (spoof,)
    elif variant == "missing_reason":
        attributes.pop("response_diagnostic")
    elif variant == "unknown_reason":
        attributes["response_diagnostic"] = "untrusted-source-shaped-reason"
    elif variant == "known_noncontrol_reason":
        attributes["response_diagnostic"] = "metadata_header_value_limit"
    elif variant == "code_string_subclass":
        attributes["code"] = StringSubclass("response_invalid")
    elif variant == "code_equality_spoof":
        spoof = EqualitySpoof("response_invalid")
        attributes["code"] = spoof
        spies = (spoof,)
    else:
        raise AssertionError("unknown synthetic boundary variant")
    return error, spies


def _inject(monkeypatch: pytest.MonkeyPatch, site: Site, error: GmailError) -> list[bool]:
    reached: list[bool] = []
    if site == "outer":
        original_claims = GmailConnector._header_claims
        calls = 0

        def claims(value: object) -> tuple[MailHeaderClaim, ...]:
            nonlocal calls
            calls += 1
            if calls == 2:
                reached.append(True)
                raise error
            return original_claims(value)

        monkeypatch.setattr(GmailConnector, "_header_claims", staticmethod(claims))
    else:
        original_text = module._header_text

        def header_text(value: object, *, decoded: bool = False) -> str:
            if type(value) is str and value == _TRIGGER and decoded == (site == "decoded"):
                reached.append(True)
                raise error
            return original_text(value, decoded=decoded)

        monkeypatch.setattr(module, "_header_text", header_text)
    return reached


@pytest.mark.parametrize("site", ["outer", "raw", "decoded"])
@pytest.mark.parametrize(
    "variant",
    [
        "wrong_code",
        "reason_string_subclass",
        "reason_equality_spoof",
        "missing_reason",
        "unknown_reason",
        "known_noncontrol_reason",
        "code_string_subclass",
        "code_equality_spoof",
    ],
)
def test_unqualified_error_is_same_exception_and_never_an_omission(
    monkeypatch: pytest.MonkeyPatch, site: Site, variant: str
) -> None:
    connector, http = connected({"old": message("old")})
    old = connector.list_headers(cancel=threading.Event())
    assert connector._catalog == old.catalog
    http.rows = {
        "safe": message("safe"),
        "target": message("target", _TRIGGER),
        "never-fetched": message("never-fetched"),
    }
    reason = DECODED if site == "decoded" else RAW
    original, spies = _forged_error(variant, reason)
    original_text = str(original)
    reached = _inject(monkeypatch, site, original)
    before = len(http.calls)

    with pytest.raises(GmailError) as caught:
        connector.list_headers(cancel=threading.Event())

    assert caught.value is original
    assert str(caught.value) == original_text
    assert reached == [True]
    assert connector._catalog is None
    assert connector.account is not None
    assert http.calls[before:] == [_LIST_PATH, f"{_LIST_PATH}/safe", f"{_LIST_PATH}/target"]
    for spy in spies:
        assert spy.comparisons == 0
        assert spy.hashes == 0


@pytest.mark.parametrize(
    ("site", "reason"),
    [("outer", RAW), ("outer", DECODED), ("raw", RAW), ("decoded", DECODED)],
)
def test_literal_matching_code_and_control_reason_omits_only_the_target_message(
    monkeypatch: pytest.MonkeyPatch, site: Site, reason: GmailHeaderOmissionReason
) -> None:
    connector, http = connected(
        {
            "safe": message("safe"),
            "target": message("target", _TRIGGER),
            "after": message("after"),
        }
    )
    original = GmailError("response_invalid", response_diagnostic=reason)
    reached = _inject(monkeypatch, site, original)

    page = connector.list_headers(cancel=threading.Event())

    assert reached == [True]
    assert page.completeness == "partial"
    assert page.omitted_count == 1
    assert page.total_rows == 3
    assert [(item.reason, item.count) for item in page.omissions] == [(reason, 1)]
    assert page.catalog.message_ids == ("safe", "after")
    assert tuple(item.source.message_id for item in page.messages) == ("safe", "after")
    assert connector._catalog == page.catalog
    assert http.calls == [
        _LIST_PATH,
        f"{_LIST_PATH}/safe",
        f"{_LIST_PATH}/target",
        f"{_LIST_PATH}/after",
    ]
    assert _TRIGGER not in page.model_dump_json()
