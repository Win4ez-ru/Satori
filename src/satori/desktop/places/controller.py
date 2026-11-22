"""One transient owner; all helper callbacks remain outside canonical Satori state."""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from .contracts import (
    AreaChoice,
    Cleared,
    Coordinate,
    Event,
    PlacesRejected,
    Principal,
    ProtocolError,
    RouteCommand,
    RouteResult,
    Searching,
    SearchInput,
    SearchOutput,
    SearchResult,
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def _binding(
    value: SearchInput | SearchOutput | Searching | Cleared | RouteResult,
) -> tuple[str, str, int]:
    return value.request_id, value.session_id, value.epoch


def _distance(first: Coordinate, second: Coordinate) -> float:
    k = math.pi / 180
    a = (
        math.sin((second.latitude - first.latitude) * k / 2) ** 2
        + math.cos(first.latitude * k)
        * math.cos(second.latitude * k)
        * math.sin((second.longitude - first.longitude) * k / 2) ** 2
    )
    return 6_371_008.8 * 2 * math.asin(math.sqrt(min(1.0, max(0.0, a))))


class PlacesController:
    """Serialized UI-thread owner. Construction has no source/process/file I/O."""

    def __init__(
        self,
        principal: Principal,
        *,
        now: Callable[[], datetime] = utc_now,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.principal = principal
        self.session_id = str(uuid4())
        self.epoch = 0
        self.request: SearchInput | None = None
        self.output: SearchOutput | None = None
        self.pending_route: RouteCommand | None = None
        self.status = "idle"
        self.native_route_status: str | None = None
        self.deadline: float | None = None
        self._route_deadline: float | None = None
        self._now = now
        self._mono = monotonic
        self._last_wall: datetime | None = None
        self._last_mono: float | None = None
        self._closed = False
        self._retired: deque[tuple[str, str, int]] = deque(maxlen=64)
        self._seen_receipts: set[str] = set()
        self._route_actions: set[str] = set()
        self._area_choices: tuple[AreaChoice, ...] = ()

    def _clock(self) -> tuple[datetime, float]:
        wall, mono = self._now(), self._mono()
        if (
            wall.utcoffset() is None
            or not math.isfinite(mono)
            or mono < 0
            or (self._last_wall is not None and wall < self._last_wall)
            or (self._last_mono is not None and mono < self._last_mono)
        ):
            self.cancel("clock_invalid")
            self._closed = True
            raise PlacesRejected("clock_invalid")
        self._last_wall, self._last_mono = wall, mono
        return wall, mono

    def cancel(self, reason: str = "cancelled") -> None:
        if self.request is not None:
            self._retired.append(_binding(self.request))
        self.epoch += 1
        self.request = None
        self.output = None
        self.pending_route = None
        self.deadline = None
        self._route_deadline = None
        self._seen_receipts.clear()
        self._route_actions.clear()
        self._area_choices = ()
        self.native_route_status = None
        self.status = reason

    def close(self) -> None:
        self.cancel("closed")
        self._closed = True

    def set_principal(self, principal: Principal) -> None:
        self.cancel("profile_changed")
        self.principal = principal
        self.session_id = str(uuid4())
        # A closed/invalid-clock owner is not revived through profile change.

    def start_search(
        self,
        city: str,
        district: str,
        query: str,
        *,
        radius_meters: int = 2000,
        explicit_center: Coordinate | None = None,
    ) -> SearchInput:
        _, mono = self._clock()
        if self._closed:
            raise PlacesRejected("closed_session")
        candidate = SearchInput(
            request_id=str(uuid4()),
            session_id=self.session_id,
            epoch=self.epoch + 1,
            city=city.strip(),
            district=district.strip(),
            query=query.strip(),
            radius_meters=radius_meters,
            explicit_center=explicit_center,
        )
        self.cancel("superseded")
        self.request = candidate
        self.deadline = mono + 15
        self.status = "searching"
        return candidate

    def tick(self) -> bool:
        try:
            wall, mono = self._clock()
        except PlacesRejected:
            return True
        if self.request is None:
            return False
        if self._route_deadline is not None and mono >= self._route_deadline:
            self.cancel("route_outcome_unknown")
            return True
        if self.deadline is not None and mono >= self.deadline:
            self.cancel("timeout" if self.output is None else "expired")
            return True
        if self.output is not None and wall >= self.output.expires_at:
            self.cancel("expired")
            return True
        return False

    def seconds_remaining(self) -> float | None:
        if self.deadline is None:
            return None
        deadline = min(self.deadline, self._route_deadline or self.deadline)
        return max(0.0, deadline - self._mono())

    def receive(self, event: Event) -> bool:
        self.tick()
        if self._closed or self.request is None:
            return False
        wall, mono = self._clock()
        if isinstance(event, ProtocolError):
            self.cancel("source_rejected")
            return True
        value = event.result if isinstance(event, SearchResult) else event
        if _binding(value) != _binding(self.request):
            # Never project a callback from a different request, profile or app session.
            return False
        if isinstance(event, Cleared):
            self.cancel(
                event.reason if event.reason in {"expired", "timeout", "map_closed"} else "cleared"
            )
            return True
        if isinstance(event, Searching):
            if self.status == "needs_origin_confirmation" and self.output is not None:
                self.output = None
                self.deadline = mono + 15
                self.status = "searching_places"
                self.native_route_status = None
                return True
            # Initial optional acknowledgement cannot extend the original 15-second deadline.
            return False
        if isinstance(event, RouteResult):
            return self._route_result(event)
        if self.status not in {"searching", "searching_places"}:
            return False
        result = event.result
        request = self.request
        age = (wall - result.observed_at).total_seconds()
        lifetime = (result.expires_at - wall).total_seconds()
        if (
            (result.city, result.district, result.query)
            != (request.city, request.district, request.query)
            or result.receipt_id in self._seen_receipts
            or not 0 <= age < 60
            or not 0 < lifetime <= 60
            or (request.explicit_center is not None and result.origin != request.explicit_center)
            or (self.status == "searching_places" and result.status == "needs_origin_confirmation")
            or (
                self.status == "searching"
                and request.explicit_center is None
                and result.status in {"complete", "partial"}
            )
            or (
                self.status == "searching_places"
                and result.status in {"complete", "partial"}
                and not any(
                    result.origin == choice.coordinate and result.origin_label == choice.label
                    for choice in self._area_choices
                )
            )
        ):
            self.cancel("source_rejected")
            return True
        if result.origin is not None:
            for place in result.places:
                actual = _distance(result.origin, place.coordinate)
                if actual > request.radius_meters + 0.01 or abs(actual - place.distance_meters) > 1:
                    self.cancel("source_rejected")
                    return True
        self._seen_receipts.add(result.receipt_id)
        if result.status == "needs_origin_confirmation":
            self._area_choices = result.area_choices
        self.pending_route = None
        self._route_deadline = None
        self.output = result if result.status != "unavailable" else None
        self.status = result.status
        self.deadline = mono + lifetime
        return True

    def can_route(self, place_id: str) -> bool:
        self.tick()
        return self.route_ready(place_id)

    def route_ready(self, place_id: str) -> bool:
        """Pure view projection; authoritative begin_route always rechecks expiry."""
        result = self.output
        return (
            not self._closed
            and self.pending_route is None
            and result is not None
            and result.map_visible
            and result.origin_confirmed
            and result.status in {"complete", "partial"}
            and len(self._route_actions) < 64
            and any(place.place_id == place_id for place in result.places)
        )

    def begin_route(self, place_id: str) -> RouteCommand:
        if not self.can_route(place_id) or self.output is None:
            raise PlacesRejected("route_denied")
        _, mono = self._clock()
        command = RouteCommand(
            receipt_id=self.output.receipt_id, place_id=place_id, action_id=str(uuid4())
        )
        self._route_actions.add(command.action_id)
        self.pending_route = command
        self._route_deadline = mono + 15
        self.native_route_status = None
        return command

    def _route_result(self, event: RouteResult) -> bool:
        result = self.output
        if (
            result is None
            or result.receipt_id != event.receipt_id
            or not any(p.place_id == event.place_id for p in result.places)
        ):
            return False
        command = self.pending_route
        if command is not None and (event.receipt_id, event.place_id, event.action_id) == (
            command.receipt_id,
            command.place_id,
            command.action_id,
        ):
            self.pending_route = None
            self._route_deadline = None
            self.status = event.status
            return True
        # A real native-window button has its own action ID. This is informational,
        # never an acknowledgement of a different Python route command.
        if event.action_id in self._route_actions or len(self._route_actions) >= 64:
            return False
        self._route_actions.add(event.action_id)
        self.native_route_status = event.status
        return True
