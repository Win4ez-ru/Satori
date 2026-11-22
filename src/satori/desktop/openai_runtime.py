"""Explicit private Desktop OpenAI composition using the existing budget/Core owners.

Constructing the owner performs no I/O. Only an explicitly configured worker open
reads the referenced credential and opens its stable production ledger. No API
request, vector backfill, provider fallback or budget renewal occurs at startup.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import stat
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from importlib.resources import files
from pathlib import Path
from threading import Condition
from typing import TYPE_CHECKING, Any, Literal, Self
from uuid import NAMESPACE_URL, UUID, uuid5

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from satori.application.assistant.contracts import require_utc
from satori.config import Settings
from satori.desktop.mobile_gateway.private_store import _ACLGuard
from satori.infrastructure.providers.openai_http import OpenAIHttpClient, OpenAITransportError
from satori.infrastructure.providers.openai_structured import OpenAIStructuredTransport
from satori.server.budget import BudgetedOpenAITransport, PriceAssumptions
from satori.server.openai_runtime import (
    OPENAI_ROLES,
    budget_model_for_role,
    build_openai_providers,
    build_openai_settings,
)
from satori.server.usage import UsageLedger

if TYPE_CHECKING:
    from satori.desktop.backend import DesktopProviders

CONFIG_NAME = "desktop-openai-runtime.json"
USAGE_NAME = "openai-production-usage"
EXISTING_KEY_FILE = Path(".env")
MAX_CONFIG_BYTES = 32768
MAX_CREDENTIAL_BYTES = 65536
PRICING_SHA256 = "4fdba8e397959e45a3e062fdc67d64b97639f3ef1781400de9f77ecc60abd547"


class DesktopOpenAIRuntimeError(ValueError):
    """Finite public diagnostic; never includes credentials or parsed input."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _DTO(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


class CredentialReference(_DTO):
    kind: Literal["dotenv"] = "dotenv"
    path: Path = Field(repr=False)
    variable: Literal["SATORI_OPENAI_API_KEY"] = "SATORI_OPENAI_API_KEY"

    @field_validator("path")
    @classmethod
    def absolute_path(cls, value: Path) -> Path:
        if not value.is_absolute() or ".." in value.parts:
            raise ValueError("credential_reference_invalid")
        return value


class DesktopOpenAIConfig(_DTO):
    schema_version: Literal[1] = 1
    profile_id: UUID
    model_profile: Literal["all_luna"] = "all_luna"
    credential: CredentialReference = Field(repr=False)
    budget_period: str = Field(pattern=r"^[2-9][0-9]{3}-(0[1-9]|1[0-2])$")
    cap_micro_rub: int = Field(ge=1, le=1_000_000_000)
    pricing_review_id: str = Field(min_length=1, max_length=100)
    rub_per_usd_ceiling: Decimal
    reviewed_at: datetime
    expires_at: datetime
    authorized: Literal[True]

    _utc = field_validator("reviewed_at", "expires_at")(require_utc)

    @field_validator("schema_version", mode="before")
    @classmethod
    def exact_version(cls, value: object) -> Literal[1]:
        if type(value) is not int or value != 1:
            raise ValueError("desktop_openai_version_invalid")
        return 1

    @field_validator("authorized", mode="before")
    @classmethod
    def explicit_authority(cls, value: object) -> Literal[True]:
        if value is not True:
            raise ValueError("desktop_openai_authorization_required")
        return True

    @model_validator(mode="after")
    def finite(self) -> Self:
        if not self.profile_id.int:
            raise ValueError("desktop_openai_profile_invalid")
        self.prices()
        return self

    def prices(self) -> PriceAssumptions:
        return PriceAssumptions(self.rub_per_usd_ceiling, self.reviewed_at, self.expires_at)


class PricingReviewIdentity(_DTO):
    review_id: str
    reviewed_at: datetime
    expires_at: datetime
    rub_per_usd_ceiling: Decimal

    _utc = field_validator("reviewed_at", "expires_at")(require_utc)

    @model_validator(mode="after")
    def finite(self) -> Self:
        PriceAssumptions(self.rub_per_usd_ceiling, self.reviewed_at, self.expires_at)
        return self


class ReviewedDesktopPricing(PricingReviewIdentity):
    recommended_cap_micro_rub: int
    maximum_cap_micro_rub: int
    fx_is_assumption: Literal[True] = True
    compatible_reviews: tuple[PricingReviewIdentity, ...] = ()


