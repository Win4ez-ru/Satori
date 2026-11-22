from datetime import timedelta
from uuid import uuid4

import pytest

from satori.desktop.places.contracts import (
    Cleared,
    Coordinate,
    PlacesRejected,
    Principal,
    RouteResult,
    Searching,
    SearchResult,
)
from satori.desktop.places.controller import PlacesController

from .conftest import Clock, output_for


def start(controller: PlacesController) -> object:
    return controller.start_search("PUBLIC CITY", "PUBLIC AREA", "restaurant")


def test_inert_startup(controller: PlacesController) -> None:
    assert controller.request is None
    assert controller.output is None
    assert controller.deadline is None
    assert controller.status == "idle"
    assert not controller.can_route("unknown")


def test_area_confirmation_then_native_user_choice(
    controller: PlacesController, clock: Clock
) -> None:
    request = controller.start_search("PUBLIC CITY", "PUBLIC AREA", "restaurant")
    area = output_for(request, clock, area=True)
    assert controller.receive(SearchResult(event="search_result", result=area))
    assert controller.status == "needs_origin_confirmation"
    assert not controller.can_route("synthetic-place")
    clock.advance(30)
    searching = Searching(
        event="searching",
        request_id=request.request_id,
        session_id=request.session_id,
        epoch=request.epoch,
    )
    assert controller.receive(searching)
    assert controller.output is None
    assert controller.deadline == clock.mono + 15
    clock.advance(1)
    assert not controller.receive(searching)
    assert controller.deadline == clock.mono + 14
    assert controller.receive(
        SearchResult(event="search_result", result=output_for(request, clock))
    )
    assert controller.can_route("synthetic-place")


@pytest.mark.parametrize("field", ["request_id", "session_id", "epoch"])
def test_foreign_callbacks_never_display(
    controller: PlacesController, clock: Clock, field: str
) -> None:
    request = controller.start_search(
        "CITY",
        "AREA",
        "restaurant",
        explicit_center=Coordinate(latitude=59.9398, longitude=30.3146),
    )
    output = output_for(request, clock).model_copy(
        update={field: 999 if field == "epoch" else "foreign"}
    )
    assert not controller.receive(SearchResult(event="search_result", result=output))
    assert controller.output is None
    assert controller.request == request


@pytest.mark.parametrize("transition", ["cancel", "hide", "close", "profile", "supersede"])
def test_late_result_transition(
    controller: PlacesController, clock: Clock, principal: Principal, transition: str
) -> None:
    request = controller.start_search(
        "CITY",
        "AREA",
        "restaurant",
        explicit_center=Coordinate(latitude=59.9398, longitude=30.3146),
    )
    event = SearchResult(event="search_result", result=output_for(request, clock))
    if transition == "cancel":
        controller.cancel()
    elif transition == "hide":
        controller.cancel("hidden")
    elif transition == "close":
        controller.close()
    elif transition == "profile":
        controller.set_principal(principal.model_copy(update={"profile_id": "other"}))
    else:
        controller.start_search("OTHER CITY", "OTHER AREA", "coffee")
    assert not controller.receive(event)
    assert controller.output is None


@pytest.mark.parametrize("seconds", [15, 16, 100])
def test_request_timeout_no_replay(
    controller: PlacesController, clock: Clock, seconds: int
) -> None:
    request = controller.start_search(
        "CITY",
        "AREA",
        "restaurant",
        explicit_center=Coordinate(latitude=59.9398, longitude=30.3146),
    )
    clock.advance(seconds)
    assert controller.tick()
    assert controller.status == "timeout"
    assert controller.request is None
    assert not controller.receive(
        SearchResult(event="search_result", result=output_for(request, clock))
    )


def test_result_ttl_not_extended_by_duplicate(controller: PlacesController, clock: Clock) -> None:
    request = controller.start_search(
        "CITY",
        "AREA",
        "restaurant",
        explicit_center=Coordinate(latitude=59.9398, longitude=30.3146),
    )
    event = SearchResult(event="search_result", result=output_for(request, clock))
    controller.receive(event)
    deadline = controller.deadline
    clock.advance(30)
    assert not controller.receive(event)
    assert controller.deadline == deadline
    clock.advance(30)
    assert controller.tick()
    assert controller.output is None
    assert controller.status == "expired"


