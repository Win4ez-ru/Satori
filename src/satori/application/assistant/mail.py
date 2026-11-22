"""Offline selected-mail policy owner; no source I/O, inference or persistent writes."""

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal, NoReturn
from uuid import UUID, uuid4

from pydantic import ValidationError

from satori.application.assistant.contracts import LocalPrincipal
from satori.application.assistant.mail_contracts import (
    MAX_MAIL_GRANT_LIFETIME,
    MAX_MAIL_REQUEST_LIFETIME,
    MAX_MAIL_SNAPSHOT_AGE,
    MailAccessCode,
    MailAccessDiagnostic,
    MailAccessError,
    MailAuditEvent,
    MailCatalog,
    MailClock,
    MailReadGrant,
    MailReadRequest,
    MailReadResult,
    MailSelectionProof,
    MailSnapshot,
    MailSourceRef,
)
from satori.application.assistant.mail_projection import project_gmail_batch

LiteralEvent = Literal[
    "catalog_admitted",
    "selection_confirmed",
    "grant_issued",
    "grant_revoked",
    "read_prepared",
    "read_admitted",
    "read_rejected",
    "snapshot_expired",
]


def _clock() -> MailClock:
    return MailClock(wall=datetime.now(UTC), monotonic=time.monotonic())


class MailAccessCoordinator:
    """Ephemeral exact-selection capabilities for a trusted future adapter.

    Catalog admission is not an account connection or proof of OAuth identity. An
    explicit selection from that catalog must precede grant issuance. The owner
    parses raw bounded results itself; caller-provided projections are not receipts.
    """

    def __init__(
        self,
        principal: LocalPrincipal,
        *,
        clock: Callable[[], MailClock] = _clock,
        audit: Callable[[MailAuditEvent], None] | None = None,
    ) -> None:
        self._principal = principal
        self._clock = clock
        self._audit = audit
        self._last_now: MailClock | None = None
        self._generation = 0
        self._revision = 0
        self._catalog: MailCatalog | None = None
        self._selection: MailSelectionProof | None = None
        self._grant: MailReadGrant | None = None
        self._pending: MailReadRequest | None = None
        self._snapshot: MailSnapshot | None = None

    @property
    def principal(self) -> LocalPrincipal:
        return self._principal

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def grant(self) -> MailReadGrant | None:
        return self._current_grant(self._now())

    @property
    def catalog(self) -> MailCatalog | None:
        now = self._now()
        if self._catalog is not None and self._catalog_expired(now):
            self._catalog = None
            self._selection = None
        return self._catalog

    @property
    def snapshot(self) -> MailSnapshot | None:
        now = self._now()
        if self._current_grant(now) is None:
            return None
        snapshot = self._snapshot
        if snapshot is not None and (
            now.wall >= snapshot.expires_at or now.monotonic >= snapshot.monotonic_deadline
        ):
            self._snapshot = None
            self._emit("snapshot_expired", now, request_id=snapshot.request.request_id)
        return self._snapshot

    def admit_catalog(self, catalog: MailCatalog) -> None:
        """Admit an explicitly obtained local catalog, invalidating previous work."""

        now = self._now()
        self._invalidate(include_catalog=True)
        if catalog.principal != self.principal:
            self._deny("scope_mismatch", now)
        age = (now.wall - catalog.observed.wall).total_seconds()
        mono_age = now.monotonic - catalog.observed.monotonic
        if not 0 <= age < 300 or not 0 <= mono_age < 300:
            self._deny("catalog_invalid", now)
        self._catalog = catalog
        self._emit("catalog_admitted", now, count=len(catalog.message_ids))

    def select(self, message_ids: tuple[str, ...], *, confirmed: bool) -> MailSelectionProof:
        """Record explicit exact IDs from the current account/catalog, not a folder."""

        now = self._now()
        catalog = self._catalog
        self._invalidate()
        if catalog is None or self._catalog_expired(now):
            self._deny("catalog_expired", now)
        assert catalog is not None
        if (
            confirmed is not True
            or not isinstance(message_ids, tuple)
            or any(item not in catalog.message_ids for item in message_ids)
        ):
            self._deny("selection_invalid", now)
        self._revision += 1
        try:
            selection = MailSelectionProof(
                principal=self.principal,
                account=catalog.account,
                catalog_id=catalog.catalog_id,
                selection_id=uuid4(),
                generation=self._generation,
                revision=self._revision,
                resources=tuple(
                    MailSourceRef(account=catalog.account, message_id=item) for item in message_ids
                ),
            )
        except (ValidationError, TypeError):
            self._deny("selection_invalid", now)
        self._selection = selection
        self._emit("selection_confirmed", now, count=len(selection.resources))
        current = self._now()
        if self._selection != selection or self._catalog_expired(current):
            self._deny("selection_invalid", current)
        return selection

    def issue(
        self,
        selection: MailSelectionProof,
        *,
        confirmed: bool,
        lifetime: timedelta = MAX_MAIL_GRANT_LIFETIME,
    ) -> MailReadGrant:
        """Consume the current selection proof once; no automatic renewal."""

        now = self._now()
        if self._catalog is None or self._catalog_expired(now):
            self._deny("catalog_expired", now)
        if (
            confirmed is not True
            or not isinstance(selection, MailSelectionProof)
            or self._selection != selection
        ):
            self._deny("selection_invalid", now)
        self._selection = None
        if (
            not isinstance(lifetime, timedelta)
            or not timedelta(0) < lifetime <= MAX_MAIL_GRANT_LIFETIME
        ):
            self._deny("selection_invalid", now)
        grant = MailReadGrant(
            grant_id=uuid4(),
            selection=selection,
            issued=now,
            expires_at=now.wall + lifetime,
            monotonic_deadline=now.monotonic + lifetime.total_seconds(),
        )
        self._grant = grant
        self._emit("grant_issued", now, grant_id=grant.grant_id, count=len(selection.resources))
        current = self._now()
        if self._require_grant(current) != grant:
            self._deny("scope_mismatch", current)
        return grant

    def prepare_read(self) -> MailReadRequest:
        """Prepare one immutable 30-second command; performs no source access."""

        now = self._now()
        grant = self._require_grant(now)
        if self._pending is not None:
            if (
                now.wall < self._pending.expires_at
                and now.monotonic < self._pending.monotonic_deadline
            ):
                self._deny("stale_request", now)
            expired_id = self._pending.request_id
            self._pending = None
            self._emit("read_rejected", now, request_id=expired_id, reason="request_expired")
            now = self._now()
            if self._require_grant(now) != grant or self._pending is not None:
                self._deny("stale_request", now)
        seconds = min(
            MAX_MAIL_REQUEST_LIFETIME.total_seconds(),
            (grant.expires_at - now.wall).total_seconds(),
            grant.monotonic_deadline - now.monotonic,
        )
        request = MailReadRequest(
            request_id=uuid4(),
            grant=grant,
            created=now,
            expires_at=now.wall + timedelta(seconds=seconds),
            monotonic_deadline=now.monotonic + seconds,
        )
        self._snapshot = None
        self._pending = request
        self._emit("read_prepared", now, request_id=request.request_id, grant_id=grant.grant_id)
        current = self._now()
        if self._require_grant(current) != grant or self._pending != request:
            self._deny("stale_request", current, request_id=request.request_id)
        self._check_request_time(request, current)
        return request

    def accept_result(self, request: MailReadRequest, result: MailReadResult) -> MailSnapshot:
        """Consume a single current result and recheck capabilities after parsing."""

        now = self._now()
        grant = self._require_grant(now)
        if self._pending != request or request.grant != grant:
            self._deny("stale_request", now, request_id=request.request_id)
        self._pending = None
        self._snapshot = None
        self._check_request_time(request, now)
        selection = grant.selection
        if (
            result.request_id != request.request_id
            or result.principal != self.principal
            or result.account != selection.account
            or result.generation != self._generation
            or result.selection_revision != selection.revision
            or result.observed.wall < request.created.wall
            or result.observed.wall > now.wall
            or result.observed.monotonic < request.created.monotonic
            or result.observed.monotonic > now.monotonic
        ):
            self._deny("result_invalid", now, request_id=request.request_id)
        try:
            messages = project_gmail_batch(result.messages, selection.resources)
        except MailAccessError as error:
            self._deny(error.code, now, request_id=request.request_id, diagnostic=error.diagnostic)
        completed = self._now()
        if self._require_grant(completed) != grant or self._generation != selection.generation:
            self._deny("stale_request", completed, request_id=request.request_id)
        self._check_request_time(request, completed)
        seconds = min(
            MAX_MAIL_SNAPSHOT_AGE.total_seconds(),
            (grant.expires_at - result.observed.wall).total_seconds(),
            grant.monotonic_deadline - result.observed.monotonic,
        )
        snapshot = MailSnapshot(
            snapshot_id=uuid4(),
            request=request,
            observed=result.observed,
            expires_at=result.observed.wall + timedelta(seconds=seconds),
            monotonic_deadline=result.observed.monotonic + seconds,
            messages=messages,
        )
        if (
            completed.wall >= snapshot.expires_at
            or completed.monotonic >= snapshot.monotonic_deadline
        ):
            self._deny("snapshot_expired", completed, request_id=request.request_id)
        self._snapshot = snapshot
        self._emit(
            "read_admitted",
            completed,
            request_id=request.request_id,
            grant_id=grant.grant_id,
            count=len(messages),
        )
        current = self._now()
        if self._require_grant(current) != grant or self._snapshot != snapshot:
            self._deny("stale_request", current, request_id=request.request_id)
        if current.wall >= snapshot.expires_at or current.monotonic >= snapshot.monotonic_deadline:
            self._snapshot = None
            self._deny("snapshot_expired", current, request_id=request.request_id)
        return snapshot

    def revoke(self) -> None:
        now = self._now()
        grant = self._grant
        self._invalidate(include_catalog=True)
        self._emit("grant_revoked", now, grant_id=grant.grant_id if grant else None)

    def _catalog_expired(self, now: MailClock) -> bool:
        if self._catalog is None:
            return True
        return (
            now.wall - self._catalog.observed.wall
        ) >= MAX_MAIL_SNAPSHOT_AGE or now.monotonic - self._catalog.observed.monotonic >= 300

    def _check_request_time(self, request: MailReadRequest, now: MailClock) -> None:
        if now.wall >= request.expires_at or now.monotonic >= request.monotonic_deadline:
            self._deny("request_expired", now, request_id=request.request_id)

    def _invalidate(self, *, include_catalog: bool = False) -> None:
        self._generation += 1
        self._selection = None
        self._grant = None
        self._pending = None
        self._snapshot = None
        if include_catalog:
            self._catalog = None

    def _current_grant(self, now: MailClock) -> MailReadGrant | None:
        grant = self._grant
        if grant is not None and (
            now.wall >= grant.expires_at or now.monotonic >= grant.monotonic_deadline
        ):
            self._invalidate(include_catalog=True)
            self._emit("grant_revoked", now, grant_id=grant.grant_id, reason="grant_expired")
        return self._grant

    def _require_grant(self, now: MailClock) -> MailReadGrant:
        expired = self._grant is not None and (
            now.wall >= self._grant.expires_at or now.monotonic >= self._grant.monotonic_deadline
        )
        grant = self._current_grant(now)
        if grant is None:
            self._deny("grant_expired" if expired else "grant_required", now)
        assert grant is not None
        return grant

    def _now(self) -> MailClock:
        try:
            now = MailClock.model_validate(self._clock().model_dump())
        except Exception:
            self._invalidate(include_catalog=True)
            raise MailAccessError("clock_invalid") from None
        previous = self._last_now
        if previous is not None and (
            now.wall < previous.wall or now.monotonic < previous.monotonic
        ):
            self._invalidate(include_catalog=True)
            raise MailAccessError("clock_invalid")
        self._last_now = now
        return now

    def _emit(
        self,
        event: LiteralEvent,
        now: MailClock,
        *,
        request_id: UUID | None = None,
        grant_id: UUID | None = None,
        count: int = 0,
        reason: MailAccessCode | None = None,
    ) -> None:
        if self._audit is None:
            return
        try:
            self._audit(
                MailAuditEvent(
                    event=event,
                    occurred_at=now.wall,
                    generation=self._generation,
                    request_id=request_id,
                    grant_id=grant_id,
                    resource_count=count,
                    reason=reason,
                )
            )
        except Exception:
            self._invalidate(include_catalog=True)
            raise MailAccessError("audit_unavailable") from None

    def _deny(
        self,
        code: MailAccessCode,
        now: MailClock,
        *,
        request_id: UUID | None = None,
        diagnostic: MailAccessDiagnostic | None = None,
    ) -> NoReturn:
        self._emit("read_rejected", now, request_id=request_id, reason=code)
        raise MailAccessError(code, diagnostic)