def load_reviewed_desktop_pricing() -> ReviewedDesktopPricing:
    """Read the fixed public reviewed resource; never refresh dates or query billing."""
    try:
        raw = files("satori.resources").joinpath("openai/desktop-pricing-v1.json").read_bytes()
        if len(raw) > MAX_CONFIG_BYTES or hashlib.sha256(raw).hexdigest() != PRICING_SHA256:
            raise DesktopOpenAIRuntimeError("price_review_unavailable")
        data = json.loads(raw)
        # Compatibility requires the same priced wire contract, not only a
        # familiar review identifier. A changed currency, tier, model or rate
        # requires a separately reviewed budget decision.
        schedule = {
            key: value
            for key, value in data.items()
            if key in {"currency", "tokens_per_unit", "service_tier", "context_tier"}
            or key.startswith(("generation_", "embedding_", "budget_cache_"))
        }
        schedule_digest = hashlib.sha256(
            json.dumps(schedule, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return ReviewedDesktopPricing(
            review_id=data["review_id"],
            reviewed_at=require_utc(datetime.fromisoformat(data["reviewed_at"])),
            expires_at=require_utc(datetime.fromisoformat(data["expires_at"])),
            rub_per_usd_ceiling=Decimal(data["rub_per_usd_ceiling"]),
            recommended_cap_micro_rub=data["recommended_api_cap_rub"] * 1_000_000,
            maximum_cap_micro_rub=data["maximum_api_cap_rub"] * 1_000_000,
            compatible_reviews=tuple(
                PricingReviewIdentity(
                    review_id=previous["review_id"],
                    reviewed_at=require_utc(datetime.fromisoformat(previous["reviewed_at"])),
                    expires_at=require_utc(datetime.fromisoformat(previous["expires_at"])),
                    rub_per_usd_ceiling=Decimal(previous["rub_per_usd_ceiling"]),
                )
                for previous in data.get("compatible_reviews", [])
                if previous["price_schedule_sha256"] == schedule_digest
            ),
        )
    except Exception:
        raise DesktopOpenAIRuntimeError("price_review_unavailable") from None


def _pricing_match(config: DesktopOpenAIConfig) -> None:
    review = load_reviewed_desktop_pricing()
    if (
        config.pricing_review_id != review.review_id
        or config.reviewed_at != review.reviewed_at
        or config.expires_at != review.expires_at
        or config.rub_per_usd_ceiling != review.rub_per_usd_ceiling
        or config.cap_micro_rub > review.maximum_cap_micro_rub
    ):
        raise DesktopOpenAIRuntimeError("price_review_mismatch")


def _effective_review(config: DesktopOpenAIConfig) -> DesktopOpenAIConfig:
    """Adopt only a hash-pinned compatible review, without persisting or renewing a budget."""
    review = load_reviewed_desktop_pricing()
    identity = PricingReviewIdentity(
        review_id=config.pricing_review_id,
        reviewed_at=config.reviewed_at,
        expires_at=config.expires_at,
        rub_per_usd_ceiling=config.rub_per_usd_ceiling,
    )
    if identity in review.compatible_reviews:
        if config.rub_per_usd_ceiling != review.rub_per_usd_ceiling:
            raise DesktopOpenAIRuntimeError("price_review_mismatch")
        values = config.model_dump()
        values.update(
            pricing_review_id=review.review_id,
            reviewed_at=review.reviewed_at,
            expires_at=review.expires_at,
        )
        config = DesktopOpenAIConfig.model_validate(values)
    _pricing_match(config)
    return config


def _path(path: Path) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise DesktopOpenAIRuntimeError("configuration_invalid")
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise DesktopOpenAIRuntimeError("configuration_invalid")


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_uid,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _regular(info: os.stat_result, maximum: int) -> None:
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
        or not 0 < info.st_size <= maximum
    ):
        raise DesktopOpenAIRuntimeError("configuration_invalid")


def _read(path: Path, maximum: int) -> bytes:
    _path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(descriptor)
        _regular(before, maximum)
        _ACLGuard().check(descriptor)
        data = os.read(descriptor, maximum + 1)
        if (
            len(data) != before.st_size
            or len(data) > maximum
            or _identity(before) != _identity(os.fstat(descriptor))
            or _identity(before) != _identity(path.lstat())
        ):
            raise DesktopOpenAIRuntimeError("configuration_changed")
        return data
    finally:
        os.close(descriptor)


def _profile(directory: Path) -> None:
    _path(directory)
    info = directory.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise DesktopOpenAIRuntimeError("profile_unavailable")


def inspect_existing_credential_reference(data_directory: Path) -> CredentialReference | None:
    """Review the profile-local credential file without reading its value."""
    try:
        _profile(data_directory)
        credential_path = data_directory / EXISTING_KEY_FILE
        _path(credential_path)
        _regular(credential_path.lstat(), MAX_CREDENTIAL_BYTES)
        return CredentialReference(path=credential_path)
    except (OSError, ValueError):
        return None


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise DesktopOpenAIRuntimeError("configuration_invalid")
        result[key] = value
    return result


def load_openai_runtime_config(path: Path, data_directory: Path) -> DesktopOpenAIConfig:
    try:
        _profile(data_directory)
        if path != data_directory / CONFIG_NAME:
            raise DesktopOpenAIRuntimeError("configuration_path_mismatch")
        raw = _read(path, MAX_CONFIG_BYTES)
        json.loads(raw, object_pairs_hook=_unique)
        config = DesktopOpenAIConfig.model_validate_json(raw)
        if config.profile_id != uuid5(NAMESPACE_URL, str(data_directory)):
            raise DesktopOpenAIRuntimeError("configuration_profile_mismatch")
        return _effective_review(config)
    except DesktopOpenAIRuntimeError:
        raise
    except Exception:
        raise DesktopOpenAIRuntimeError("configuration_unavailable") from None


def inspect_openai_runtime_config(path: Path, data_directory: Path) -> dict[str, object]:
    """Metadata only; no key, ledger or provider construction is performed."""
    try:
        config = load_openai_runtime_config(path, data_directory)
        return {
            "configured": True,
            "provider": "openai",
            "model": "gpt-6-luna",
            "embedding_model": "text-embedding-3-small",
            "model_profile": config.model_profile,
            "budget_period": config.budget_period,
            "cap_micro_rub": config.cap_micro_rub,
            "pricing_review_id": config.pricing_review_id,
            "reviewed_at": config.reviewed_at.isoformat(),
            "expires_at": config.expires_at.isoformat(),
            "credential_reference": "saved_file_reference",
        }
    except DesktopOpenAIRuntimeError:
        return {"configured": False, "provider": "openai", "reason": "configuration_unavailable"}


def write_reviewed_openai_config(
    data_directory: Path,
    config: DesktopOpenAIConfig,
) -> Path:
    """Write reviewed metadata only; never create/read/update a secret or budget."""
    temporary: Path | None = None
    try:
        _profile(data_directory)
        config = DesktopOpenAIConfig.model_validate(config)
        if config.profile_id != uuid5(NAMESPACE_URL, str(data_directory)):
            raise DesktopOpenAIRuntimeError("configuration_profile_mismatch")
        _pricing_match(config)
        target = data_directory / CONFIG_NAME
        _path(target)
        old = _identity(target.lstat()) if target.exists() else None
        if old is not None:
            _regular(target.lstat(), MAX_CONFIG_BYTES)
        from uuid import uuid4

        temporary = data_directory / (".openai-config-" + uuid4().hex + ".tmp")
        raw = config.model_dump_json().encode()
        if len(raw) > MAX_CONFIG_BYTES:
            raise DesktopOpenAIRuntimeError("configuration_invalid")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if old != (_identity(target.lstat()) if target.exists() else None):
            raise DesktopOpenAIRuntimeError("configuration_changed")
        os.replace(temporary, target)
        temporary = None
        return target
    except DesktopOpenAIRuntimeError:
        raise
    except Exception:
        raise DesktopOpenAIRuntimeError("configuration_unavailable") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_referenced_openai_key(reference: CredentialReference) -> SecretStr:
    try:
        reference = CredentialReference.model_validate(reference)
        raw = _read(reference.path, MAX_CREDENTIAL_BYTES)
        values = dotenv_values(stream=io.StringIO(raw.decode("utf-8")), interpolate=False)
        key = values.get(reference.variable)
        if (
            not isinstance(key, str)
            or not 20 <= len(key) <= 512
            or not key.isascii()
            or not key.startswith("sk-")
            or "${" in key
            or any(char.isspace() or ord(char) < 33 or ord(char) > 126 for char in key)
        ):
            raise DesktopOpenAIRuntimeError("credential_unavailable")
        return SecretStr(key)
    except Exception:
        raise DesktopOpenAIRuntimeError("credential_unavailable") from None


class DesktopOpenAIRuntime:
    """One all-role provider/client/ledger owner, closed only after Core drains."""

    def __init__(
        self,
        data_directory: Path,
        config_path: Path,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        client_factory: Callable[[str, str], OpenAIStructuredTransport] = OpenAIHttpClient,
    ) -> None:
        self.data_directory, self.config_path = data_directory, config_path
        self._clock, self._factory = clock, client_factory
        self._client: OpenAIStructuredTransport | None = None
        self._ledger: UsageLedger | None = None
        self._config: DesktopOpenAIConfig | None = None
        self.settings: Settings | None = None
        self.providers: DesktopProviders | None = None
        self._closed = False
        self._closing = False
        self._condition = Condition()
        self._active_calls = 0

    def open(self) -> Self:
        if self._closed or self.settings is not None:
            raise DesktopOpenAIRuntimeError("runtime_state_invalid")
        try:
            config = load_openai_runtime_config(self.config_path, self.data_directory)
            now = self._clock()
            if config.budget_period != now.strftime("%Y-%m"):
                raise DesktopOpenAIRuntimeError("budget_period_unavailable")
            if not config.reviewed_at <= now < config.expires_at:
                raise DesktopOpenAIRuntimeError("price_review_expired")
            key = load_referenced_openai_key(config.credential)
            settings = build_openai_settings(self.data_directory, key)
            self._ledger = UsageLedger(self.data_directory / USAGE_NAME, clock=self._clock)
            self._ledger.configure_period(config.budget_period, config.cap_micro_rub)
            self._client = self._factory(settings.openai_base_url, key.get_secret_value())
            transports = {
                role: _OwnedRoleTransport(
                    self,
                    BudgetedOpenAITransport(
                        self._client,
                        self._ledger,
                        role,
                        config.prices(),
                        clock=self._clock,
                        model=budget_model_for_role(settings, role),
                    ),
                )
                for role in OPENAI_ROLES
            }
            providers = build_openai_providers(settings, transports)
            self._config, self.settings, self.providers = config, settings, providers
            return self
        except Exception:
            self.close()
            raise DesktopOpenAIRuntimeError("runtime_unavailable") from None

    def owns(self, directory: Path, settings: Settings, providers: DesktopProviders | None) -> bool:
        return (
            not self._closed
            and not self._closing
            and self.data_directory == directory
            and self.settings == settings
            and self.providers is providers
            and self._ledger is not None
            and self._client is not None
        )

    def status(self) -> dict[str, object]:
        if self._closed or self._ledger is None or self._config is None:
            return {"owned_openai_runtime": False, "supports_source_answers": False}
        snapshot = self._ledger.snapshot(self._config.budget_period)
        active = (
            not self._closing
            and self._config.budget_period == self._clock().strftime("%Y-%m")
            and self._config.reviewed_at <= self._clock() < self._config.expires_at
        )
        return {
            "owned_openai_runtime": True,
            "supports_source_answers": active,
            "model_profile": "all_luna",
            "all_model_roles": "openai",
            "usage": snapshot.model_dump(mode="json"),
            "embedding_adoption": "explicit_action_required_no_vector_retag",
        }

    def _enter_call(self) -> None:
        with self._condition:
            if self._closed or self._closing or self.settings is None or self.providers is None:
                raise OpenAITransportError("desktop_runtime_closing")
            self._active_calls += 1

    def _leave_call(self) -> None:
        with self._condition:
            self._active_calls -= 1
            self._condition.notify_all()

    def _wait_drained(self) -> None:
        with self._condition:
            while self._active_calls:
                self._condition.wait()

    async def aclose(self) -> None:
        """Await actual threaded reservation→dispatch→accounting, even after cancellation.

        Cancellation of this await never closes the still-owned ledger/client.
        A caller can await this same owner again; no operation is replayed.
        """
        with self._condition:
            if self._closed:
                return
            self._closing = True
        await asyncio.to_thread(self._wait_drained)
        self.close()

    def close(self) -> None:
        """Synchronous cleanup is legal only with no active transport lifecycle."""
        with self._condition:
            if self._closed:
                return
            self._closing = True
            if self._active_calls:
                raise DesktopOpenAIRuntimeError("runtime_still_draining")
            self._closed = True
        try:
            close = getattr(self._client, "close", None)
            if callable(close):
                close()
        finally:
            if self._ledger is not None:
                self._ledger.close()
            self._client = self._ledger = None
            self.settings = self.providers = None


class _OwnedRoleTransport:
    """Track the entire existing budget transport, not just the HTTP socket call."""

    def __init__(self, owner: DesktopOpenAIRuntime, budgeted: BudgetedOpenAITransport) -> None:
        self._owner, self._budgeted = owner, budgeted

    def post_json(
        self,
        path: str,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes:
        self._owner._enter_call()
        try:
            return self._budgeted.post_json(
                path,
                payload,
                timeout_seconds=timeout_seconds,
                max_response_bytes=max_response_bytes,
            )
        finally:
            # This runs only after budget settlement/UNKNOWN bookkeeping finishes.
            self._owner._leave_call()
