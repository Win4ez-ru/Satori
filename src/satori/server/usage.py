"""Bounded, content-free attempt accounting; not a provider invoice or dispatch adapter.

One ledger owner serializes reservations before network work. A durable dispatch
transition grants permission exactly once. Missing outcomes retain their liability;
reopening a ledger never replays an attempt or silently resets a UTC-month budget.
"""

import os
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from threading import RLock
from typing import Annotated, Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from satori.desktop.mobile_gateway.contracts import GatewayFault
from satori.desktop.mobile_gateway.private_store import MAX_PRIVATE_FILE_BYTES, PrivateStore

MAX_MICRO_RUB = 1_000_000_000_000
MAX_ATTEMPTS = 100_000
MAX_PERIODS = 24
type Money = Annotated[int, Field(strict=True, ge=0, le=MAX_MICRO_RUB)]
type Tokens = Annotated[int, Field(strict=True, ge=0, le=1_000_000_000_000)]


class UsageError(ValueError):
    """A closed diagnostic, with no exception detail or caller-supplied text."""

    def __init__(
        self,
        code: Literal[
            "invalid_metadata",
            "unavailable",
            "conflict",
            "budget_required",
            "cap_exceeded",
            "capacity_reached",
            "not_found",
            "invalid_transition",
            "outcome_unknown",
        ],
    ) -> None:
        self.code = code
        super().__init__(code)


class Provider(StrEnum):
    OPENAI = "openai"
    CLOUD_RU = "cloud_ru"
    SELECTEL = "selectel"
    YANDEX = "yandex"
    ELEVENLABS = "elevenlabs"
    LOCAL = "local"


class Model(StrEnum):
    GPT_6_LUNA = "gpt_6_luna"
    GPT_6_1_TERRA = "gpt_6_1_terra"
    TEXT_EMBEDDING_3_SMALL = "text_embedding_3_small"
    GIGACHAT_3_PRO = "gigachat_3_pro"
    GIGACHAT_3_10B = "gigachat_3_10b"
    QWEN_3_4B = "qwen_3_4b"
    EMBEDDINGGEMMA_300M = "embeddinggemma_300m"
    SPEECHKIT_V3 = "speechkit_v3"
    ELEVEN_V3 = "eleven_v3"


class Role(StrEnum):
    CONVERSATION = "conversation"
    APPRAISAL = "appraisal"
    EPISODE = "episode"
    SEMANTIC = "semantic"
    RELATIONSHIP = "relationship"
    MODELS = "models"
    POSITIONS = "positions"
    REFLECTION = "reflection"
    EMBEDDING = "embedding"
    STT = "stt"
    TTS = "tts"


class AttemptState(StrEnum):
    RESERVED = "reserved"
    DISPATCHED = "dispatched"
    UNKNOWN = "unknown"
    SETTLED = "settled"
    CANCELLED = "cancelled"


