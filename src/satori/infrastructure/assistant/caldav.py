"""iCloud CalDAV ports for existing read-grant and durable Calendar action owners.

No credentials, grants, persistence, learning or retry policy are owned here. Discovery
builds a private exact-resource map; callers must separately approve selected resources.
"""

import asyncio
import hashlib
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, TypeVar
from urllib.parse import urlsplit
from uuid import UUID

from satori.application.assistant.calendar_actions_contracts import (
    CalendarActionCode,
    CalendarActionError,
    CalendarActionScope,
    CalendarCreateAttempt,
    CalendarLookupReceipt,
    CalendarProviderReceipt,
    calendar_action_digest,
)
from satori.application.assistant.calendar_admission import require_calendar_host_admission
from satori.application.assistant.contracts import (
    CalendarCatalog,
    CalendarDescriptor,
    CalendarFailureReason,
    CalendarOccurrence,
    CalendarReadRequest,
    CalendarReadResult,
    CalendarSourceRef,
    require_identifier,
    require_utc,
)
from satori.infrastructure.assistant.caldav_ics import (
    create_calendar,
    exact_own_event,
    intersects,
    parse_calendar,
    project_event,
    utc_wire,
)
from satori.infrastructure.assistant.caldav_transport import (
    CalDAVError,
    CalDAVHTTP,
    CalDAVResponse,
    icloud_url,
    resolve_icloud_href,
)
from satori.infrastructure.assistant.caldav_xml import (
    CAL,
    DAV,
    DAVResource,
    calendar_query,
    multistatus,
    propfind,
    tag,
)

_CATALOG_PROPS = (
    (DAV, "resourcetype"),
    (DAV, "displayname"),
    (DAV, "current-user-privilege-set"),
    (DAV, "supported-report-set"),
    (CAL, "supported-calendar-component-set"),
)
_T = TypeVar("_T")


@dataclass(frozen=True, slots=True, repr=False)
class _Collection:
    url: str
    descriptor: CalendarDescriptor
    query_supported: bool


class _Operation:
    def __init__(self, deadline: float) -> None:
        self.deadline = deadline
        self.cancelled = threading.Event()
        self.loop = asyncio.get_running_loop()
        self.owner_thread = threading.get_ident()

    def check(self) -> None:
        if self.cancelled.is_set() or time.monotonic() >= self.deadline:
            raise CalDAVError("caldav_operation_expired")

    def admit(self, callback: Callable[[], None]) -> None:
        """Owner callbacks may access SQLite; keep them on the originating loop thread."""
        self.check()
        if threading.get_ident() == self.owner_thread:
            callback()
            self.check()
            return

        async def invoke() -> None:
            self.check()
            callback()
            self.check()

        pending = asyncio.run_coroutine_threadsafe(invoke(), self.loop)
        try:
            pending.result(timeout=max(0, self.deadline - time.monotonic()))
        except TimeoutError:
            pending.cancel()
            raise CalDAVError("caldav_operation_expired") from None


