"""Public synthetic requests stay in finite, deterministic assistant routes."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from pydantic import BaseModel, ConfigDict

from satori.application.assistant.intents import parse_assistant_intent


class RequestCase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    id: str
    text: str
    kind: Literal["calendar_agenda", "calendar_gaps", "clarification"] | None
    day_offset: int | None


class RequestCorpus(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal[1]
    corpus_id: Literal["assistant-calendar-requests-v1"]
    provenance: str
    cases: tuple[RequestCase, ...]


CORPUS = RequestCorpus.model_validate_json(
    (Path(__file__).parent / "fixtures" / "assistant_calendar_requests_v1.json").read_bytes()
)
REQUEST_ID = UUID("0c0b04ec-d2b1-4a10-aa01-39abeafbf710")


def test_calendar_request_corpus_is_public_synthetic_and_has_unique_cases() -> None:
    assert CORPUS.provenance.startswith("public-synthetic;")
    assert len(CORPUS.cases) == len({case.id for case in CORPUS.cases}) == 53
    assert {case.kind for case in CORPUS.cases} == {
        "calendar_agenda",
        "calendar_gaps",
        "clarification",
        None,
    }


@pytest.mark.parametrize("case", CORPUS.cases, ids=lambda case: case.id)
@pytest.mark.parametrize(
    ("timezone", "now"),
    [
        ("Europe/Moscow", datetime(2026, 11, 2, 22, 30, tzinfo=UTC)),
        ("Europe/Amsterdam", datetime(2026, 3, 28, 23, 30, tzinfo=UTC)),
        ("Europe/Amsterdam", datetime(2026, 10, 24, 22, 30, tzinfo=UTC)),
    ],
    ids=["moscow-local-date", "amsterdam-spring-dst", "amsterdam-autumn-dst"],
)
def test_calendar_request_corpus_preserves_routing_scope_and_local_date(
    case: RequestCase, timezone: str, now: datetime
) -> None:
    result = parse_assistant_intent(case.text, now=now, timezone=timezone, request_id=REQUEST_ID)
    if case.kind is None:
        assert result is None
        return
    assert result is not None
    assert result.kind == case.kind
    assert result.request_id == REQUEST_ID
    assert result.timezone == timezone
    expected_day = (
        now.astimezone(ZoneInfo(timezone)).date() + timedelta(days=case.day_offset)
        if case.day_offset is not None
        else None
    )
    assert result.day == expected_day
    assert result.due_at is None
    assert result.source is None
    assert result.query is None
    assert result.reminder_id is None
    if case.kind == "clarification":
        assert result.clarification
    else:
        assert result.clarification is None
        assert result.title is None
