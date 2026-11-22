"""Bounded mobile views over owner receipts, without GUI or source access."""

from collections.abc import Mapping
from typing import cast

from satori.desktop.mobile_gateway.contracts import BridgeCall, GatewayFault
from satori.desktop.mobile_gateway.wire import (
    ChatData,
    HistoryData,
    NewSessionData,
    SessionsData,
    StatusData,
)


def identifier(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > 128
        or value != value.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise GatewayFault("invalid_request")
    return value


def _rows(value: object, maximum: int) -> list[dict[str, object]]:
    if not isinstance(value, list) or len(value) > maximum:
        raise GatewayFault("operation_failed", 502)
    if any(type(row) is not dict for row in value):
        raise GatewayFault("operation_failed", 502)
    return cast(list[dict[str, object]], value)


def project(call: BridgeCall, receipt: Mapping[str, object]) -> dict[str, object]:
    """Validate the same v1 wire shape as the existing native phone client."""
    if call.operation == "status":
        state = receipt.get("state")
        if state not in {"ready", "closing", "closed", "unknown"}:
            raise GatewayFault("operation_failed", 502)
        return StatusData.model_validate(
            {
                "ready": state == "ready",
                "coreState": "ready" if state == "ready" else "stopped",
                "profileID": call.scope.profile_id,
                "sessionID": None,
                "sourceStatuses": [
                    {"id": source, "connection": "disconnected", "capabilities": []}
                    for source in ("calendar", "gmail", "telegram")
                ],
            }
        ).model_dump()
    if call.operation in {"sessions", "history"}:
        truncated = receipt.get("truncated")
        if type(truncated) is not bool:
            raise GatewayFault("operation_failed", 502)
        if call.operation == "sessions":
            rows = _rows(receipt.get("sessions"), 1000)
            return SessionsData.model_validate(
                {
                    "activeSessionID": None,
                    "sessions": [
                        {
                            "id": identifier(row.get("session_id")),
                            "startedAt": row.get("started_at"),
                            "status": row.get("status"),
                        }
                        for row in rows[-100:]
                    ],
                    "truncated": truncated or len(rows) > 100,
                }
            ).model_dump()
        session = identifier(call.payload.get("sessionID"))
        interactions = _rows(receipt.get("interactions"), 200)
        messages: list[dict[str, object]] = []
        for row in interactions:
            if row.get("session_id") != session:
                raise GatewayFault("operation_failed", 502)
            ident = identifier(row.get("interaction_id"))
            status = row.get("status")
            if status not in {"completed", "pending", "failed"}:
                raise GatewayFault("operation_failed", 502)
            messages.append(
                {
                    "id": ident + ":user",
                    "role": "user",
                    "text": row.get("user_text"),
                    "createdAt": row.get("started_at"),
                    "status": status,
                }
            )
            if status == "completed":
                messages.append(
                    {
                        "id": ident + ":assistant",
                        "role": "assistant",
                        "text": row.get("assistant_text"),
                        "createdAt": row.get("started_at"),
                        "status": "completed",
                    }
                )
        return HistoryData.model_validate(
            {
                "sessionID": session,
                "messages": messages[-64:],
                "truncated": truncated or len(messages) > 64,
            }
        ).model_dump()
    if call.operation == "new_session":
        return NewSessionData(
            requestID=identifier(call.request_id),
            state="completed",
            sessionID=identifier(receipt.get("session_id")),
        ).model_dump()
    session = identifier(call.payload.get("sessionID"))
    if receipt.get("session_id") != session or receipt.get("client_request_id") != call.request_id:
        raise GatewayFault("operation_failed", 502)
    state = receipt.get("state", "completed" if call.operation == "chat" else None)
    if state not in {"completed", "pending", "failed", "unknown"}:
        raise GatewayFault("operation_failed", 502)
    return ChatData.model_validate(
        {
            "requestID": call.request_id,
            "state": state,
            "kind": "conversation",
            "sessionID": session,
            "text": receipt.get("text") if state == "completed" else "",
            "interactionID": identifier(receipt.get("interaction_id"))
            if state == "completed"
            else None,
            "card": None,
        }
    ).model_dump()
