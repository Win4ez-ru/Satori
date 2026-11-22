import json
from datetime import timedelta

import pytest
from pydantic import ValidationError

from satori.desktop.places.contracts import (
    INPUT_CAP,
    OUTPUT_CAP,
    Coordinate,
    LineFramer,
    PlacesRejected,
    SearchCommand,
    SearchInput,
    SearchOutput,
    SearchResult,
    decode_event,
    encode_command,
)

from .conftest import Clock, output_for


def request() -> SearchInput:
    return SearchInput(
        request_id="request",
        session_id="session",
        epoch=0,
        city="PUBLIC CITY",
        district="PUBLIC AREA",
        query="restaurant",
    )


def test_camel_case_roundtrip(clock: Clock) -> None:
    event = SearchResult(event="search_result", result=output_for(request(), clock))
    assert decode_event(event.model_dump_json(by_alias=True).encode()) == event
    command = encode_command(SearchCommand(search=request()))
    assert len(command) <= INPUT_CAP
    data = json.loads(command)
    assert data["search"]["requestID"] == "request"
    assert "request_id" not in data["search"]
    assert b"explicitCenter" not in command


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"x" * (OUTPUT_CAP + 1),
        b"\xff",
        b"{",
        b"[]",
        b"[" * 1000,
        b'{"event":"protocol_error","code":"a","code":"b"}',
        b'{"event":"protocol_error","code":NaN}',
    ],
)
def test_decoder_closed_bounds(raw: bytes) -> None:
    with pytest.raises(PlacesRejected) as error:
        decode_event(raw)
    assert str(error.value) in {"output_size", "invalid_frame"}
    assert error.value.__suppress_context__ or not error.value.__context__


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("city", ""),
        ("district", " "),
        ("query", "bad\ntext"),
        ("city", "я" * 61),
        ("radius_meters", True),
        ("radius_meters", 5001),
        ("epoch", -1),
    ],
)
def test_strict_request(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        SearchInput.model_validate({**request().model_dump(), field: value})


@pytest.mark.parametrize("field", ["cuisineStatus", "hoursStatus", "budgetStatus"])
def test_unknowns_cannot_be_upgraded(field: str, clock: Clock) -> None:
    data = SearchResult(event="search_result", result=output_for(request(), clock)).model_dump(
        mode="json", by_alias=True
    )
    data["result"]["places"][0][field] = "known"
    with pytest.raises(PlacesRejected):
        decode_event(json.dumps(data).encode())


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -91.0, 91.0])
def test_coordinate_bounds(value: float) -> None:
    with pytest.raises(ValidationError):
        Coordinate(latitude=value, longitude=30.0)


@pytest.mark.parametrize(
    "change", ["map", "count", "duplicate", "ttl", "naive", "partial_origin", "extra"]
)
def test_result_contract(clock: Clock, change: str) -> None:
    value = output_for(request(), clock)
    data = value.model_dump()
    if change == "map":
        data["map_visible"] = False
    elif change == "count":
        data["places"] = tuple(
            value.places[0].model_copy(update={"place_id": str(i)}) for i in range(21)
        )
    elif change == "duplicate":
        data["places"] = value.places * 2
    elif change == "ttl":
        data["expires_at"] = clock.wall + timedelta(seconds=61)
    elif change == "naive":
        data["observed_at"] = clock.wall.replace(tzinfo=None)
    elif change == "partial_origin":
        data.update(origin_confirmed=False, origin_label=None)
    elif change == "extra":
        data["remember_preference"] = True
    with pytest.raises(ValidationError):
        SearchOutput.model_validate(data)


def test_line_framer_fragmentation_and_eof() -> None:
    frame = LineFramer()
    assert frame.feed(b'{"event":') == ()
    assert frame.feed(b'"protocol_error","code":"a"}\n') == (
        b'{"event":"protocol_error","code":"a"}',
    )
    frame.eof()
    frame.feed(b"{")
    with pytest.raises(PlacesRejected, match="truncated_frame"):
        frame.eof()


def test_unterminated_frame_cap() -> None:
    frame = LineFramer()
    for _ in range(4):
        frame.feed(b"x" * 65536)
    with pytest.raises(PlacesRejected, match="output_size"):
        frame.feed(b"x")


def test_schema_version_boolean_is_not_integer_one() -> None:
    data = request().model_dump()
    data["schema_version"] = True
    with pytest.raises(ValidationError):
        SearchInput.model_validate(data)


def test_unavailable_without_map_data_is_allowed(clock: Clock) -> None:
    data = output_for(request(), clock).model_dump()
    data.update(status="unavailable", places=(), area_choices=(), map_visible=False)
    result = SearchOutput.model_validate(data)
    assert not result.map_visible
    assert result.places == ()
