"""Bounded immutable protocol shared with the owned native MapKit helper."""

from __future__ import annotations

import json
import unicodedata
from datetime import timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

INPUT_CAP = 8192
OUTPUT_CAP = 256 * 1024


class PlacesRejected(ValueError):
    """Closed code only; source/validation text never leaves this boundary."""


def text(value: str, limit: int, *, empty: bool = False) -> str:
    try:
        if (
            (not empty and not value.strip())
            or len(value.encode("utf-8")) > limit
            or any(unicodedata.category(c) in {"Cc", "Cs"} for c in value)
        ):
            raise ValueError("invalid_text")
    except UnicodeError:
        raise ValueError("invalid_text") from None
    return value


class Boundary(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, allow_inf_nan=False, populate_by_name=True
    )


class Principal(Boundary):
    uid: Annotated[int, Field(ge=0)]
    profile_id: Annotated[str, Field(min_length=1, max_length=128)]
    app_session_id: Annotated[str, Field(min_length=1, max_length=128)]
    device_id: Annotated[str, Field(min_length=1, max_length=128)]


class Coordinate(Boundary):
    latitude: Annotated[float, Field(ge=-90, le=90)]
    longitude: Annotated[float, Field(ge=-180, le=180)]


class Binding(Boundary):
    request_id: Annotated[str, Field(alias="requestID", min_length=1, max_length=128)]
    session_id: Annotated[str, Field(alias="sessionID", min_length=1, max_length=128)]
    epoch: Annotated[int, Field(ge=0, le=2**53 - 1)]


class SearchInput(Binding):
    schema_version: Annotated[int, Field(alias="schemaVersion", ge=1, le=1)] = 1
    city: str
    district: str
    query: str
    radius_meters: Annotated[int, Field(alias="radiusMeters", ge=100, le=5000)] = 2000
    explicit_center: Annotated[Coordinate | None, Field(alias="explicitCenter")] = None

    @field_validator("city", "district", "query")
    @classmethod
    def bounded_text(cls, value: str, info: ValidationInfo) -> str:
        return text(value, {"city": 120, "district": 180, "query": 240}[str(info.field_name)])


class AreaChoice(Boundary):
    choice_id: Annotated[str, Field(alias="choiceID", min_length=1, max_length=128)]
    label: str
    coordinate: Coordinate

    @field_validator("label")
    @classmethod
    def bounded_label(cls, value: str) -> str:
        return text(value, 600)


class PlaceRecord(Boundary):
    place_id: Annotated[str, Field(alias="placeID", min_length=1, max_length=128)]
    name: str
    address: str
    coordinate: Coordinate
    distance_meters: Annotated[int, Field(alias="distanceMeters", ge=0, le=5000)]
    cuisine_status: Annotated[Literal["unknown"], Field(alias="cuisineStatus")]
    hours_status: Annotated[Literal["unknown"], Field(alias="hoursStatus")]
    budget_status: Annotated[Literal["unknown"], Field(alias="budgetStatus")]

    @field_validator("name", "address")
    @classmethod
    def bounded_text(cls, value: str, info: ValidationInfo) -> str:
        return text(value, 400 if info.field_name == "name" else 600)


type SearchStatus = Literal["needs_origin_confirmation", "complete", "partial", "unavailable"]


class SearchOutput(Binding):
    schema_version: Annotated[int, Field(alias="schemaVersion", ge=1, le=1)]
    receipt_id: Annotated[str, Field(alias="receiptID", min_length=1, max_length=128)]
    status: SearchStatus
    source: Literal["apple_mapkit"]
    observed_at: Annotated[AwareDatetime, Field(alias="observedAt")]
    expires_at: Annotated[AwareDatetime, Field(alias="expiresAt")]
    city: str
    district: str
    query: str
    origin_label: Annotated[str | None, Field(alias="originLabel")] = None
    origin: Coordinate | None = None
    origin_confirmed: Annotated[bool, Field(alias="originConfirmed")]
    places: Annotated[tuple[PlaceRecord, ...], Field(max_length=20)]
    area_choices: Annotated[tuple[AreaChoice, ...], Field(alias="areaChoices", max_length=5)]
    map_visible: Annotated[bool, Field(alias="mapVisible")]
    error_code: Annotated[
        Annotated[str, Field(pattern=r"^[a-z0-9_]{1,64}$")] | None, Field(alias="errorCode")
    ] = None

    @field_validator("city", "district", "query")
    @classmethod
    def bounded_text(cls, value: str, info: ValidationInfo) -> str:
        return text(value, {"city": 120, "district": 180, "query": 240}[str(info.field_name)])

    @field_validator("origin_label")
    @classmethod
    def bounded_origin(cls, value: str | None) -> str | None:
        return text(value, 1200) if value is not None else None

    @model_validator(mode="after")
    def coherent(self) -> SearchOutput:
        if not timedelta(0) < self.expires_at - self.observed_at <= timedelta(seconds=60):
            raise ValueError("receipt_lifetime")
        if self.origin_confirmed and (self.origin is None or self.origin_label is None):
            raise ValueError("origin_mismatch")
        if not self.origin_confirmed and (self.origin is not None or self.origin_label is not None):
            raise ValueError("origin_mismatch")
        if len(self.places) != len({p.place_id for p in self.places}):
            raise ValueError("duplicate_place")
        if len(self.area_choices) != len({p.choice_id for p in self.area_choices}):
            raise ValueError("duplicate_area")
        if self.status == "needs_origin_confirmation":
            if (
                self.origin_confirmed
                or self.places
                or not self.area_choices
                or not self.map_visible
            ):
                raise ValueError("area_shape")
        elif self.status in {"complete", "partial"}:
            if not self.origin_confirmed or self.area_choices or not self.map_visible:
                raise ValueError("places_shape")
        elif self.places or self.area_choices:
            raise ValueError("unavailable_shape")
        return self


