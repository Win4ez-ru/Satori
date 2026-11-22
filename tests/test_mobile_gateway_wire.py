"""Synthetic protocol bounds and source/canonical separation."""

import json
from typing import cast
from uuid import uuid4

import pytest

from satori.desktop.mobile_gateway.contracts import DeviceContext, GatewayFault, ScopeBinding
from satori.desktop.mobile_gateway.wire import (
    MAX_RESPONSE_BYTES,
    ChatRequest,
    PairRequest,
    RequestID,
    json_bytes,
    request_body,
    response_data,
)


def context() -> DeviceContext:
    return DeviceContext(
        str(uuid4()),
        ScopeBinding("principal", "profile", 1),
        frozenset({"status", "chat", "history", "reminders"}),
        9999999999,
        "transport",
    )


def chat(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "requestID": str(uuid4()),
        "state": "completed",
        "kind": "conversation",
        "sessionID": "session",
        "text": "reply",
        "interactionID": "interaction",
        "card": None,
    }
    value.update(updates)
    return value


@pytest.mark.parametrize(
    "data",
    [
        b"[]",
        b"null",
        b'{"requestID":"x","requestID":"y"}',
        b"{}x",
        b"\xff",
        b'{"requestID":true}',
        b'{"requestID":"00000000-0000-0000-0000-000000000000","extra":1}',
        b'{"requestID":"00000000-0000-0000-0000-000000000000","text":"\\ud800","sessionID":"s"}',
    ],
)
def test_invalid_body_never_admitted(data: bytes) -> None:
    with pytest.raises(GatewayFault):
        request_body(data, ChatRequest if b"text" in data else RequestID)


def test_unicode_scalar_text_preserved_and_uuid_case_canonical() -> None:
    identifier = str(uuid4())
    value = {
        "requestID": identifier.upper(),
        "sessionID": "session",
        "text": "Привет\nsecond line 😀",
    }
    model = request_body(json.dumps(value).encode(), ChatRequest)
    assert isinstance(model, ChatRequest)
    assert model.requestID == identifier
    assert model.text == value["text"]


def test_deep_oversize_duplicate_and_surrogate_pair_are_closed() -> None:
    for data in [
        b"[" * 33 + b"]" * 33,
        b"x" * 65537,
        b'{"code":"x","deviceName":"\\udfff"}',
        b'{"code":"x","deviceName":"x","code":"x"}',
    ]:
        with pytest.raises(GatewayFault):
            request_body(data, PairRequest)


@pytest.mark.parametrize("size", [0, 8001])
def test_input_text_limits_are_independent_of_reply_limit(size: int) -> None:
    with pytest.raises(GatewayFault):
        request_body(
            json.dumps({"requestID": str(uuid4()), "sessionID": "s", "text": "x" * size}).encode(),
            ChatRequest,
        )


def test_reply64000_scalars_and_assistant_card24000_are_admitted() -> None:
    value = chat(
        kind="assistant",
        text="😀" * 64000,
        card={
            "type": "reminder",
            "title": "done",
            "body": "я" * 24000,
            "source": "own reminders",
            "observedAt": "2026-10-31T10:20:30.123456Z",
            "expiresAt": None,
            "status": "completed",
        },
    )
    projected = response_data("chat", value, context())
    assert projected["text"] == value["text"]
    assert len(json_bytes({"ok": True, "data": projected})) <= MAX_RESPONSE_BYTES


@pytest.mark.parametrize(
    "value",
    [
        chat(text="x" * 64001),
        chat(kind="task"),
        chat(kind="assistant", card=None),
        chat(text="\ud800"),
        chat(state="invented"),
        chat(unexpected="value"),
    ],
)
def test_invalid_reply_is_rejected_without_rewriting(value: dict[str, object]) -> None:
    with pytest.raises(GatewayFault):
        response_data("chat", value, context())


def test_default_device_cannot_receive_calendar_result() -> None:
    value = chat(
        kind="assistant",
        text="",
        card={
            "type": "calendar",
            "title": "private",
            "body": "should not be disclosed",
            "source": "calendar",
            "observedAt": None,
            "expiresAt": None,
            "status": "completed",
        },
    )
    with pytest.raises(GatewayFault, match="source_grant_required"):
        response_data("chat", value, context())


@pytest.mark.parametrize(
    "date",
    [
        "2026-10-31 10:20:30Z",
        "2026-10-31T10:20Z",
        "20261031T102030Z",
        "2026-W44-6T10:20:30Z",
        "2026-10-31T10:20:30+03:00",
        "2026-02-30T10:20:30Z",
        "2026-10-31T10:20:30",
    ],
)
def test_response_dates_are_rfc3339_utc_seconds(date: str) -> None:
    value = {
        "activeSessionID": None,
        "sessions": [{"id": "s", "startedAt": date, "status": "open"}],
        "truncated": False,
    }
    with pytest.raises(GatewayFault):
        response_data("sessions", value, context())


@pytest.mark.parametrize(
    "date", ["2026-10-31T10:20:30Z", "2026-10-31T10:20:30.123456789Z", "2026-10-31T10:20:30+00:00"]
)
def test_wire_accepts_both_seconds_and_fractional_utc(date: str) -> None:
    value = {
        "activeSessionID": None,
        "sessions": [{"id": "s", "startedAt": date, "status": "open"}],
        "truncated": False,
    }
    rows = cast(list[dict[str, object]], response_data("sessions", value, context())["sessions"])
    assert rows[0]["startedAt"] == date


def test_history_caps_whole_messages_and_declares_truncation() -> None:
    messages = [
        {
            "id": str(index),
            "role": "assistant",
            "text": "x" * 64000,
            "createdAt": "2026-10-31T10:20:30Z",
            "status": "completed",
        }
        for index in range(70)
    ]
    result = response_data(
        "history", {"sessionID": "s", "messages": messages, "truncated": False}, context()
    )
    assert result["truncated"] is True
    selected = cast(list[dict[str, object]], result["messages"])
    assert 0 < len(selected) <= 64
    assert selected[-1]["id"] == "69"
    assert all(row["text"] == "x" * 64000 for row in selected)
    assert len(json_bytes({"ok": True, "data": result})) <= MAX_RESPONSE_BYTES


@pytest.mark.parametrize("role", ["system", "tool", "external", "user_tool_receipt"])
def test_history_cannot_admit_tool_receipts_as_canonical_messages(role: str) -> None:
    with pytest.raises(GatewayFault):
        response_data(
            "history",
            {
                "sessionID": "s",
                "messages": [
                    {
                        "id": "m",
                        "role": role,
                        "text": "untrusted",
                        "createdAt": "2026-10-31T10:20:30Z",
                        "status": "completed",
                    }
                ],
                "truncated": False,
            },
            context(),
        )


def test_status_wrong_profile_is_not_published() -> None:
    with pytest.raises(GatewayFault, match="profile_changed"):
        response_data(
            "status",
            {
                "ready": True,
                "coreState": "ready",
                "profileID": "other",
                "sessionID": None,
                "sourceStatuses": [],
            },
            context(),
        )


def test_plain_data_is_not_html_interpreted_or_modified() -> None:
    value = chat(text="<script>untrusted plain text</script>")
    assert response_data("chat", value, context())["text"] == value["text"]
