"""Public bounded evidence contracts and finite worker ingress; no accounts."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import BaseModel, ValidationError

from satori.application.assistant.source_answer_contracts import (
    SourceCoverage,
    SourceObservation,
    SourceSearchPlan,
    SourceTaskCommand,
    SourceTaskRequest,
    SourceTaskScope,
    SourceTaskSnapshot,
)
from satori.desktop.worker import WorkerRequest

NOW = datetime(2026, 11, 6, 9, tzinfo=UTC)


def public_request() -> SourceTaskRequest:
    return SourceTaskRequest(
        request_id=uuid4(),
        scope=SourceTaskScope(
            identity_id=uuid4(),
            profile_id=uuid4(),
            session_id=uuid4(),
            generation=uuid4(),
            timezone="Europe/Moscow",
        ),
        user_text="PUBLIC source question",
        sources=("mail", "telegram"),
        created_at=NOW,
        expires_at=NOW + timedelta(seconds=120),
    )


def public_snapshot() -> SourceTaskSnapshot:
    request = public_request()
    return SourceTaskSnapshot(
        request_id=request.request_id,
        scope=request.scope,
        plan_digest="a" * 64,
        observed_at=NOW,
        items=(
            SourceObservation(
                source="mail",
                source_ref="mail:public",
                observed_at=NOW,
                title="PUBLIC SUBJECT",
                text="PUBLIC BODY",
                completeness="partial",
            ),
        ),
        coverage=(
            SourceCoverage(
                source="mail",
                status="partial",
                query="public",
                window_start=NOW - timedelta(days=30),
                window_end=NOW,
            ),
        ),
    )


@pytest.mark.parametrize("change", ["bytes", "calls_item", "unknown_source", "duplicate_ref"])
def test_snapshot_rejects_utf8_overflow_or_fabricated_coverage(change: str) -> None:
    original = public_snapshot()
    data = original.model_dump(mode="json")
    if change == "bytes":
        # Each per-item string is legal, but the aggregate exceeds the byte bound.
        data["items"] = [
            {**data["items"][0], "source_ref": f"mail:{i}", "text": "я" * 4000} for i in range(5)
        ]
    elif change == "calls_item":
        data["items"][0]["source"] = "calls"
        data["coverage"][0].update(source="calls", status="unavailable")
    elif change == "unknown_source":
        data["items"][0]["source"] = "telegram"
    else:
        data["items"] *= 2
    with pytest.raises(ValidationError):
        SourceTaskSnapshot.model_validate_json(json.dumps(data))


def test_private_text_is_absent_from_validation_error_and_repr() -> None:
    snapshot = public_snapshot()
    assert "PUBLIC BODY" not in repr(snapshot)
    data = snapshot.model_dump(mode="json")
    data["items"][0]["text"] = "PUBLIC PRIVATE CONTENT " * 1000
    with pytest.raises(ValidationError) as error:
        SourceTaskSnapshot.model_validate_json(json.dumps(data))
    assert "PUBLIC PRIVATE CONTENT" not in str(error.value)


def test_task_mail_query_reserves_space_for_deterministic_date_filters() -> None:
    plan = SourceSearchPlan(
        status="search",
        mail_query="a" * 440,
        window_start=NOW - timedelta(days=30),
        window_end=NOW,
    )
    with pytest.raises(ValidationError):
        SourceSearchPlan.model_validate(plan.model_copy(update={"mail_query": "a" * 441}))


def test_mail_header_plan_wire_preserves_legacy_digest_and_serializes_explicit_mode() -> None:
    class PlanPacket(BaseModel):
        plan: SourceSearchPlan

    plan = SourceSearchPlan(
        status="search",
        mail_query="in:inbox",
        window_start=NOW - timedelta(days=1),
        window_end=NOW,
    )
    wire = PlanPacket(plan=plan).model_dump(mode="json")
    assert "mail_headers_only" not in wire["plan"]
    assert plan.digest == "79a5d51b4ce25695f27ae9c88fa4f9529eb604b99b4fec84abfcf84aed6cc412"
    assert PlanPacket.model_validate_json(json.dumps(wire)).plan == plan

    local = SourceSearchPlan.model_validate(plan.model_dump() | {"mail_headers_only": True})
    local_wire = PlanPacket(plan=local).model_dump_json()
    assert json.loads(local_wire)["plan"]["mail_headers_only"] is True
    assert PlanPacket.model_validate_json(local_wire).plan == local
    assert local.digest != plan.digest


@pytest.mark.parametrize("operation", ["plan", "answer", "cancel"])
def test_worker_wire_roundtrip_exact_task_binding(operation: str) -> None:
    request = public_request()
    snapshot = public_snapshot().model_copy(
        update={
            "request_id": request.request_id,
            "scope": request.scope,
        }
    )
    command = SourceTaskCommand(
        operation=operation,  # type: ignore[arg-type]
        request=request,
        snapshot=snapshot if operation == "answer" else None,
    )
    frame = WorkerRequest.model_validate_json(
        json.dumps(
            {
                "id": "public-wire",
                "command": "source_answer",
                "data": {
                    "session_id": str(request.scope.session_id),
                    "request_id": str(request.request_id),
                    "source_answer": command.model_dump(mode="json"),
                },
            }
        )
    )
    assert frame.data.source_answer == command


@pytest.mark.parametrize("field", ["source_answer", "session_id", "request_id"])
def test_worker_rejects_missing_source_command_field(field: str) -> None:
    request = public_request()
    data = {
        "session_id": str(request.scope.session_id),
        "request_id": str(request.request_id),
        "source_answer": SourceTaskCommand(operation="plan", request=request).model_dump(
            mode="json"
        ),
    }
    data.pop(field)
    with pytest.raises(ValidationError):
        WorkerRequest.model_validate_json(
            json.dumps(
                {
                    "id": "public-wire",
                    "command": "source_answer",
                    "data": data,
                }
            )
        )
