"""One active paired phone, finite grants and digest-only private persistence."""

import hashlib
import math
import re
import secrets
import time
import unicodedata
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import RLock
from typing import Literal, NoReturn
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .contracts import DEFAULT_CAPABILITIES, DeviceContext, GatewayFault, ScopeBinding
from .private_store import PrivateStore

PAIRING_SECONDS = 120
PAIRING_ATTEMPTS = 5
DEVICE_SECONDS = 30 * 24 * 60 * 60
_STATE_NAME = "auth.json"
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z")


def _text(value: str, maximum: int) -> str:
    if (
        type(value) is not str
        or not value
        or len(value.encode("utf-8")) > maximum
        or any(unicodedata.category(char).startswith("C") for char in value)
    ):
        raise ValueError("invalid_metadata")
    return value


class _Record(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        allow_inf_nan=False,
        hide_input_in_errors=True,
    )


class _Binding(_Record):
    principal_id: str = Field(repr=False)
    profile_id: str = Field(repr=False)
    generation: int = Field(ge=0, le=2**63 - 1)
    transport_id: str = Field(repr=False)

    @field_validator("principal_id", "profile_id", "transport_id")
    @classmethod
    def bounded_identifiers(cls, value: str) -> str:
        return _text(value, 256)

    def scope(self) -> ScopeBinding:
        return ScopeBinding(self.principal_id, self.profile_id, self.generation)


class _Device(_Record):
    device_id: str
    name: str = Field(repr=False)
    token_hash: str = Field(pattern=r"^[0-9a-f]{64}$", repr=False)
    capabilities: list[str] = Field(min_length=1, max_length=4)
    issued_at: float = Field(ge=0)
    expires_at: float = Field(ge=0)
    revoked: bool = False

    @field_validator("device_id")
    @classmethod
    def opaque_id(cls, value: str) -> str:
        if str(UUID(value)) != value:
            raise ValueError("invalid_metadata")
        return value

    @field_validator("name")
    @classmethod
    def bounded_name(cls, value: str) -> str:
        if len(value) > 80:
            raise ValueError("invalid_metadata")
        return _text(value, 320)

    @field_validator("capabilities")
    @classmethod
    def finite_capabilities(cls, values: list[str]) -> list[str]:
        if (
            "status" not in values
            or len(set(values)) != len(values)
            or not set(values) <= DEFAULT_CAPABILITIES
        ):
            raise ValueError("invalid_metadata")
        return values

    @model_validator(mode="after")
    def bounded_lifetime(self) -> "_Device":
        if not 0 < self.expires_at - self.issued_at <= DEVICE_SECONDS:
            raise ValueError("invalid_metadata")
        return self


class _State(_Record):
    schema_version: Literal[1]
    binding: _Binding
    last_wall: float = Field(ge=0)
    device: _Device | None

    @field_validator("schema_version", mode="before")
    @classmethod
    def exact_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("invalid_metadata")
        return value

    @model_validator(mode="after")
    def issuance_not_in_future(self) -> "_State":
        if self.device is not None and self.device.issued_at > self.last_wall:
            raise ValueError("invalid_metadata")
        return self


@dataclass
class _Pairing:
    code_hash: str = field(repr=False)
    wall_deadline: float
    monotonic_deadline: float
    capabilities: frozenset[str]
    attempts: int = 0


