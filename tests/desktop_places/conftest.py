import os
from datetime import UTC, datetime, timedelta

import pytest

from satori.desktop.places.contracts import (
    AreaChoice,
    Coordinate,
    PlaceRecord,
    Principal,
    SearchInput,
    SearchOutput,
)
from satori.desktop.places.controller import PlacesController

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class Clock:
    def __init__(self) -> None:
        self.wall = datetime(2026, 10, 31, 12, tzinfo=UTC)
        self.mono = 10.0

    def advance(self, seconds: float) -> None:
        self.wall += timedelta(seconds=seconds)
        self.mono += seconds


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def principal() -> Principal:
    return Principal(
        uid=0, profile_id="synthetic", app_session_id="synthetic", device_id="synthetic"
    )


@pytest.fixture
def controller(principal: Principal, clock: Clock) -> PlacesController:
    return PlacesController(principal, now=lambda: clock.wall, monotonic=lambda: clock.mono)


@pytest.fixture
def point() -> Coordinate:
    return Coordinate(latitude=59.9398, longitude=30.3146)


def output_for(request: SearchInput, clock: Clock, *, area: bool = False) -> SearchOutput:
    point = Coordinate(latitude=59.9398, longitude=30.3146)
    return SearchOutput(
        schema_version=1,
        request_id=request.request_id,
        session_id=request.session_id,
        epoch=request.epoch,
        receipt_id="synthetic-area" if area else "synthetic-places",
        status="needs_origin_confirmation" if area else "complete",
        source="apple_mapkit",
        observed_at=clock.wall,
        expires_at=clock.wall + timedelta(seconds=60),
        city=request.city,
        district=request.district,
        query=request.query,
        origin_label=None if area else "SYNTHETIC area",
        origin=None if area else point,
        origin_confirmed=not area,
        places=()
        if area
        else (
            PlaceRecord(
                place_id="synthetic-place",
                name="<script>SYNTHETIC Infinity venue</script>",
                address="SYNTHETIC PUBLIC POINT",
                coordinate=point,
                distance_meters=0,
                cuisine_status="unknown",
                hours_status="unknown",
                budget_status="unknown",
            ),
        ),
        area_choices=(
            AreaChoice(choice_id="synthetic-choice", label="SYNTHETIC area", coordinate=point),
        )
        if area
        else (),
        map_visible=True,
    )