@pytest.mark.parametrize("mutation", ["future", "expired", "query", "radius"])
def test_semantic_source_admission(
    controller: PlacesController, clock: Clock, mutation: str
) -> None:
    request = controller.start_search(
        "CITY",
        "AREA",
        "restaurant",
        explicit_center=Coordinate(latitude=59.9398, longitude=30.3146),
    )
    output = output_for(request, clock)
    if mutation == "future":
        output = output.model_copy(
            update={
                "observed_at": clock.wall + timedelta(seconds=1),
                "expires_at": clock.wall + timedelta(seconds=61),
            }
        )
    elif mutation == "expired":
        output = output.model_copy(
            update={"observed_at": clock.wall - timedelta(seconds=60), "expires_at": clock.wall}
        )
    elif mutation == "query":
        output = output.model_copy(update={"query": "other"})
    else:
        output = output.model_copy(
            update={
                "places": (
                    output.places[0].model_copy(
                        update={"coordinate": Coordinate(latitude=0.0, longitude=0.0)}
                    ),
                )
            }
        )
    assert controller.receive(SearchResult(event="search_result", result=output))
    assert controller.output is None
    assert controller.status == "source_rejected"


def test_clock_rollback_closes(controller: PlacesController, clock: Clock) -> None:
    controller.start_search(
        "CITY",
        "AREA",
        "restaurant",
        explicit_center=Coordinate(latitude=59.9398, longitude=30.3146),
    )
    clock.advance(-1)
    assert controller.tick()
    assert controller.status == "clock_invalid"
    clock.advance(2)
    with pytest.raises(PlacesRejected, match="closed_session"):
        controller.start_search("CITY", "AREA", "coffee")


def test_route_requires_exact_fresh_pending_action(
    controller: PlacesController, clock: Clock
) -> None:
    request = controller.start_search(
        "CITY",
        "AREA",
        "restaurant",
        explicit_center=Coordinate(latitude=59.9398, longitude=30.3146),
    )
    output = output_for(request, clock)
    controller.receive(SearchResult(event="search_result", result=output))
    command = controller.begin_route("synthetic-place")
    assert not controller.can_route("synthetic-place")
    with pytest.raises(PlacesRejected):
        controller.begin_route("synthetic-place")
    event = RouteResult(
        event="route_result",
        request_id=request.request_id,
        session_id=request.session_id,
        epoch=request.epoch,
        receipt_id=output.receipt_id,
        place_id=command.place_id,
        action_id=command.action_id,
        status="maps_handoff_accepted",
    )
    assert controller.receive(event)
    assert controller.pending_route is None
    assert controller.status == "maps_handoff_accepted"
    assert not controller.receive(event)
    assert controller.can_route("synthetic-place")
    clock.advance(60)
    with pytest.raises(PlacesRejected):
        controller.begin_route("synthetic-place")


def test_native_route_button_is_informational_not_ack_of_own_action(
    controller: PlacesController, clock: Clock
) -> None:
    request = controller.start_search(
        "CITY",
        "AREA",
        "restaurant",
        explicit_center=Coordinate(latitude=59.9398, longitude=30.3146),
    )
    output = output_for(request, clock)
    controller.receive(SearchResult(event="search_result", result=output))
    command = controller.begin_route("synthetic-place")
    native = RouteResult(
        event="route_result",
        request_id=request.request_id,
        session_id=request.session_id,
        epoch=request.epoch,
        receipt_id=output.receipt_id,
        place_id="synthetic-place",
        action_id=str(uuid4()),
        status="maps_handoff_accepted",
    )
    assert controller.receive(native)
    assert controller.pending_route == command
    assert controller.native_route_status == "maps_handoff_accepted"
    assert not controller.receive(native)
    assert not controller.receive(native.model_copy(update={"receipt_id": "other"}))


def test_route_timeout_is_unknown_no_replay(controller: PlacesController, clock: Clock) -> None:
    request = controller.start_search(
        "CITY",
        "AREA",
        "restaurant",
        explicit_center=Coordinate(latitude=59.9398, longitude=30.3146),
    )
    controller.receive(SearchResult(event="search_result", result=output_for(request, clock)))
    controller.begin_route("synthetic-place")
    clock.advance(15)
    assert controller.tick()
    assert controller.status == "route_outcome_unknown"
    assert controller.pending_route is None
    assert controller.output is None


def test_native_cleared_removes_cards(controller: PlacesController, clock: Clock) -> None:
    request = controller.start_search(
        "CITY",
        "AREA",
        "restaurant",
        explicit_center=Coordinate(latitude=59.9398, longitude=30.3146),
    )
    controller.receive(SearchResult(event="search_result", result=output_for(request, clock)))
    assert controller.receive(
        Cleared(
            event="cleared",
            request_id=request.request_id,
            session_id=request.session_id,
            epoch=request.epoch,
            reason="map_closed",
        )
    )
    assert controller.output is None
    assert controller.status == "map_closed"


def test_area_confirmation_cannot_be_skipped(controller: PlacesController, clock: Clock) -> None:
    request = controller.start_search("CITY", "AREA", "restaurant")
    controller.receive(SearchResult(event="search_result", result=output_for(request, clock)))
    assert controller.status == "source_rejected"
    assert controller.output is None