class AuthOwner:
    """Local owner action opens pairing; a successful pair replaces the one active phone.

    Labels never identify devices. New grants always receive a new UUID and 256-bit
    random token. Pending codes are process-local and close on every authority change.
    Wall-clock high water is durable; monotonic high water fences this process too.
    """

    def __init__(
        self,
        store: PrivateStore,
        scope_provider: Callable[[], ScopeBinding],
        transport_id: str,
        *,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._lock = RLock()
        self._store = store
        self._scope_provider = scope_provider
        self._clock = clock
        self._monotonic = monotonic
        self._last_monotonic: float | None = None
        self._pairing: _Pairing | None = None
        self._failed = False
        self._state: _State | None = None
        try:
            self._transport_id = _text(transport_id, 256)
            loaded = store.read_json(_STATE_NAME)
            if loaded is not None:
                self._state = _State.model_validate(loaded)
            self._synchronize()
        except (ValidationError, ValueError, TypeError):
            raise GatewayFault("operation_failed", 503) from None

    def _persist(self, state: _State) -> None:
        try:
            self._store.write_json(_STATE_NAME, state.model_dump(mode="json"))
        except GatewayFault:
            self._failed = True
            self._pairing = None
            raise
        self._state = state

    def _clock_failure(self) -> NoReturn:
        self._failed = True
        self._pairing = None
        if self._state is not None:
            self._persist(self._state.model_copy(update={"device": None}))
        raise GatewayFault("operation_failed", 503)

    def _synchronize(self) -> tuple[_State, float, float]:
        if self._failed:
            raise GatewayFault("operation_failed", 503)
        try:
            self._store.path(_STATE_NAME)
        except GatewayFault:
            self._failed = True
            self._pairing = None
            raise
        try:
            wall_value = self._clock()
            monotonic_value = self._monotonic()
            if type(wall_value) not in {int, float} or type(monotonic_value) not in {int, float}:
                self._clock_failure()
            wall = float(wall_value)
            monotonic = float(monotonic_value)
            if (
                not math.isfinite(wall)
                or not math.isfinite(monotonic)
                or not 0 <= wall <= 253402300799 - DEVICE_SECONDS
                or monotonic < 0
                or (self._state is not None and wall < self._state.last_wall)
                or (self._last_monotonic is not None and monotonic < self._last_monotonic)
            ):
                self._clock_failure()
        except (OSError, ValueError, TypeError, OverflowError):
            self._clock_failure()
        scope = self._scope_provider()
        if not isinstance(scope, ScopeBinding):
            raise GatewayFault("operation_failed", 503)
        try:
            binding = _Binding(
                principal_id=scope.principal_id,
                profile_id=scope.profile_id,
                generation=scope.generation,
                transport_id=self._transport_id,
            )
        except ValidationError:
            raise GatewayFault("operation_failed", 503) from None
        self._last_monotonic = monotonic
        if self._state is None:
            self._persist(_State(schema_version=1, binding=binding, last_wall=wall, device=None))
        else:
            changed = self._state.binding != binding
            if changed:
                self._pairing = None
            if changed or wall > self._state.last_wall:
                self._persist(
                    self._state.model_copy(
                        update={
                            "binding": binding,
                            "last_wall": wall,
                            "device": None if changed else self._state.device,
                        }
                    )
                )
        assert self._state is not None
        return self._state, wall, monotonic

    def begin_pairing(self, capabilities: Collection[str]) -> str:
        with self._lock:
            _, wall, monotonic = self._synchronize()
            self._pairing = None
            if (
                not isinstance(capabilities, (list, tuple, set, frozenset))
                or len(capabilities) > len(DEFAULT_CAPABILITIES)
                or any(type(item) is not str for item in capabilities)
                or not set(capabilities) <= DEFAULT_CAPABILITIES
            ):
                raise GatewayFault("forbidden", 403)
            selected = frozenset(capabilities) | {"status"}
            code = f"{secrets.randbelow(1_000_000):06d}"
            self._pairing = _Pairing(
                hashlib.sha256(code.encode("ascii")).hexdigest(),
                wall + PAIRING_SECONDS,
                monotonic + PAIRING_SECONDS,
                selected,
            )
            return code

    def pair(self, code: str, device_name: str) -> dict[str, object]:
        with self._lock:
            if self._pairing is None and not self._failed:
                raise GatewayFault("pairing_closed", 403)
            state, wall, monotonic = self._synchronize()
            pairing = self._attempt_pairing(wall, monotonic)
            valid = type(code) is str and re.fullmatch(r"[0-9]{6}", code) is not None
            if not valid or not secrets.compare_digest(
                hashlib.sha256(code.encode("ascii")).hexdigest(),
                pairing.code_hash,
            ):
                if pairing.attempts >= PAIRING_ATTEMPTS:
                    self._pairing = None
                    raise GatewayFault("pairing_rate_limited", 429)
                raise GatewayFault("unauthorized", 401)
            try:
                if type(device_name) is not str:
                    raise ValueError("invalid_metadata")
                name = unicodedata.normalize("NFC", device_name.strip())
                _Device.bounded_name(name)
            except (ValueError, ValidationError):
                if pairing.attempts >= PAIRING_ATTEMPTS:
                    self._pairing = None
                    raise GatewayFault("pairing_rate_limited", 429) from None
                raise GatewayFault("invalid_request", 400) from None
            # Consume before minting or attempting durable publication.
            self._pairing = None
            try:
                token = secrets.token_urlsafe(32)
                device = _Device(
                    device_id=str(uuid4()),
                    name=name,
                    token_hash=hashlib.sha256(token.encode("ascii")).hexdigest(),
                    capabilities=["status", *sorted(pairing.capabilities - {"status"})],
                    issued_at=wall,
                    expires_at=wall + DEVICE_SECONDS,
                )
            except (ValueError, ValidationError):
                raise GatewayFault("invalid_request", 400) from None
            self._persist(state.model_copy(update={"device": device}))
            return {
                "deviceID": device.device_id,
                "token": token,
                "expiresAt": datetime.fromtimestamp(device.expires_at, UTC)
                .isoformat(timespec="seconds")
                .replace("+00:00", "Z"),
                "profileID": state.binding.profile_id,
                "capabilities": list(device.capabilities),
            }

    def cancel_pairing(self) -> None:
        """Stopping a listener closes its RAM window without extending/revoking a phone grant."""
        with self._lock:
            self._pairing = None

    def _attempt_pairing(self, wall: float, monotonic: float) -> _Pairing:
        pairing = self._pairing
        if pairing is None:
            raise GatewayFault("pairing_closed", 403)
        if wall >= pairing.wall_deadline or monotonic >= pairing.monotonic_deadline:
            self._pairing = None
            raise GatewayFault("pairing_expired", 410)
        pairing.attempts += 1
        return pairing

    def reject_pairing_attempt(self) -> None:
        """A malformed HTTP pair body shares the same five-attempt window, without a code oracle."""
        with self._lock:
            if self._pairing is None and not self._failed:
                raise GatewayFault("pairing_closed", 403)
            _, wall, monotonic = self._synchronize()
            pairing = self._attempt_pairing(wall, monotonic)
            if pairing.attempts >= PAIRING_ATTEMPTS:
                self._pairing = None
                raise GatewayFault("pairing_rate_limited", 429)

    def _context(self, state: _State, device: _Device) -> DeviceContext:
        return DeviceContext(
            device.device_id,
            state.binding.scope(),
            frozenset(device.capabilities),
            device.expires_at,
            state.binding.transport_id,
        )

    def _live_device(self, state: _State, wall: float) -> _Device:
        if state.device is None:
            raise GatewayFault("device_revoked", 401)
        if wall >= state.device.expires_at:
            raise GatewayFault("device_expired", 401)
        if state.device.revoked:
            raise GatewayFault("device_revoked", 401)
        return state.device

    def authenticate(self, token: str) -> DeviceContext:
        with self._lock:
            if self._failed:
                raise GatewayFault("operation_failed", 503)
            # Invalid remote credentials cannot force private-state writes/fsyncs.
            if (
                type(token) is not str
                or _TOKEN.fullmatch(token) is None
                or self._state is None
                or self._state.device is None
            ):
                raise GatewayFault("unauthorized", 401)
            if not secrets.compare_digest(
                hashlib.sha256(token.encode("ascii")).hexdigest(),
                self._state.device.token_hash,
            ):
                raise GatewayFault("unauthorized", 401)
            state, wall, _ = self._synchronize()
            if state.device is None:
                raise GatewayFault("unauthorized", 401)
            device = self._live_device(state, wall)
            return self._context(state, device)

    def revalidate(self, context: DeviceContext) -> None:
        with self._lock:
            state, wall, _ = self._synchronize()
            if not isinstance(context, DeviceContext):
                raise GatewayFault("unauthorized", 401)
            if context.scope != state.binding.scope():
                raise GatewayFault("profile_changed", 409)
            device = self._live_device(state, wall)
            if context != self._context(state, device):
                raise GatewayFault("device_revoked", 401)

    def revoke(self, device_id: str) -> None:
        with self._lock:
            state, _, _ = self._synchronize()
            self._pairing = None
            try:
                if type(device_id) is not str or str(UUID(device_id)) != device_id:
                    raise ValueError("invalid_metadata")
            except ValueError:
                raise GatewayFault("invalid_request", 400) from None
            if state.device is not None and state.device.device_id == device_id:
                self._persist(
                    state.model_copy(
                        update={
                            "device": state.device.model_copy(update={"revoked": True}),
                        }
                    )
                )

    def revoke_all(self) -> None:
        with self._lock:
            state, _, _ = self._synchronize()
            self._pairing = None
            self._persist(state.model_copy(update={"device": None}))

    def set_transport(self, transport_id: str) -> None:
        with self._lock:
            state, _, _ = self._synchronize()
            try:
                transport_id = _text(transport_id, 256)
            except (ValueError, TypeError):
                raise GatewayFault("invalid_request", 400) from None
            if transport_id != self._transport_id:
                self._pairing = None
                self._persist(
                    state.model_copy(
                        update={
                            "binding": state.binding.model_copy(
                                update={"transport_id": transport_id}
                            ),
                            "device": None,
                        }
                    )
                )
                self._transport_id = transport_id