class _Document(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class AttemptSpec(_Document):
    attempt_id: UUID
    provider: Provider
    model: Model
    role: Role
    reservation_micro_rub: Annotated[int, Field(strict=True, ge=1, le=MAX_MICRO_RUB)]


class AttemptRecord(_Document):
    spec: AttemptSpec
    period: str
    created_at: datetime
    state: AttemptState = AttemptState.RESERVED
    reported_micro_rub: Money | None = None
    input_tokens: Tokens | None = None
    output_tokens: Tokens | None = None

    @field_validator("period")
    @classmethod
    def valid_period(cls, value: str) -> str:
        return _period(value)

    @field_validator("created_at")
    @classmethod
    def utc_date(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(None):
            raise ValueError("utc_required")
        return value

    @property
    def liability_micro_rub(self) -> int:
        if self.state == AttemptState.CANCELLED:
            return 0
        if self.state == AttemptState.SETTLED:
            assert self.reported_micro_rub is not None
            return self.reported_micro_rub
        return self.spec.reservation_micro_rub


class DispatchReceipt(_Document):
    attempt: AttemptRecord
    dispatch_permitted: bool


class BudgetSnapshot(_Document):
    period: str
    cap_micro_rub: Money
    liability_micro_rub: int
    unsettled_attempts: int
    settled_reported_micro_rub: int
    over_cap: bool
    provider_invoice_verified: Literal[False] = False


_TABLES = {
    "periods": "CREATE TABLE periods (period TEXT PRIMARY KEY, cap INTEGER NOT NULL)",
    "attempts": "CREATE TABLE attempts (attempt_id TEXT PRIMARY KEY, document TEXT NOT NULL)",
}


def _period(value: str) -> str:
    if (
        type(value) is not str
        or len(value) != 7
        or value[4] != "-"
        or not value[:4].isascii()
        or not value[:4].isdigit()
        or not value[5:].isascii()
        or not value[5:].isdigit()
        or not 2000 <= int(value[:4]) <= 9999
        or not 1 <= int(value[5:]) <= 12
    ):
        raise UsageError("invalid_metadata")
    return value


def _money(value: int) -> int:
    if type(value) is not int or not 0 <= value <= MAX_MICRO_RUB:
        raise UsageError("invalid_metadata")
    return value


class UsageLedger:
    """Single-owner accounting store, capped at 100,000 attempts and 24 explicit budgets.

    The namespace additionally enforces a 64 MiB file bound. The attempt count
    is a ceiling, not a lifetime retention policy or performance guarantee.

    Amounts are integer micro-RUB estimates/reports, never floats. This component
    cannot guarantee a provider's ultimate charge: the caller must bound request
    cost and integrate every attempt. An over-reservation report is retained and
    blocks further admission rather than being clipped. Same-UID hostile rollback
    is outside this local store's assurance; there is no external freshness anchor.
    """

    def __init__(self, directory: Path, *, clock: Callable[[], datetime] | None = None) -> None:
        self._mutex = RLock()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._closed = False
        self._store: PrivateStore | None = None
        try:
            self._store = PrivateStore(directory, max_file_bytes=MAX_PRIVATE_FILE_BYTES)
            path = self._store.path("usage.db")
            if set(path.parent.iterdir()) - {path, path.parent / ".owner.lock"}:
                raise UsageError("unavailable")
            if not path.exists():
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                os.close(descriptor)
                connection = sqlite3.connect(path)
                try:
                    for statement in _TABLES.values():
                        connection.execute(statement)
                    connection.execute("PRAGMA user_version=1")
                    connection.commit()
                finally:
                    connection.close()
            metadata = path.stat(follow_symlinks=False)
            self._database_identity = metadata.st_dev, metadata.st_ino
            with self._transaction():
                pass
        except (OSError, sqlite3.Error, GatewayFault, UsageError, ValidationError):
            self.close()
            raise UsageError("unavailable") from None

    def close(self) -> None:
        with self._mutex:
            self._closed = True
            if self._store is not None:
                self._store.close()
                self._store = None

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._mutex:
            if self._closed or self._store is None:
                raise UsageError("unavailable")
            connection: sqlite3.Connection | None = None
            committing = False
            try:
                path = self._store.path("usage.db")
                if not path.exists():
                    raise UsageError("unavailable")
                metadata = path.stat(follow_symlinks=False)
                if (metadata.st_dev, metadata.st_ino) != self._database_identity:
                    raise UsageError("unavailable")
                if set(path.parent.iterdir()) != {path, path.parent / ".owner.lock"}:
                    raise UsageError("unavailable")
                connection = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=1)
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("BEGIN IMMEDIATE")
                self._validate(connection)
                yield connection
                self._check_identity()
                committing = True
                connection.commit()
                self._check_identity()
            except UsageError:
                if committing:
                    raise UsageError("outcome_unknown") from None
                raise
            except (sqlite3.Error, OSError, GatewayFault, ValidationError, ValueError, TypeError):
                raise UsageError("outcome_unknown" if committing else "unavailable") from None
            finally:
                if connection is not None:
                    connection.close()

    def _check_identity(self) -> None:
        assert self._store is not None
        metadata = self._store.path("usage.db").stat(follow_symlinks=False)
        if (metadata.st_dev, metadata.st_ino) != self._database_identity:
            raise UsageError("unavailable")

    @staticmethod
    def _validate(connection: sqlite3.Connection) -> None:
        if connection.execute("PRAGMA user_version").fetchone() != (1,):
            raise UsageError("unavailable")
        schema = dict(
            connection.execute("SELECT name, sql FROM sqlite_master WHERE type='table'").fetchall()
        )
        if schema != _TABLES or connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise UsageError("unavailable")
        extras = connection.execute(
            "SELECT type, name FROM sqlite_master WHERE type NOT IN ('table', 'index') "
            "OR (type='index' AND sql IS NOT NULL)"
        ).fetchall()
        if extras or connection.execute("PRAGMA journal_mode").fetchone() != ("delete",):
            raise UsageError("unavailable")
        if connection.execute("SELECT count(*) FROM attempts").fetchone()[0] > MAX_ATTEMPTS:
            raise UsageError("unavailable")
        budgets = connection.execute("SELECT period, cap FROM periods").fetchall()
        if len(budgets) > MAX_PERIODS:
            raise UsageError("unavailable")
        for period, cap in budgets:
            _period(period)
            _money(cap)
        periods = {row[0] for row in budgets}
        for key, document in connection.execute("SELECT attempt_id, document FROM attempts"):
            record = AttemptRecord.model_validate_json(document)
            if str(record.spec.attempt_id) != key or record.period not in periods:
                raise UsageError("unavailable")
            if record.period != record.created_at.strftime("%Y-%m"):
                raise UsageError("unavailable")
            if (record.state == AttemptState.SETTLED) != (record.reported_micro_rub is not None):
                raise UsageError("unavailable")
            if record.state != AttemptState.SETTLED and (
                record.input_tokens is not None or record.output_tokens is not None
            ):
                raise UsageError("unavailable")

    def configure_period(self, period: str, cap_micro_rub: int) -> None:
        """Explicit immutable budget selection; no implicit month rollover or increase."""
        period, cap = _period(period), _money(cap_micro_rub)
        with self._transaction() as connection:
            previous = connection.execute(
                "SELECT cap FROM periods WHERE period=?", (period,)
            ).fetchone()
            if previous is not None:
                if previous[0] != cap:
                    raise UsageError("conflict")
                return
            if connection.execute("SELECT count(*) FROM periods").fetchone()[0] >= MAX_PERIODS:
                raise UsageError("capacity_reached")
            connection.execute("INSERT INTO periods VALUES (?, ?)", (period, cap))

    def reserve(self, spec: AttemptSpec) -> AttemptRecord:
        """Durably reserve a bounded estimate; this does not grant dispatch permission."""
        if not isinstance(spec, AttemptSpec):
            raise UsageError("invalid_metadata")
        try:
            spec = AttemptSpec.model_validate_json(spec.model_dump_json())
        except (ValidationError, ValueError, TypeError):
            raise UsageError("invalid_metadata") from None
        with self._transaction() as connection:
            previous = self._read(connection, spec.attempt_id)
            if previous is not None:
                if previous.spec != spec:
                    raise UsageError("conflict")
                return previous
            now = self._clock()
            if now.tzinfo is None or now.utcoffset() is None:
                raise UsageError("invalid_metadata")
            now = now.astimezone(UTC)
            period = _period(now.strftime("%Y-%m"))
            snapshot = self._snapshot(connection, period)
            if snapshot.liability_micro_rub + spec.reservation_micro_rub > snapshot.cap_micro_rub:
                raise UsageError("cap_exceeded")
            if connection.execute("SELECT count(*) FROM attempts").fetchone()[0] >= MAX_ATTEMPTS:
                raise UsageError("capacity_reached")
            record = AttemptRecord(spec=spec, period=period, created_at=now)
            connection.execute(
                "INSERT INTO attempts VALUES (?, ?)",
                (
                    str(spec.attempt_id),
                    record.model_dump_json(),
                ),
            )
            return record

    def begin_dispatch(self, attempt_id: UUID) -> DispatchReceipt:
        """Persist possible dispatch before caller network I/O; never grant a replay."""
        with self._transaction() as connection:
            record = self._required(connection, attempt_id)
            if record.state != AttemptState.RESERVED:
                return DispatchReceipt(attempt=record, dispatch_permitted=False)
            now = self._clock()
            if now.tzinfo is None or now.utcoffset() is None:
                raise UsageError("invalid_metadata")
            if now.astimezone(UTC).strftime("%Y-%m") != record.period:
                raise UsageError("invalid_transition")
            if self._snapshot(connection, record.period).over_cap:
                raise UsageError("cap_exceeded")
            record = record.model_copy(update={"state": AttemptState.DISPATCHED})
            self._write(connection, record)
            return DispatchReceipt(attempt=record, dispatch_permitted=True)

    def mark_unknown(self, attempt_id: UUID) -> AttemptRecord:
        with self._transaction() as connection:
            record = self._required(connection, attempt_id)
            if record.state not in {AttemptState.DISPATCHED, AttemptState.UNKNOWN}:
                raise UsageError("invalid_transition")
            record = record.model_copy(update={"state": AttemptState.UNKNOWN})
            self._write(connection, record)
            return record

    def cancel_before_dispatch(self, attempt_id: UUID) -> AttemptRecord:
        with self._transaction() as connection:
            record = self._required(connection, attempt_id)
            if record.state not in {AttemptState.RESERVED, AttemptState.CANCELLED}:
                raise UsageError("invalid_transition")
            record = record.model_copy(update={"state": AttemptState.CANCELLED})
            self._write(connection, record)
            return record

    def settle_report(
        self,
        attempt_id: UUID,
        reported_micro_rub: int,
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> AttemptRecord:
        """Accept explicit adapter charge metadata, not an authenticated provider invoice."""
        amount = _money(reported_micro_rub)
        for count in (input_tokens, output_tokens):
            if count is not None:
                _money(count)
        with self._transaction() as connection:
            record = self._required(connection, attempt_id)
            updated = record.model_copy(
                update={
                    "state": AttemptState.SETTLED,
                    "reported_micro_rub": amount,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                }
            )
            if record.state == AttemptState.SETTLED:
                if updated != record:
                    raise UsageError("conflict")
                return record
            if record.state not in {AttemptState.DISPATCHED, AttemptState.UNKNOWN}:
                raise UsageError("invalid_transition")
            self._write(connection, updated)
            return updated

    def reconcile(self, attempt_id: UUID) -> AttemptRecord:
        """Read only: never calls a provider, releases liability or authorizes replay."""
        with self._transaction() as connection:
            return self._required(connection, attempt_id)

    def snapshot(self, period: str) -> BudgetSnapshot:
        with self._transaction() as connection:
            return self._snapshot(connection, _period(period))

    @staticmethod
    def _read(connection: sqlite3.Connection, attempt_id: UUID) -> AttemptRecord | None:
        if not isinstance(attempt_id, UUID):
            raise UsageError("invalid_metadata")
        row = connection.execute(
            "SELECT document FROM attempts WHERE attempt_id=?", (str(attempt_id),)
        ).fetchone()
        return None if row is None else AttemptRecord.model_validate_json(row[0])

    def _required(self, connection: sqlite3.Connection, attempt_id: UUID) -> AttemptRecord:
        record = self._read(connection, attempt_id)
        if record is None:
            raise UsageError("not_found")
        return record

    @staticmethod
    def _write(connection: sqlite3.Connection, record: AttemptRecord) -> None:
        connection.execute(
            "UPDATE attempts SET document=? WHERE attempt_id=?",
            (
                record.model_dump_json(),
                str(record.spec.attempt_id),
            ),
        )

    @staticmethod
    def _snapshot(connection: sqlite3.Connection, period: str) -> BudgetSnapshot:
        row = connection.execute("SELECT cap FROM periods WHERE period=?", (period,)).fetchone()
        if row is None:
            raise UsageError("budget_required")
        records = [
            AttemptRecord.model_validate_json(row[0])
            for row in connection.execute("SELECT document FROM attempts")
        ]
        selected = [record for record in records if record.period == period]
        liability = sum(record.liability_micro_rub for record in selected)
        settled = sum(
            record.liability_micro_rub
            for record in selected
            if record.state == AttemptState.SETTLED
        )
        return BudgetSnapshot(
            period=period,
            cap_micro_rub=cast(int, row[0]),
            liability_micro_rub=liability,
            unsettled_attempts=sum(
                record.state
                in {
                    AttemptState.RESERVED,
                    AttemptState.DISPATCHED,
                    AttemptState.UNKNOWN,
                }
                for record in selected
            ),
            settled_reported_micro_rub=settled,
            over_cap=liability > row[0],
        )