class ICloudCalDAVAdapter:
    provider_kind: Literal["caldav"] = "caldav"
    external_effects = True

    def __init__(
        self,
        account_id: str,
        http: CalDAVHTTP,
        *,
        read_admission: Callable[[CalendarActionScope], None] | None = None,
        clock: Callable[[], datetime] | None = None,
        bootstrap_url: str = "https://caldav.icloud.com/",
    ) -> None:
        self._account = require_identifier(account_id)
        self._http = http
        self._read_admission = read_admission
        self._clock = clock or (lambda: datetime.now(UTC))
        self._bootstrap = icloud_url(bootstrap_url)
        self._collections: dict[str, _Collection] = {}
        self._map_lock = threading.Lock()

    def _now(self) -> datetime:
        return require_utc(self._clock())

    async def _run(self, callback: Callable[[_Operation], _T], seconds: float) -> _T:
        operation = _Operation(time.monotonic() + min(30, max(0, seconds)))
        try:
            async with asyncio.timeout(min(30, max(0, seconds))):
                return await asyncio.to_thread(callback, operation)
        finally:
            # A cancelled queued worker must not dispatch after the coroutine returned.
            operation.cancelled.set()

    def _guard(self, operation: _Operation, before: Callable[[], None]) -> None:
        def require() -> None:
            operation.check()
            require_calendar_host_admission()
            before()
            operation.check()

        operation.admit(require)

    def _request(
        self,
        operation: _Operation,
        method: str,
        url: str,
        before: Callable[[], None],
        *,
        body: bytes = b"",
        depth: str | None = None,
        redirects: bool = False,
    ) -> CalDAVResponse:
        headers = {"Cache-Control": "no-cache", "Accept": "application/xml, text/calendar"}
        if depth is not None:
            headers["Depth"] = depth
        if body:
            headers["Content-Type"] = "application/xml; charset=utf-8"
        current = icloud_url(url)
        for hop in range(3):
            response = self._http.request(
                method,
                current,
                headers=headers,
                body=body,
                before_dispatch=lambda: self._guard(operation, before),
            )
            if response.status not in {301, 302, 307, 308}:
                return response
            if not redirects or method not in {"PROPFIND", "REPORT"} or hop == 2:
                raise CalDAVError("caldav_redirect_rejected")
            current = resolve_icloud_href(current, response.headers.get("location", ""))
        raise CalDAVError("caldav_redirect_rejected")

    def _props(
        self,
        operation: _Operation,
        url: str,
        before: Callable[[], None],
        names: tuple[tuple[str, str], ...],
        *,
        depth: str = "0",
    ) -> tuple[DAVResource, ...]:
        response = self._request(
            operation, "PROPFIND", url, before, body=propfind(*names), depth=depth, redirects=True
        )
        if response.status in {401, 403}:
            raise CalDAVError("caldav_permission_denied")
        if response.status != 207:
            raise CalDAVError("caldav_response_invalid")
        return multistatus(response.body)

    def _single_href(self, resources: tuple[DAVResource, ...], key: str, base: str) -> str:
        if len(resources) != 1 or resources[0].failed:
            raise CalDAVError("caldav_response_invalid")
        prop = resources[0].properties.get(key)
        if prop is None or len(prop) != 1 or prop[0].tag != tag(DAV, "href"):
            raise CalDAVError("caldav_response_invalid")
        return resolve_icloud_href(base, prop[0].text or "")

    async def discover(
        self, request_id: UUID, *, before_read: Callable[[], None]
    ) -> CalendarCatalog:
        def work(operation: _Operation) -> tuple[_Collection, ...]:
            root = self._props(
                operation, self._bootstrap, before_read, ((DAV, "current-user-principal"),)
            )
            principal = self._single_href(root, tag(DAV, "current-user-principal"), self._bootstrap)
            home_result = self._props(
                operation, principal, before_read, ((CAL, "calendar-home-set"),)
            )
            home = self._single_href(home_result, tag(CAL, "calendar-home-set"), principal)
            if not home.endswith("/"):
                raise CalDAVError("caldav_response_invalid")
            resources = self._props(operation, home, before_read, _CATALOG_PROPS, depth="1")
            collected: list[_Collection] = []
            seen: set[str] = set()
            for item in resources:
                url = resolve_icloud_href(home, item.href)
                if url == home:
                    continue
                if not _direct_child(home, url) or url in seen or item.failed:
                    raise CalDAVError("caldav_response_invalid")
                seen.add(url)
                kind = item.properties.get(tag(DAV, "resourcetype"))
                if kind is None:
                    raise CalDAVError("caldav_response_invalid")
                if kind.find(tag(CAL, "calendar")) is None:
                    continue
                collected.append(self._collection(item, url))
                if len(collected) > 100:
                    raise CalDAVError("caldav_catalog_limit")
            operation.check()
            self._guard(operation, before_read)
            return tuple(collected)

        try:
            result = await self._run(work, 30)
            assert isinstance(result, tuple)
            require_calendar_host_admission()
            before_read()
            with self._map_lock:
                self._collections = {c.descriptor.source.calendar_id: c for c in result}
            return CalendarCatalog(
                request_id=request_id,
                observed_at=self._now(),
                calendars=tuple(c.descriptor for c in result),
            )
        except (ValueError, TimeoutError) as error:
            with self._map_lock:
                self._collections = {}
            status, reason = _read_failure(error)
            return CalendarCatalog(
                request_id=request_id, observed_at=self._now(), status=status, reason=reason
            )

    def _collection(self, item: DAVResource, url: str) -> _Collection:
        if not url.endswith("/"):
            raise CalDAVError("caldav_response_invalid")
        kind = item.properties.get(tag(DAV, "resourcetype"))
        if kind is None or kind.find(tag(CAL, "calendar")) is None:
            raise CalDAVError("caldav_response_invalid")
        # Unknown nonstandard resource types are not promoted to writable calendars.
        subscribed = any(
            child.tag not in {tag(DAV, "collection"), tag(CAL, "calendar")} for child in kind
        )
        components = item.properties.get(tag(CAL, "supported-calendar-component-set"))
        supports = (
            None
            if components is None
            else any(
                child.tag == tag(CAL, "comp") and child.attrib.get("name") == "VEVENT"
                for child in components
            )
        )
        privileges = item.properties.get(tag(DAV, "current-user-privilege-set"))
        writable: bool | None = None
        if privileges is not None:
            names = {node.tag for privilege in privileges for node in privilege}
            writable = bool({tag(DAV, "all"), tag(DAV, "write")} & names) or {
                tag(DAV, "bind"),
                tag(DAV, "write-content"),
            }.issubset(names)
        if subscribed or supports is False:
            writable = False
        title = item.properties.get(tag(DAV, "displayname"))
        if title is None or not title.text:
            raise CalDAVError("caldav_response_invalid")
        reports = item.properties.get(tag(DAV, "supported-report-set"))
        query_supported = reports is not None and any(
            node.tag == tag(CAL, "calendar-query") for node in reports.iter()
        )
        source = CalendarSourceRef(
            provider="caldav",
            account_id=self._account,
            calendar_id=hashlib.sha256(url.encode()).hexdigest(),
        )
        return _Collection(
            url,
            CalendarDescriptor(
                source=source,
                title=title.text,
                account_title="iCloud",
                writable=writable,
                allows_modification=writable,
                supports_events=supports,
                source_kind="subscribed" if subscribed else "caldav",
            ),
            query_supported,
        )

    def _selected(self, source: CalendarSourceRef) -> _Collection:
        if source.provider != "caldav" or source.account_id != self._account:
            raise CalDAVError("caldav_scope_mismatch")
        with self._map_lock:
            collection = self._collections.get(source.calendar_id)
        if collection is None or collection.descriptor.source != source:
            raise CalDAVError("caldav_scope_mismatch")
        return collection

    def _query(
        self,
        operation: _Operation,
        collection: _Collection,
        start: datetime,
        end: datetime,
        timezone: str,
        before: Callable[[], None],
        *,
        max_events: int = 100,
        max_bytes: int = 512 * 1024,
    ) -> tuple[tuple[CalendarOccurrence, ...], bool, bool, int]:
        if not collection.query_supported:
            return (), True, False, 0
        response = self._request(
            operation,
            "REPORT",
            collection.url,
            before,
            body=calendar_query(utc_wire(start), utc_wire(end)),
            depth="1",
        )
        if response.status in {401, 403}:
            raise CalDAVError("caldav_permission_denied")
        if response.status != 207:
            raise CalDAVError("caldav_response_invalid")
        if len(response.body) > max_bytes:
            return (), False, True, len(response.body)
        result: list[CalendarOccurrence] = []
        seen: set[str] = set()
        unsupported = False
        limited = False
        for item in multistatus(response.body):
            href = resolve_icloud_href(collection.url, item.href)
            if not _direct_child(collection.url, href) or href in seen:
                raise CalDAVError("caldav_response_invalid")
            seen.add(href)
            data, etag = (
                item.properties.get(tag(CAL, "calendar-data")),
                item.properties.get(tag(DAV, "getetag")),
            )
            if item.failed or data is None or data.text is None or etag is None or not etag.text:
                unsupported = True
                continue
            try:
                events = parse_calendar(data.text.encode("utf-8"))
                if not events:
                    unsupported = True
                for event in events:
                    projected = project_event(
                        event,
                        source=collection.descriptor.source,
                        href=href,
                        revision=etag.text,
                        timezone=timezone,
                    )
                    if intersects(projected, start, end):
                        if len(result) >= max_events:
                            limited = True
                            break
                        result.append(projected)
            except CalDAVError as error:
                if error.code.endswith("limit"):
                    limited = True
                else:
                    unsupported = True
        identifiers = [item.occurrence_id for item in result]
        if len(identifiers) != len(set(identifiers)):
            unsupported = True
        return tuple(result), unsupported, limited, len(response.body)

    async def read_events(
        self, request: CalendarReadRequest, *, before_read: Callable[[], None]
    ) -> CalendarReadResult:
        def work(operation: _Operation) -> CalendarReadResult:
            result: list[CalendarOccurrence] = []
            unsupported = limited = False
            budget = request.max_bytes
            for source in request.resources:
                collection = self._selected(source)
                items, missing, limit, consumed = self._query(
                    operation,
                    collection,
                    request.starts_at,
                    request.ends_at,
                    request.timezone,
                    before_read,
                    max_events=max(1, request.max_events - len(result)),
                    max_bytes=budget,
                )
                unsupported |= missing
                limited |= limit
                result.extend(items)
                budget -= max(consumed, sum(len(item.model_dump_json().encode()) for item in items))
                if len(result) >= request.max_events or budget <= 0:
                    limited = True
                    break
            self._guard(operation, before_read)
            return CalendarReadResult(
                request_id=request.request_id,
                observed_at=self._now(),
                status="partial" if unsupported or limited else "ok",
                reason="unsupported_projection"
                if unsupported
                else "limit_reached"
                if limited
                else None,
                occurrences=tuple(result[: request.max_events]),
            )

        try:
            if not request.created_at <= self._now() < request.expires_at:
                raise CalDAVError("caldav_operation_expired")
            result = await self._run(work, (request.expires_at - self._now()).total_seconds())
            assert isinstance(result, CalendarReadResult)
            before_read()
            require_calendar_host_admission()
            return result
        except (ValueError, TimeoutError) as error:
            status, reason = _read_failure(error)
            return CalendarReadResult(
                request_id=request.request_id, observed_at=self._now(), status=status, reason=reason
            )

    def _attempt_collection(self, attempt: CalendarCreateAttempt) -> _Collection:
        scope = attempt.proposal.scope
        expected = calendar_action_digest(
            {
                "schema": "satori.calendar-create-attempt.v1",
                "request_id": str(attempt.proposal.command.request_id),
                "payload_digest": attempt.proposal.payload_digest,
            }
        )
        if scope.provider != "caldav" or expected != attempt.idempotency_token:
            raise CalDAVError("caldav_scope_mismatch")
        return self._selected(
            CalendarSourceRef(
                provider="caldav", account_id=scope.account_id, calendar_id=scope.calendar_id
            )
        )

    def _resource_url(self, collection: _Collection, attempt: CalendarCreateAttempt) -> str:
        return icloud_url(collection.url + attempt.idempotency_token + ".ics")

    def _receipt(
        self,
        attempt: CalendarCreateAttempt,
        status: Literal["created", "rejected", "unknown"],
        *,
        reason: CalendarActionCode | None = None,
        event_id: str | None = None,
    ) -> CalendarProviderReceipt:
        return CalendarProviderReceipt(
            attempt_id=attempt.attempt_id,
            idempotency_token=attempt.idempotency_token,
            payload_digest=attempt.proposal.payload_digest,
            provider="caldav",
            status=status,
            observed_at=max(self._now(), attempt.claimed_at),
            event_id=event_id,
            exact_fields_verified=status == "created",
            fresh_store_verified=status == "created",
            reason=reason,
        )

    async def create(
        self, attempt: CalendarCreateAttempt, *, before_effect: Callable[[], None]
    ) -> CalendarProviderReceipt:
        dispatched = threading.Event()
        callback_refused = threading.Event()

        if self._read_admission is None:
            return self._receipt(attempt, "rejected", reason="calendar_action_permission_required")

        read_admission = self._read_admission

        def source_read() -> None:
            read_admission(attempt.proposal.scope)

        def work(operation: _Operation) -> CalendarProviderReceipt:
            collection = self._attempt_collection(attempt)
            fresh = self._props(operation, collection.url, source_read, _CATALOG_PROPS)
            if (
                len(fresh) != 1
                or resolve_icloud_href(collection.url, fresh[0].href) != collection.url
            ):
                raise CalDAVError("caldav_response_invalid")
            current = self._collection(fresh[0], collection.url)
            if (
                current.descriptor.writable is not True
                or current.descriptor.supports_events is not True
            ):
                return self._receipt(attempt, "rejected", reason="calendar_action_not_writable")
            command = attempt.proposal.command
            items, missing, limit, _ = self._query(
                operation,
                current,
                command.starts_at,
                command.ends_at,
                command.timezone,
                source_read,
            )
            if missing or limit or any(item.status != "cancelled" for item in items):
                return self._receipt(attempt, "rejected", reason="calendar_action_request_conflict")
            url = self._resource_url(current, attempt)

            def effect_guard() -> None:
                def authorize() -> None:
                    self._guard(operation, source_read)
                    if not attempt.claimed_at <= self._now() < attempt.expires_at:
                        raise CalDAVError("caldav_operation_expired")
                    try:
                        before_effect()
                    except Exception:
                        callback_refused.set()
                        raise
                    operation.check()
                    dispatched.set()  # An admitted PUT has no resend path.

                operation.admit(authorize)

            response = self._http.request(
                "PUT",
                url,
                headers={"Content-Type": "text/calendar; charset=utf-8", "If-None-Match": "*"},
                body=create_calendar(attempt, self._now()),
                before_dispatch=effect_guard,
            )
            if response.status == 412:
                return self._receipt(attempt, "rejected", reason="calendar_action_request_conflict")
            if response.status != 201:
                return self._receipt(attempt, "unknown", reason="calendar_action_unknown_no_replay")
            readback = self._request(operation, "GET", url, source_read)
            if readback.status == 200 and exact_own_event(readback.body, attempt):
                return self._receipt(attempt, "created", event_id=attempt.idempotency_token)
            return self._receipt(attempt, "unknown", reason="calendar_action_unknown_no_replay")

        try:
            if not attempt.claimed_at <= self._now() < attempt.expires_at:
                return self._receipt(attempt, "rejected", reason="calendar_action_stale")
            result = await self._run(work, (attempt.expires_at - self._now()).total_seconds())
            assert isinstance(result, CalendarProviderReceipt)
            return result
        except Exception:
            if callback_refused.is_set() and not dispatched.is_set():
                raise
            return self._receipt(
                attempt,
                "unknown" if dispatched.is_set() else "rejected",
                reason="calendar_action_unknown_no_replay"
                if dispatched.is_set()
                else "calendar_action_provider_unavailable",
            )

    async def lookup(
        self,
        attempt: CalendarCreateAttempt,
        scope: CalendarActionScope,
        *,
        before_read: Callable[[], None],
    ) -> CalendarLookupReceipt:
        def result(
            status: Literal["owned_exact", "absent", "conflict", "unknown"],
        ) -> CalendarLookupReceipt:
            return CalendarLookupReceipt(
                attempt_id=attempt.attempt_id,
                idempotency_token=attempt.idempotency_token,
                payload_digest=attempt.proposal.payload_digest,
                provider="caldav",
                status=status,
                observed_at=max(self._now(), attempt.claimed_at),
                event_id=attempt.idempotency_token if status == "owned_exact" else None,
                exact_fields_verified=status == "owned_exact",
                fresh_store_verified=status != "unknown",
            )

        def work(operation: _Operation) -> CalendarLookupReceipt:
            if not scope.same_resource(attempt.proposal.scope):
                raise CalendarActionError("calendar_action_scope_mismatch")
            collection = self._attempt_collection(attempt)
            response = self._request(
                operation, "GET", self._resource_url(collection, attempt), before_read
            )
            if response.status == 404:
                return result("absent")
            if response.status == 200:
                return result(
                    "owned_exact" if exact_own_event(response.body, attempt) else "conflict"
                )
            return result("unknown")

        try:
            receipt = await self._run(work, 30)
            assert isinstance(receipt, CalendarLookupReceipt)
            return receipt
        except CalendarActionError:
            raise
        except Exception:
            return result("unknown")


def _direct_child(parent: str, child: str) -> bool:
    left, right = urlsplit(parent), urlsplit(child)
    suffix = right.path.removeprefix(left.path).rstrip("/")
    return (
        left.netloc == right.netloc
        and left.path.endswith("/")
        and right.path.startswith(left.path)
        and bool(suffix)
        and "/" not in suffix
    )


def _read_failure(
    error: Exception,
) -> tuple[Literal["permission_denied", "unavailable", "error"], CalendarFailureReason]:
    if isinstance(error, CalDAVError) and error.code == "caldav_permission_denied":
        return "permission_denied", "permission_denied"
    if isinstance(error, CalDAVError) and error.code == "caldav_catalog_limit":
        return "error", "catalog_too_large"
    if (
        isinstance(error, CalDAVError)
        and error.code
        in {
            "caldav_transport_unavailable",
            "caldav_operation_expired",
            "caldav_credentials_unavailable",
        }
    ) or isinstance(error, TimeoutError):
        return "unavailable", "provider_unavailable"
    return "error", "invalid_response"