class SearchResult(Boundary):
    event: Literal["search_result"]
    result: SearchOutput


class Searching(Binding):
    event: Literal["searching"]


class Cleared(Binding):
    event: Literal["cleared"]
    reason: Annotated[str, Field(pattern=r"^[a-z0-9_]{1,64}$")]


class RouteResult(Binding):
    event: Literal["route_result"]
    receipt_id: Annotated[str, Field(alias="receiptID", min_length=1, max_length=128)]
    place_id: Annotated[str, Field(alias="placeID", min_length=1, max_length=128)]
    action_id: Annotated[str, Field(alias="actionID", min_length=1, max_length=128)]
    status: Literal["maps_handoff_accepted", "maps_handoff_failed"]

    @field_validator("action_id")
    @classmethod
    def action_uuid(cls, value: str) -> str:
        UUID(value)
        return value


class ProtocolError(Boundary):
    event: Literal["protocol_error"]
    code: Annotated[str, Field(pattern=r"^[a-z0-9_]{1,64}$")]


type Event = SearchResult | Searching | Cleared | RouteResult | ProtocolError
_EVENT: TypeAdapter[Event] = TypeAdapter(Annotated[Event, Field(discriminator="event")])


class SearchCommand(Boundary):
    operation: Literal["search"] = "search"
    search: SearchInput


class CancelCommand(Boundary):
    operation: Literal["cancel"] = "cancel"


class CloseCommand(Boundary):
    operation: Literal["close"] = "close"


class RouteCommand(Boundary):
    operation: Literal["route"] = "route"
    receipt_id: Annotated[str, Field(alias="receiptID", min_length=1, max_length=128)]
    place_id: Annotated[str, Field(alias="placeID", min_length=1, max_length=128)]
    action_id: Annotated[str, Field(alias="actionID", min_length=1, max_length=128)]

    @field_validator("action_id")
    @classmethod
    def action_uuid(cls, value: str) -> str:
        UUID(value)
        return value


type Command = SearchCommand | CancelCommand | CloseCommand | RouteCommand


def encode_command(command: Command) -> bytes:
    frame = command.model_dump_json(by_alias=True, exclude_none=True).encode("utf-8") + b"\n"
    if len(frame) > INPUT_CAP:
        raise PlacesRejected("input_size")
    return frame


def _pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in values:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def _constant(_: str) -> None:
    raise ValueError("nonfinite_constant")


def decode_event(raw: bytes) -> Event:
    if not raw or len(raw) > OUTPUT_CAP:
        raise PlacesRejected("output_size")
    depth = 0
    quoted = escaped = False
    for byte in raw:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            depth += 1
            if depth > 16:
                raise PlacesRejected("invalid_frame")
        elif byte in (93, 125):
            depth -= 1
            if depth < 0:
                raise PlacesRejected("invalid_frame")
    try:
        parsed = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant)
        if not isinstance(parsed, dict):
            raise ValueError("invalid_object")
        return _EVENT.validate_json(raw)
    except (ValueError, ValidationError, UnicodeError, RecursionError, OverflowError):
        raise PlacesRejected("invalid_frame") from None


class LineFramer:
    """One bounded JSON line; callers must feed reads of at most 64 KiB."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> tuple[bytes, ...]:
        if len(chunk) > 65536:
            raise PlacesRejected("read_size")
        self._buffer.extend(chunk)
        frames: list[bytes] = []
        while (index := self._buffer.find(b"\n")) >= 0:
            if not 0 < index <= OUTPUT_CAP:
                raise PlacesRejected("output_size")
            frames.append(bytes(self._buffer[:index]))
            del self._buffer[: index + 1]
        if len(self._buffer) > OUTPUT_CAP:
            raise PlacesRejected("output_size")
        return tuple(frames)

    def eof(self) -> None:
        if self._buffer:
            raise PlacesRejected("truncated_frame")
