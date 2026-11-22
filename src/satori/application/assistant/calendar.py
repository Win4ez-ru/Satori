"""Ephemeral selected-calendar consent and result admission, with no source I/O.

The desktop orchestrates native reads outside this coordinator. Only explicitly
issued, current, bounded requests can produce a temporary local snapshot. Nothing
here calls conversation, learning, persistence, a provider, or an operating-system API.
"""

from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from typing import NoReturn
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from satori.application.assistant.contracts import (
    MAX_GRANT_LIFETIME,
    MAX_READ_LIFETIME,
    MAX_RESULT_BYTES,
    MAX_SNAPSHOT_AGE,
    CalendarAccessCode,
    CalendarAuditEvent,
    CalendarAuditName,
    CalendarCatalog,
    CalendarCatalogDiagnostics,
    CalendarDescriptor,
    CalendarGrant,
    CalendarOccurrence,
    CalendarReadRequest,
    CalendarReadResult,
    CalendarSnapshot,
    CalendarSourceRef,
    LocalPrincipal,
    require_timezone,
    require_utc,
)


class CalendarAccessError(ValueError):
    """A closed code suitable for local UI diagnostics, without source text."""

    def __init__(self, code: CalendarAccessCode) -> None:
        self.code = code
        super().__init__(code)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class CalendarAccessCoordinator:
    """One local session owns one nonpersistent, revocable selected-read grant.

    ``accept_catalog`` admits the result of a separate explicit catalog operation;
    it grants no read capability. ``issue`` requires exact selection from that fresh
    catalog. ``prepare_read`` only prepares an immutable command, and callers must
    dispatch that exact command. ``accept_result`` checks the same capability again.
    A replacement request fences older callbacks; restart creates an empty owner.
    """

    def __init__(
        self,
        principal: LocalPrincipal,
        *,
        clock: Callable[[], datetime] = _utc_now,
        audit: Callable[[CalendarAuditEvent], None] | None = None,
    ) -> None:
        self._principal = principal
        self._clock = clock
        self._audit = audit
        self._last_now: datetime | None = None
        self._generation = 0
        self._catalog: tuple[CalendarDescriptor, ...] = ()
        self._catalog_observed_at: datetime | None = None
        self._catalog_summary: CalendarCatalogDiagnostics | None = None
        self._grant: CalendarGrant | None = None
        self._granted_calendars: tuple[CalendarDescriptor, ...] = ()
        self._pending: CalendarReadRequest | None = None
        self._snapshot: CalendarSnapshot | None = None

    @property
    def principal(self) -> LocalPrincipal:
        return self._principal

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def catalog(self) -> tuple[CalendarDescriptor, ...]:
        now = self._now()
        self._expire_catalog(now)
        return self._catalog

    def _expire_catalog(self, now: datetime) -> None:
        if self._catalog_observed_at is None or now - self._catalog_observed_at >= MAX_SNAPSHOT_AGE:
            self._catalog = ()
            self._catalog_observed_at = None

    @property
    def catalog_diagnostics(self) -> CalendarCatalogDiagnostics:
        now = self._now()
        self._expire_catalog(now)
        summary = self._catalog_summary
        if summary is None:
            return CalendarCatalogDiagnostics(state="never_loaded")
        assert summary.observed_at is not None
        return summary.model_copy(
            update={
                "state": "fresh" if self._catalog_observed_at is not None else "stale",
                "age_seconds": max(0.0, (now - summary.observed_at).total_seconds()),
            }
        )

    @property
    def grant(self) -> CalendarGrant | None:
        return self._current_grant(self._now())

    @property
    def granted_calendars(self) -> tuple[CalendarDescriptor, ...]:
        """Names bound to the live grant, independent of picker catalog freshness.

        This exposes only the already retained exact selection. It neither issues
        a grant nor prepares a read, and expiry/revoke drops these descriptors.
        """

        return self._granted_calendars if self.grant is not None else ()

    @property
    def snapshot(self) -> CalendarSnapshot | None:
        now = self._now()
        if self._current_grant(now) is None:
            return None
        if self._snapshot is not None and now >= self._snapshot.expires_at:
            request_id = self._snapshot.request.request_id
            self._snapshot = None
            self._emit("snapshot_expired", now, request_id=request_id, reason="snapshot_expired")
        return self._snapshot

    def accept_catalog(self, catalog: CalendarCatalog) -> tuple[CalendarDescriptor, ...]:
        """Admit a fresh explicitly requested catalog, invalidating any earlier grant."""

        now = self._now()
        self._clear(include_catalog=True)
        self._generation += 1
        if catalog.status != "ok":
            assert catalog.reason is not None
            self._deny(catalog.reason, now, request_id=catalog.request_id)
        if catalog.observed_at > now + timedelta(seconds=5):
            self._deny("invalid_catalog", now, request_id=catalog.request_id)
        if now - catalog.observed_at >= MAX_SNAPSHOT_AGE:
            self._deny("catalog_expired", now, request_id=catalog.request_id)
        self._emit(
            "catalog_accepted",
            now,
            request_id=catalog.request_id,
            resource_count=len(catalog.calendars),
        )
        self._catalog = catalog.calendars
        self._catalog_observed_at = catalog.observed_at
        capabilities = [item.write_capability for item in self._catalog]
        self._catalog_summary = CalendarCatalogDiagnostics(
            state="fresh",
            total_count=len(self._catalog),
            writable_count=capabilities.count("writable"),
            read_only_count=capabilities.count("read_only"),
            unreported_count=capabilities.count("not_reported"),
            unsupported_count=capabilities.count("unsupported_entity"),
            observed_at=catalog.observed_at,
            age_seconds=max(0.0, (now - catalog.observed_at).total_seconds()),
        )
        return self._catalog

    def issue(
        self,
        selected_sources: tuple[CalendarSourceRef, ...],
        *,
        lifetime: timedelta = MAX_GRANT_LIFETIME,
    ) -> CalendarGrant:
        """Explicit foreground consent for exact selected resources; no wildcard."""

        now = self._now()
        self._clear()
        self._generation += 1
        if self._catalog_observed_at is None or now - self._catalog_observed_at >= MAX_SNAPSHOT_AGE:
            self._deny("catalog_expired", now)
        admitted = {item.source: item for item in self._catalog}
        if (
            not isinstance(selected_sources, tuple)
            or not selected_sources
            or any(not isinstance(source, CalendarSourceRef) for source in selected_sources)
            or len(selected_sources) != len(set(selected_sources))
            or any(source not in admitted for source in selected_sources)
            or not isinstance(lifetime, timedelta)
            or not timedelta(0) < lifetime <= MAX_GRANT_LIFETIME
        ):
            self._deny("selection_invalid", now)
        grant = CalendarGrant(
            principal=self.principal,
            grant_id=uuid4(),
            generation=self._generation,
            resources=selected_sources,
            issued_at=now,
            expires_at=now + lifetime,
        )
        self._emit("grant_issued", now, grant=grant, resource_count=len(selected_sources))
        self._grant = grant
        self._granted_calendars = tuple(admitted[source] for source in selected_sources)
        return grant

    def revoke(self) -> None:
        """Invalidate pending work and drop every retained event payload immediately."""

        now = self._now()
        previous = self._grant
        self._clear(include_catalog=True)
        self._generation += 1
        self._emit("grant_revoked", now, grant=previous)

    def suspend_selection(self) -> None:
        """Stop reads while editing selection; the fresh picker catalog stays visible.

        This issues no replacement grant. A later explicit ``issue`` must still
        select exact resources from the unexpired catalog. Full Disconnect/Quit
        uses ``revoke`` and clears that catalog as well.
        """

        now = self._now()
        previous = self._grant
        self._clear()
        self._generation += 1
        self._emit("grant_revoked", now, grant=previous)

    def prepare_read(
        self,
        starts_at: datetime,
        ends_at: datetime,
        timezone: str,
        *,
        max_events: int = 100,
        max_bytes: int = MAX_RESULT_BYTES,
    ) -> CalendarReadRequest:
        """Prepare one read; native I/O must happen outside this policy owner."""

        now = self._now()
        grant = self._require_grant(now)
        try:
            request = CalendarReadRequest(
                request_id=uuid4(),
                principal=self.principal,
                grant_id=grant.grant_id,
                generation=grant.generation,
                resources=grant.resources,
                starts_at=starts_at,
                ends_at=ends_at,
                timezone=timezone,
                max_events=max_events,
                max_bytes=max_bytes,
                created_at=now,
                expires_at=min(grant.expires_at, now + MAX_READ_LIFETIME),
            )
        except ValidationError:
            self._deny("scope_mismatch", now)
        self._emit(
            "read_prepared",
            now,
            grant=grant,
            request_id=request.request_id,
            resource_count=len(request.resources),
        )
        self._pending = request
        return request

    def prepare_day(
        self,
        day: date,
        timezone: str,
        *,
        max_events: int = 100,
        max_bytes: int = MAX_RESULT_BYTES,
    ) -> CalendarReadRequest:
        """Use two local midnights, so DST days are not forced to 24 hours."""

        if not isinstance(day, date) or isinstance(day, datetime):
            self._deny("scope_mismatch", self._now())
        try:
            zone = ZoneInfo(require_timezone(timezone))
            boundaries = (
                datetime.combine(day, time.min, zone),
                datetime.combine(day + timedelta(days=1), time.min, zone),
            )
            for boundary in boundaries:
                if (
                    boundary.astimezone(UTC).astimezone(zone).replace(tzinfo=None)
                    != boundary.replace(tzinfo=None)
                    or boundary.replace(fold=1).utcoffset() != boundary.utcoffset()
                ):
                    raise ValueError("ambiguous_or_nonexistent_day_boundary")
            starts_at, ends_at = (boundary.astimezone(UTC) for boundary in boundaries)
        except (ValueError, OverflowError):
            self._deny("scope_mismatch", self._now())
        return self.prepare_read(
            starts_at, ends_at, timezone, max_events=max_events, max_bytes=max_bytes
        )

    def accept_result(
        self, request: CalendarReadRequest, result: CalendarReadResult
    ) -> CalendarSnapshot:
        """Recheck the exact request, grant and all attributed records before display."""

        now = self._now()
        grant = self._require_grant(now)
        if request.principal != self.principal:
            self._deny("scope_mismatch", now, request_id=request.request_id)
        if (
            request.grant_id != grant.grant_id
            or request.generation != grant.generation
            or request.resources != grant.resources
            or self._pending != request
        ):
            self._deny("stale_request", now, request_id=request.request_id)
        self._pending = None
        self._snapshot = None
        if now >= request.expires_at:
            self._deny("request_expired", now, request_id=request.request_id)
        if (
            result.request_id != request.request_id
            or result.observed_at < request.created_at
            or result.observed_at > now + timedelta(seconds=5)
            or result.observed_at >= request.expires_at
            or result.observed_at >= grant.expires_at
        ):
            self._deny("result_invalid", now, request_id=request.request_id)
        if result.status not in {"ok", "partial"}:
            assert result.reason is not None
            if result.status == "permission_denied":
                self._clear(include_catalog=True)
                self._generation += 1
            self._deny(result.reason, now, request_id=request.request_id)
        if (
            len(result.occurrences) > request.max_events
            or len(result.model_dump_json().encode("utf-8")) > request.max_bytes
        ):
            self._deny("result_too_large", now, request_id=request.request_id)
        seen: set[tuple[CalendarSourceRef, str, str]] = set()
        active: list[CalendarOccurrence] = []
        for occurrence in result.occurrences:
            if occurrence.source not in grant.resources:
                self._deny("source_mismatch", now, request_id=request.request_id)
            identity = (occurrence.source, occurrence.event_id, occurrence.occurrence_id)
            if identity in seen or not self._overlaps(occurrence, request):
                self._deny("result_invalid", now, request_id=request.request_id)
            seen.add(identity)
            if occurrence.status != "cancelled":
                active.append(occurrence)
        snapshot = CalendarSnapshot(
            request=request,
            observed_at=result.observed_at,
            expires_at=min(grant.expires_at, result.observed_at + MAX_SNAPSHOT_AGE),
            calendars=self._granted_calendars,
            occurrences=tuple(active),
            status="partial" if result.status == "partial" else "ok",
            reason=result.reason,
        )
        self._emit(
            "read_accepted",
            now,
            grant=grant,
            request_id=request.request_id,
            resource_count=len(grant.resources),
            event_count=len(active),
            reason=result.reason,
        )
        self._snapshot = snapshot
        return snapshot

    @staticmethod
    def _overlaps(occurrence: CalendarOccurrence, request: CalendarReadRequest) -> bool:
        if occurrence.starts_at is not None and occurrence.ends_at is not None:
            starts_at, ends_at = occurrence.starts_at, occurrence.ends_at
        else:
            assert occurrence.all_day_start is not None
            assert occurrence.all_day_end is not None
            zone = ZoneInfo(occurrence.timezone)
            try:
                starts_at = datetime.combine(occurrence.all_day_start, time.min, zone).astimezone(
                    UTC
                )
                ends_at = datetime.combine(occurrence.all_day_end, time.min, zone).astimezone(UTC)
            except (ValueError, OverflowError):
                return False
        return starts_at < ends_at and starts_at < request.ends_at and ends_at > request.starts_at

    def _now(self) -> datetime:
        try:
            now = require_utc(self._clock())
        except (ValueError, TypeError, AttributeError):
            self._clear(include_catalog=True)
            self._generation += 1
            raise CalendarAccessError("clock_invalid") from None
        if self._last_now is not None and now < self._last_now:
            self._clear(include_catalog=True)
            self._generation += 1
            raise CalendarAccessError("clock_invalid")
        self._last_now = now
        return now

    def _current_grant(self, now: datetime) -> CalendarGrant | None:
        if self._grant is not None and now >= self._grant.expires_at:
            previous = self._grant
            self._clear()
            self._generation += 1
            self._emit("grant_revoked", now, grant=previous, reason="grant_expired")
        return self._grant

    def _require_grant(self, now: datetime) -> CalendarGrant:
        expired = self._grant is not None and now >= self._grant.expires_at
        grant = self._current_grant(now)
        if grant is None:
            self._deny("grant_expired" if expired else "grant_required", now)
        return grant

    def _clear(self, *, include_catalog: bool = False) -> None:
        self._grant = None
        self._granted_calendars = ()
        self._pending = None
        self._snapshot = None
        if include_catalog:
            self._catalog = ()
            self._catalog_observed_at = None
            self._catalog_summary = None

    def _deny(
        self, code: CalendarAccessCode, now: datetime, *, request_id: UUID | None = None
    ) -> NoReturn:
        self._emit(
            "access_denied",
            now,
            request_id=request_id,
            reason=code,
        )
        raise CalendarAccessError(code)

    def _emit(
        self,
        event: CalendarAuditName,
        now: datetime,
        *,
        grant: CalendarGrant | None = None,
        request_id: UUID | None = None,
        resource_count: int = 0,
        event_count: int = 0,
        reason: CalendarAccessCode | None = None,
    ) -> None:
        if self._audit is None:
            return
        metadata = CalendarAuditEvent.model_validate(
            {
                "event": event,
                "occurred_at": now,
                "generation": self._generation,
                "grant_id": grant.grant_id if grant is not None else None,
                "request_id": request_id,
                "resource_count": resource_count,
                "event_count": event_count,
                "reason": reason,
            }
        )
        try:
            self._audit(metadata)
        except Exception:
            self._clear(include_catalog=True)
            self._generation += 1
            raise CalendarAccessError("audit_unavailable") from None
