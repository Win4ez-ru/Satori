"""Foreground Gmail reads in a transient local view, isolated from the Core."""

import os
import time
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from typing import Literal, get_args
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from PySide6.QtCore import QObject, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from satori.application.assistant.contracts import LocalPrincipal
from satori.application.assistant.mail import MailAccessCoordinator
from satori.application.assistant.mail_contracts import (
    MailAccessCode,
    MailAccessDiagnostic,
    MailAccessError,
    MailAuditEvent,
    MailClock,
    MailReadRequest,
    MailReadResult,
    MailSnapshot,
    mail_access_diagnostic,
)
from satori.application.assistant.mail_search_contracts import MailSearchRequest
from satori.application.assistant.source_answer_contracts import (
    SourceSearchPlan,
    SourceTaskRequest,
    SourceTaskSnapshot,
)
from satori.application.assistant.source_task_reads import task_search_admission, task_snapshot
from satori.desktop.source_status import (
    CatalogStatus,
    ConnectionStatus,
    SnapshotStatus,
    SourceProblem,
    SourceStatus,
)
from satori.infrastructure.gmail import (
    GmailAccount,
    GmailClientConfiguration,
    GmailConnector,
    GmailDisconnectResult,
    GmailError,
    GmailInboxPage,
    GmailLoginStart,
    GmailMessageHeader,
    load_client_configuration,
)
from satori.infrastructure.gmail.contracts import (
    GmailErrorCode,
    GmailHeaderOmission,
    GmailResponseDiagnostic,
    GmailSearchPage,
    gmail_response_diagnostic,
)

Operation = Literal["begin", "login", "list", "search", "task", "read", "disconnect", "revoke"]
ConnectionState = Literal["disconnected", "connecting", "active", "expired", "closing"]
GoogleState = Literal["not_checked", "responded", "unavailable"]
_RECEIPT_ERRORS = (
    frozenset(get_args(GmailErrorCode))
    | frozenset(get_args(MailAccessCode))
    | {
        "operation_failed",
        "browser_unavailable",
        "authorization_invalid",
    }
)
_RECEIPT_RESULTS: dict[Operation, frozenset[str]] = {
    "task": frozenset({"task_ready", "task_partial", "task_empty"}),
    "begin": frozenset(),
    "login": frozenset({"connected"}),
    "list": frozenset({"listed", "listed_partial", "headers_unsupported", "empty"}),
    "search": frozenset(
        {"searched", "searched_partial", "search_headers_unsupported", "search_empty"}
    ),
    "read": frozenset({"ready", "ready_partial", "read_unsupported"}),
    "disconnect": frozenset({"disconnected"}),
    "revoke": frozenset(
        {"disconnected_remote_revoked", "disconnected_remote_failed", "disconnected_remote_unknown"}
    ),
}


@dataclass(frozen=True)
class MailActionReceipt:
    """Last foreground action in this process; no account, resource or source contents."""

    operation: Operation
    code: str
    occurred_at: datetime | None
    diagnostic: MailAccessDiagnostic | None = None
    response_diagnostic: GmailResponseDiagnostic | None = None

    def __post_init__(self) -> None:
        if (
            type(self.operation) is not str
            or self.operation not in get_args(Operation)
            or type(self.code) is not str
            or self.code not in (_RECEIPT_ERRORS | _RECEIPT_RESULTS[self.operation])
            or (
                self.diagnostic is not None
                and mail_access_diagnostic(self.code, self.diagnostic) is None
            )
            or (
                self.response_diagnostic is not None
                and gmail_response_diagnostic(self.code, self.response_diagnostic) is None
            )
            or (
                self.occurred_at is not None
                and (
                    not isinstance(self.occurred_at, datetime)
                    or self.occurred_at.tzinfo is None
                    or self.occurred_at.utcoffset() != UTC.utcoffset(None)
                )
            )
        ):
            raise ValueError("mail_receipt_invalid")


def _clock() -> MailClock:
    return MailClock(wall=datetime.now(UTC), monotonic=time.monotonic())


def _open_browser(url: str) -> bool:
    return QDesktopServices.openUrl(QUrl(url))


def _authorization_url(url: str) -> bool:
    """Only an authorization endpoint can be opened by the system browser gesture."""

    if (
        not isinstance(url, str)
        or len(url) > 8192
        or any(not 33 <= ord(char) <= 126 for char in url)
    ):
        return False
    try:
        parts = urlsplit(url)
        return (
            parts.scheme == "https"
            and parts.netloc == "accounts.google.com"
            and parts.path == "/o/oauth2/v2/auth"
            and bool(parts.query)
            and not parts.fragment
        )
    except ValueError:
        return False


class _Worker(QThread):
    """Retain each owned thread until it actually finishes, including after cancellation."""

    def __init__(
        self,
        operation: Operation,
        generation: int,
        principal: LocalPrincipal,
        call: Callable[[Event], object],
        request: MailReadRequest | None = None,
    ) -> None:
        super().__init__()
        self.operation = operation
        self.generation = generation
        self.principal = principal
        self.cancelled = Event()
        self.call: Callable[[Event], object] | None = call
        self.request = request
        self.task_request: SourceTaskRequest | None = None
        self.search_request: MailSearchRequest | None = None
        self.result: object = None
        self.error: str | None = None
        self.diagnostic: MailAccessDiagnostic | None = None
        self.response_diagnostic: GmailResponseDiagnostic | None = None

    def run(self) -> None:
        try:
            if self.cancelled.is_set():
                raise GmailError("cancelled")
            call = self.call
            assert call is not None
            self.result = call(self.cancelled)
        except MailAccessError as error:
            self.error = error.code
            self.diagnostic = mail_access_diagnostic(error.code, error.diagnostic)
        except GmailError as error:
            self.error = error.code
            self.response_diagnostic = gmail_response_diagnostic(
                error.code, getattr(error, "response_diagnostic", None)
            )
        except Exception:
            self.error = "operation_failed"
        finally:
            self.call = None


# A panel can be destroyed while a cancelled network call is unwinding. Qt must not destroy
# its QThread in that interval. The application waits for shutdown_finished before quitting.
_WORKERS: set[_Worker] = set()


def _release_worker(worker: _Worker) -> None:
    _WORKERS.discard(worker)
    worker.result = None
    worker.request = None
    worker.search_request = None
    worker.task_request = None
    worker.diagnostic = None
    worker.response_diagnostic = None
    worker.deleteLater()


@dataclass
class _TaskFlight:
    request: SourceTaskRequest
    plan: SourceSearchPlan
    guard: Callable[[], bool]
    finished: Callable[[SourceTaskSnapshot], None]
    binding: tuple[object, ...]
    started: MailClock
    deadline: float
    worker: _Worker | None = None


class MailController(QObject):
    """One profile session, one policy owner and at most one foreground worker."""

    changed = Signal()
    shutdown_finished = Signal()

    def __init__(
        self,
        data_directory: Path,
        parent: QObject | None = None,
        *,
        connector_factory: Callable[[LocalPrincipal], GmailConnector] = GmailConnector,
        config_loader: Callable[[Path], GmailClientConfiguration] = load_client_configuration,
        browser_opener: Callable[[str], bool] = _open_browser,
        clock: Callable[[], MailClock] = _clock,
    ) -> None:
        super().__init__(parent)
        self._factory = connector_factory
        self._config_loader = config_loader
        self._browser = browser_opener
        self._clock = clock
        self._path = data_directory.expanduser().resolve(strict=False)
        self._principal = self._make_principal()
        self.audit: deque[MailAuditEvent] = deque(maxlen=256)
        self._owner = self._make_owner()
        self._connector: GmailConnector | None = None
        self._account: GmailAccount | None = None
        self._page: GmailInboxPage | None = None
        self._task_flight: _TaskFlight | None = None
        self._search_request: MailSearchRequest | None = None
        self._search_result_id: UUID | None = None
        self._search_clock: MailClock | None = None
        self._search_seen: deque[UUID] = deque(maxlen=512)
        self._job: _Worker | None = None
        self._browser_worker: _Worker | None = None
        self._cleanup: tuple[GmailConnector, bool, LocalPrincipal] | None = None
        self._generation = 0
        self._closing = False
        self._shutdown_emitted = False
        self._show_snapshot = False
        self._catalog_was_observed = False
        self._preview_was_shown = False
        self.status = "disconnected"
        self.diagnostic: MailAccessDiagnostic | None = None
        self.response_diagnostic: GmailResponseDiagnostic | None = None
        self.configuration_ready = False
        self.google_state: GoogleState = "not_checked"
        self.google_checked_at: datetime | None = None
        self.last_receipt: MailActionReceipt | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.poll)
        self._timer.start()

    def _make_principal(self) -> LocalPrincipal:
        return LocalPrincipal(
            os_uid=os.getuid(),
            profile_id=uuid5(NAMESPACE_URL, str(self._path)),
            session_id=uuid4(),
        )

    def _make_owner(self) -> MailAccessCoordinator:
        return MailAccessCoordinator(self._principal, clock=self._clock, audit=self.audit.append)

    @property
    def principal(self) -> LocalPrincipal:
        return self._principal

    @property
    def account(self) -> GmailAccount | None:
        return self._account if self._session_account() == self._account else None

    def _session_account(self) -> GmailAccount | None:
        if self._connector is None:
            return None
        try:
            return self._connector.account
        except Exception:
            return None

    @property
    def connection_state(self) -> ConnectionState:
        if self._closing:
            return "closing"
        if self.account is not None:
            return "active"
        if self._account is not None or self.status == "auth_expired":
            return "expired"
        if self._browser_worker is not None or (
            self._job is not None and self._job.operation in {"begin", "login"}
        ):
            return "connecting"
        return "disconnected"

    @property
    def connection_expires_at(self) -> datetime | None:
        connector = self._connector
        if connector is None or self.account is None:
            return None
        try:
            expires = connector.connection_expires_at
            if (
                isinstance(expires, datetime)
                and expires.tzinfo is not None
                and expires.utcoffset() == UTC.utcoffset(None)
            ):
                return expires
        except Exception:
            pass
        return None

    @property
    def source_status(self) -> SourceStatus:
        state = self.connection_state
        connection: ConnectionStatus = "connected" if state == "active" else state
        observed = self.catalog_observed if state == "active" else None
        snapshot = self.snapshot if state == "active" else None
        freshness: SnapshotStatus = "not_read"
        selected = 0
        if state == "active":
            try:
                grant = self._owner.grant
                selected = len(grant.selection.resources) if grant is not None else 0
            except MailAccessError:
                pass
            if snapshot is not None:
                observed = snapshot.observed.wall
                freshness = (
                    "unknown"
                    if not any(message.text for message in snapshot.messages)
                    else (
                        "partial"
                        if any(message.completeness != "complete" for message in snapshot.messages)
                        else "fresh"
                    )
                )
            elif self._preview_was_shown or (self._catalog_was_observed and observed is None):
                freshness = "stale"
        problem: SourceProblem = "none"
        if self.status in {"permission_denied", "scope_rejected", "login_denied"}:
            problem = "permission"
        elif (
            self.status
            in {
                "auth_expired",
                "catalog_expired",
                "snapshot_expired",
                "request_expired",
                "grant_expired",
            }
            or state == "expired"
            or freshness == "stale"
        ):
            problem = "expired"
        elif self.status == "http_unavailable":
            problem = "unavailable"
        elif self.status in {"timeout", "tls_unavailable", "http_error"}:
            problem = "transport"
        elif self.status in _RECEIPT_ERRORS:
            problem = "unknown" if self.status == "operation_failed" else "rejected"
        catalog_state = self.catalog_state
        return SourceStatus(
            source="mail",
            connection=connection,
            selected_count=selected,
            snapshot=freshness,
            observed_at=observed,
            busy=self.busy,
            problem=problem,
            catalog_state=catalog_state,
        )

    @property
    def catalog_state(self) -> CatalogStatus | None:
        if self.catalog_observed is None or self._page is None:
            return None
        if self._page.completeness == "unsupported":
            return "unsupported"
        if self._page.completeness == "partial":
            return "partial"
        return "empty" if not self._page.messages else None

    @property
    def header_omissions(self) -> tuple[GmailHeaderOmission, ...]:
        if self.catalog_observed is None or self._page is None:
            return ()
        return self._page.omissions

    @property
    def catalog_observed(self) -> datetime | None:
        # Catalog freshness belongs to the existing owner, including an empty page.
        if self.account is None:
            return None
        try:
            catalog = self._owner.catalog
        except MailAccessError:
            return None
        return catalog.observed.wall if catalog is not None else None

    @property
    def busy(self) -> bool:
        return (
            self._job is not None or self._cleanup is not None or self._browser_worker is not None
        )

    @property
    def can_cancel(self) -> bool:
        return (
            not self._closing
            and not self.remote_revoking
            and (
                self._browser_worker is not None
                or (
                    self._job is not None
                    and self._job.operation in {"begin", "login", "list", "search", "read"}
                )
            )
        )

    @property
    def shutting_down(self) -> bool:
        return self._closing

    @property
    def remote_revoking(self) -> bool:
        return (self._job is not None and self._job.operation == "revoke") or (
            self._cleanup is not None and self._cleanup[1]
        )

    @property
    def shutdown_complete(self) -> bool:
        return self._closing and not self.busy

    @property
    def headers(self) -> tuple[GmailMessageHeader, ...]:
        if self.account is None:
            self._page = None
            self._show_snapshot = False
            return ()
        try:
            if self._owner.catalog is None:
                self._page = None
        except MailAccessError:
            self._page = None
            self._show_snapshot = False
        return self._page.messages if self._page is not None else ()

    @property
    def more_available(self) -> bool:
        return bool(
            self.catalog_observed is not None
            and self._page is not None
            and self._page.more_available
        )

    @property
    def snapshot(self) -> MailSnapshot | None:
        if self.account is None:
            self._show_snapshot = False
            return None
        try:
            current = self._owner.snapshot
        except MailAccessError:
            self._show_snapshot = False
            return None
        return current if self._show_snapshot else None

    def _notify(
        self,
        status: str,
        diagnostic: MailAccessDiagnostic | None = None,
        *,
        response_diagnostic: GmailResponseDiagnostic | None = None,
    ) -> None:
        self.status = status
        self.diagnostic = mail_access_diagnostic(status, diagnostic)
        self.response_diagnostic = gmail_response_diagnostic(status, response_diagnostic)
        if (
            self.diagnostic is None
            and self.last_receipt is not None
            and self.last_receipt.diagnostic is not None
        ):
            self.last_receipt = replace(self.last_receipt, diagnostic=None)
        if (
            self.response_diagnostic is None
            and self.last_receipt is not None
            and self.last_receipt.response_diagnostic is not None
        ):
            self.last_receipt = replace(self.last_receipt, response_diagnostic=None)
        self.changed.emit()

    def _receipt_time(self) -> datetime | None:
        try:
            return MailClock.model_validate(self._clock().model_dump()).wall
        except Exception:
            return None

    def _record_receipt(
        self,
        operation: Operation,
        code: str,
        diagnostic: MailAccessDiagnostic | None = None,
        *,
        response_diagnostic: GmailResponseDiagnostic | None = None,
    ) -> None:
        allowed = _RECEIPT_ERRORS | _RECEIPT_RESULTS[operation]
        safe_code = code if type(code) is str and code in allowed else "operation_failed"
        self.last_receipt = MailActionReceipt(
            operation=operation,
            code=safe_code,
            occurred_at=None if safe_code == "clock_invalid" else self._receipt_time(),
            diagnostic=mail_access_diagnostic(safe_code, diagnostic),
            response_diagnostic=gmail_response_diagnostic(safe_code, response_diagnostic),
        )

    def _google_result(self, state: GoogleState) -> None:
        self.google_state = state
        self.google_checked_at = self._receipt_time()

    def _failure(
        self,
        operation: Operation,
        code: str,
        diagnostic: MailAccessDiagnostic | None = None,
        *,
        response_diagnostic: GmailResponseDiagnostic | None = None,
    ) -> None:
        if operation == "search":
            self._clear_source()
        if operation in {"begin", "login"} or code in {
            "auth_expired",
            "account_mismatch",
            "account_invalid",
            "not_connected",
        }:
            self._disconnect(False)
        if operation in {"list", "search", "read", "revoke"} and code in {
            "http_unavailable",
            "timeout",
            "tls_unavailable",
        }:
            self._google_result("unavailable")
        elif operation in {"login", "list", "search", "read", "revoke"} and code in {
            "http_error",
            "permission_denied",
            "response_invalid",
            "response_too_large",
        }:
            self._google_result("responded")
        self._record_receipt(operation, code, diagnostic, response_diagnostic=response_diagnostic)
        self._notify(code, diagnostic, response_diagnostic=response_diagnostic)

    def _clear_source(self) -> None:
        self._search_request = None
        self._search_result_id = None
        self._page = None
        self._show_snapshot = False
        self._catalog_was_observed = False
        self._preview_was_shown = False
        with suppress(MailAccessError):
            self._owner.revoke()

    def _cancel_worker(self) -> None:
        self._generation += 1
        if self._browser_worker is not None:
            self._browser_worker.cancelled.set()
        if self._job is not None and self._job.operation not in {"disconnect", "revoke"}:
            self._job.cancelled.set()

    def _start(
        self,
        operation: Operation,
        call: Callable[[Event], object],
        status: str,
        request: MailReadRequest | None = None,
        *,
        principal: LocalPrincipal | None = None,
        search_request: MailSearchRequest | None = None,
        task_request: SourceTaskRequest | None = None,
    ) -> None:
        assert self._job is None
        worker = _Worker(operation, self._generation, principal or self.principal, call, request)
        worker.search_request = search_request
        worker.task_request = task_request
        if operation == "task" and self._task_flight is not None:
            self._task_flight.worker = worker
        self._job = worker
        _WORKERS.add(worker)
        worker.finished.connect(self._finished)
        worker.finished.connect(lambda: _release_worker(worker))
        worker.start()
        self._notify(status)

    def configuration_path_changed(self) -> None:
        """Forget selected-file validation only; the live account is independent."""
        self.configuration_ready = False
        # A new file choice clears transient reasons, not the live account or historical action.
        self._notify(self.status)

    def connect_account(self, path: Path) -> None:
        if self._closing or self.busy:
            return
        if self._connector is not None or self._account is not None:
            self._notify("login_in_progress")
            return
        self._clear_source()
        self.configuration_ready = False
        self.google_state = "not_checked"
        self.google_checked_at = None
        self.last_receipt = None
        self._generation += 1
        try:
            connector = self._factory(self.principal)
        except Exception:
            self._record_receipt("begin", "operation_failed")
            self._notify("operation_failed")
            return
        self._connector = connector
        loader = self._config_loader

        def begin(cancel: Event) -> GmailLoginStart:
            config = loader(path)
            if cancel.is_set():
                raise GmailError("cancelled")
            return connector.begin_login(config)

        self._start("begin", begin, "connecting")

    def list_inbox(self) -> None:
        if self._closing or self.busy:
            self._notify("busy" if not self._closing else "shutting_down")
            return
        self._clear_source()
        connector = self._connector
        if connector is None or self.account is None:
            expired = self._account is not None
            self._disconnect(False)
            self._notify("auth_expired" if expired else "not_connected")
            return
        self._generation += 1
        self._start("list", lambda cancel: connector.list_inbox(cancel=cancel), "listing")

    @property
    def search_result_id(self) -> UUID | None:
        """Correlate displayed metadata with an explicitly issued search, never a read grant."""
        if self.catalog_observed is None:
            return None
        return self._search_result_id

    def _search_now(self) -> MailClock:
        try:
            now = MailClock.model_validate(self._clock().model_dump())
        except Exception:
            raise GmailError("request_invalid") from None
        if self._search_clock is not None and (
            now.wall < self._search_clock.wall or now.monotonic < self._search_clock.monotonic
        ):
            raise GmailError("request_invalid")
        self._search_clock = now
        return now

    def _search_current(self, request: MailSearchRequest) -> bool:
        now = self._search_now()
        account = self.account
        return (
            self._search_request == request
            and request.principal == self.principal
            and account is not None
            and request.account == account.account
            and request.created.wall <= now.wall < request.expires_at
            and request.created.monotonic <= now.monotonic < request.monotonic_deadline
        )

    @property
    def task_account_binding(self) -> tuple[object, ...] | None:
        account = self.account
        if self._connector is None or account is None or self._closing:
            return None
        return (self.principal, self._connector, account.account)

    def _task_current(self, flight: _TaskFlight) -> bool:
        try:
            now = self._search_now()
            task_search_admission(flight.request, flight.plan, "mail", now.wall)
            return (
                self._task_flight is flight
                and flight.binding == self.task_account_binding
                and now.wall >= flight.started.wall
                and now.monotonic >= flight.started.monotonic
                and bool(flight.guard())
                and not self._closing
            )
        except Exception:
            return False

    def start_task_search(
        self,
        request: SourceTaskRequest,
        plan: SourceSearchPlan,
        *,
        guard: Callable[[], bool],
        finished: Callable[[SourceTaskSnapshot], None],
    ) -> bool:
        """Read for the visible conversation lease, independently of source-tab visibility."""
        if self._closing or self.busy or self._task_flight is not None:
            return False
        connector, binding = self._connector, self.task_account_binding
        if connector is None or binding is None:
            return False
        try:
            request = SourceTaskRequest.model_validate(request.model_dump())
            plan = SourceSearchPlan.model_validate(plan.model_dump())
            now = self._search_now()
            task_search_admission(request, plan, "mail", now.wall)
            if request.scope.profile_id != self.principal.profile_id or not guard():
                return False
        except Exception:
            return False
        self._clear_source()
        flight = _TaskFlight(request, plan, guard, finished, binding, now, now.monotonic + 30)
        self._task_flight = flight
        self._generation += 1
        self._start(
            "task",
            lambda cancel: connector.task_search(request, plan, cancel=cancel),
            "searching",
            task_request=request,
        )
        return True

    def cancel_task_search(self, task_id: UUID) -> None:
        flight = self._task_flight
        if flight is None or flight.request.request_id != task_id:
            return
        self._task_flight = None
        if self._job is flight.worker and self._job is not None:
            self._generation += 1
            self._job.cancelled.set()
        self._notify("cancelled")

    def _finish_task_search(self, worker: _Worker, result: object) -> None:
        flight = self._task_flight
        if flight is None or flight.worker is not worker or not self._task_current(flight):
            return
        try:
            if worker.error is not None:
                raise ValueError("source_transport_error")
            if not isinstance(result, SourceTaskSnapshot):
                raise ValueError("source_result_invalid")
            snapshot = SourceTaskSnapshot.model_validate(result.model_dump())
            if (
                snapshot.request_id != flight.request.request_id
                or snapshot.scope != flight.request.scope
                or snapshot.plan_digest != flight.plan.digest
                or {item.source for item in snapshot.coverage} != {"mail"}
                or any(
                    item.query != flight.plan.mail_query
                    or item.window_start != flight.plan.window_start
                    or item.window_end != flight.plan.window_end
                    for item in snapshot.coverage
                )
                or any(
                    item.source != "mail"
                    or not flight.request.created_at <= item.observed_at <= snapshot.observed_at
                    or (
                        item.event_at is not None
                        and not flight.plan.window_start <= item.event_at < flight.plan.window_end
                    )
                    for item in snapshot.items
                )
                or not flight.request.created_at <= snapshot.observed_at < flight.request.expires_at
            ):
                raise ValueError("source_result_scope_invalid")
        except Exception:
            snapshot = task_snapshot(
                flight.request,
                flight.plan,
                "mail",
                self._clock().wall,
                (),
                status="error",
                detail="mail_task_read_failed",
            )
        if not self._task_current(flight):
            return
        self._task_flight = None
        self._record_receipt(
            "task",
            "task_partial"
            if snapshot.coverage[0].status != "searched"
            else "task_ready"
            if snapshot.items
            else "task_empty",
        )
        flight.finished(snapshot)
        self._notify(self.status)

    def search_mailbox(self, query: str, request_id: UUID) -> bool:
        """Run one user-requested Gmail search; no selection or body-read capability is issued."""
        if self._closing or self.busy or request_id in self._search_seen:
            return False
        connector, account = self._connector, self.account
        if connector is None or account is None:
            self._notify("not_connected")
            return False
        self._clear_source()
        try:
            now = self._search_now()
            request = MailSearchRequest(
                request_id=request_id,
                principal=self.principal,
                account=account.account,
                query=query,
                created=now,
                expires_at=now.wall + timedelta(seconds=30),
                monotonic_deadline=now.monotonic + 30,
            )
        except Exception:
            self._record_receipt("search", "request_invalid")
            self._notify("request_invalid")
            return False
        self._generation += 1
        self._search_seen.append(request_id)
        self._search_request = request
        self._start(
            "search",
            lambda cancel: connector.search_headers(request, cancel=cancel),
            "searching",
            search_request=request,
        )
        return True

    def cancel_search(self, request_id: UUID) -> None:
        """Retire only the matching search; never cancel a newer request, login or Read."""
        request = self._search_request
        if request is None or request.request_id != request_id:
            return
        worker = self._job
        if worker is not None and (
            worker.operation != "search" or worker.search_request != request
        ):
            # A subsequent explicit operation owns its own source authority now.
            self._search_request = None
            self._search_result_id = None
            return
        if worker is not None:
            self._generation += 1
            worker.cancelled.set()
        self._clear_source()
        self._record_receipt("search", "cancelled")
        self._notify("cancelled")

    def selection_changed(self, message_ids: tuple[str, ...]) -> None:
        if self._task_flight is not None:
            return
        # The explicit Read gesture supplies the exact new selection to the owner.
        if (
            self._closing
            or self._browser_worker is not None
            or self._cleanup is not None
            or (
                self._job is not None
                and self._job.operation in {"begin", "login", "disconnect", "revoke"}
            )
        ):
            return
        self._show_snapshot = False
        self._preview_was_shown = False
        self._cancel_worker()
        page = self._page
        try:
            if page is None:
                self._owner.revoke()
            else:
                # Same catalog and original observation time: no fetch or freshness extension.
                self._owner.admit_catalog(page.catalog)
        except MailAccessError as error:
            self._page = None
            self._notify(error.code)
            return
        if self.account is None:
            self._notify("not_connected")
        elif page is None:
            self._notify("catalog_expired")
        else:
            self._notify("select_messages" if message_ids else "selection_cleared")

    def read_selected(self, message_ids: tuple[str, ...]) -> UUID | None:
        if self._closing or self.busy:
            self._notify("busy" if not self._closing else "shutting_down")
            return None
        self._show_snapshot = False
        self._preview_was_shown = False
        connector = self._connector
        if connector is None or self.account is None:
            expired = self._account is not None
            self._disconnect(False)
            self._notify("auth_expired" if expired else "not_connected")
            return None
        try:
            selection = self._owner.select(message_ids, confirmed=True)
            self._owner.issue(selection, confirmed=True)
            request = self._owner.prepare_read()
        except MailAccessError as error:
            self._notify(error.code)
            return None
        self._generation += 1
        self._start(
            "read",
            lambda cancel: connector.read_selected(request, cancel=cancel),
            "reading",
            request,
        )
        # Metadata correlation for the initiating explicit UI gesture; not a new source grant.
        return request.request_id

    def cancel(self) -> None:
        if self._closing:
            return
        if self._task_flight is not None:
            # Hiding the legacy source panel cannot revoke the conversation lease.
            # Its owner cancels through the exact task-ID port.
            self._clear_source()
            return
        if self.remote_revoking:
            self._notify("revoking")
            return
        if self._job is not None and self._job.operation == "disconnect":
            return
        if self._browser_worker is not None or (
            self._job is not None and self._job.operation in {"begin", "login"}
        ):
            worker = self._browser_worker or self._job
            assert worker is not None
            operation = worker.operation
            self._disconnect(False)
            self._record_receipt(operation, "cancelled")
            self._notify("cancelled")
            return
        cancelled_operation = self._job.operation if self._job is not None else None
        self._cancel_worker()
        self._clear_source()
        if cancelled_operation is not None:
            self._record_receipt(cancelled_operation, "cancelled")
        self._notify("cancelled")

    def disconnect_account(self) -> None:
        revoking = self.remote_revoking
        self._disconnect(False)
        if not revoking and not self._closing:
            self._record_receipt("disconnect", "disconnected")
            self.changed.emit()

    def revoke_remote(self) -> None:
        """Host must obtain explicit project-wide revocation confirmation first."""
        if self._account is None and not self.remote_revoking:
            self._notify("remote_not_connected")
            return
        self._disconnect(True)

    def _disconnect(self, remote: bool) -> None:
        self._task_flight = None
        if self.remote_revoking:
            self._notify("shutting_down" if self._closing else "revoking")
            return
        connector = self._connector
        self._cancel_worker()
        self._clear_source()
        self._connector = None
        self._account = None
        self.google_state = "not_checked"
        self.google_checked_at = None
        if connector is not None:
            with suppress(Exception):
                connector.cancel_login()
            self._cleanup = (connector, remote, self.principal)
        self._notify("shutting_down" if self._closing else "revoking" if remote else "disconnected")
        self._drain_cleanup()

    def _drain_cleanup(self) -> None:
        if self._job is not None:
            return
        cleanup = self._cleanup
        self._cleanup = None
        if cleanup is not None:
            connector, remote, principal = cleanup
            status = self.status
            if self._closing:
                status = "shutting_down"
            elif principal == self.principal and remote:
                status = "revoking"
            self._start(
                "revoke" if remote else "disconnect",
                lambda cancel: connector.disconnect(revoke=remote),
                status,
                principal=principal,
            )
        elif self.shutdown_complete and not self._shutdown_emitted:
            self._shutdown_emitted = True
            self.shutdown_finished.emit()

    def set_profile(self, data_directory: Path) -> None:
        path = data_directory.expanduser().resolve(strict=False)
        if self._closing or path == self._path:
            return
        self._disconnect(False)
        self._path = path
        self._principal = self._make_principal()
        self._owner = self._make_owner()
        self.configuration_ready = False
        self.google_state = "not_checked"
        self.google_checked_at = None
        self.last_receipt = None
        self._notify("disconnected")

    def shutdown(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._timer.stop()
        self._disconnect(False)

    def poll(self) -> None:
        if self._closing:
            return
        flight = self._task_flight
        if flight is not None:
            if not self._task_current(flight):
                self.cancel_task_search(flight.request.request_id)
            elif self._clock().monotonic >= flight.deadline:
                callback = flight.finished
                failure = task_snapshot(
                    flight.request,
                    flight.plan,
                    "mail",
                    self._clock().wall,
                    (),
                    status="error",
                    detail="mail_task_timeout",
                )
                self.cancel_task_search(flight.request.request_id)
                if flight.guard():
                    callback(failure)
        if self._account is not None and (self._session_account() != self._account):
            self._disconnect(False)
            self._notify("auth_expired")
            return
        worker = self._job
        if (
            worker is not None
            and worker.operation == "search"
            and worker.search_request is not None
        ):
            try:
                current = self._search_current(worker.search_request)
            except GmailError:
                current = False
            if not current:
                self.cancel_search(worker.search_request.request_id)
                return
        had_headers = self._page is not None
        had_snapshot = self._show_snapshot
        headers = self.headers
        snapshot = self.snapshot
        if had_snapshot and snapshot is None:
            self._show_snapshot = False
            self._notify("snapshot_expired")
        elif had_headers and not headers and self._page is None:
            self._notify("catalog_expired")

    @Slot()
    def _finished(self) -> None:
        worker = self._job
        if worker is None or self.sender() is not worker:
            return
        self._job = None
        result = worker.result
        worker.result = None
        current = (
            not worker.cancelled.is_set()
            and (worker.generation == self._generation or worker.operation == "revoke")
            and worker.principal == self.principal
            and not self._closing
        )
        if current:
            try:
                self._accept(worker, result)
            except MailAccessError as error:
                self._show_snapshot = False
                self._failure(worker.operation, error.code, error.diagnostic)
            except GmailError as error:
                self._show_snapshot = False
                self._failure(
                    worker.operation,
                    error.code,
                    response_diagnostic=gmail_response_diagnostic(
                        error.code, getattr(error, "response_diagnostic", None)
                    ),
                )
            except Exception:
                if worker.operation == "search":
                    self._clear_source()
                self._show_snapshot = False
                self._record_receipt(worker.operation, "response_invalid")
                self._notify("response_invalid")
        self._drain_cleanup()
        self.changed.emit()

    def _accept(self, worker: _Worker, result: object) -> None:
        if worker.operation == "task":
            self._finish_task_search(worker, result)
            return
        if worker.error is not None:
            self._failure(
                worker.operation,
                worker.error,
                worker.diagnostic,
                response_diagnostic=worker.response_diagnostic,
            )
            return
        connector = self._connector
        if worker.operation in {"disconnect", "revoke"}:
            if not isinstance(result, GmailDisconnectResult):
                self._notify("operation_failed")
            elif worker.operation == "revoke" and result.remote in {"revoked", "failed", "unknown"}:
                self._record_receipt("revoke", f"disconnected_remote_{result.remote}")
                if result.remote == "revoked":
                    self._google_result("responded")
                self._notify(f"disconnected_remote_{result.remote}")
            elif worker.operation == "revoke" or self.status == "disconnected":
                self._notify("disconnected")
        elif connector is None:
            raise GmailError("not_connected")
        elif worker.operation == "begin":
            if not isinstance(result, GmailLoginStart) or not _authorization_url(
                result.authorization_url
            ):
                self._disconnect(False)
                self._record_receipt("begin", "authorization_invalid")
                self._notify("authorization_invalid")
                return
            self.configuration_ready = True
            self._browser_worker = worker
            try:
                opened = self._browser(result.authorization_url)
            except Exception:
                opened = False
            finally:
                if self._browser_worker is worker:
                    self._browser_worker = None
            if (
                worker.cancelled.is_set()
                or worker.generation != self._generation
                or worker.principal != self.principal
                or self._closing
                or self._connector is not connector
                or self._job is not None
            ):
                return
            if not opened:
                self._disconnect(False)
                self._record_receipt("begin", "browser_unavailable")
                self._notify("browser_unavailable")
                return
            attempt_id = result.attempt_id
            self._start(
                "login",
                lambda cancel: connector.finish_login(attempt_id, cancel=cancel),
                "connecting",
            )
        elif worker.operation == "login":
            if not isinstance(result, GmailAccount) or connector.account != result:
                self._disconnect(False)
                self._record_receipt("login", "account_invalid")
                self._notify("account_invalid")
                return
            self._account = result
            self._google_result("responded")
            self._record_receipt("login", "connected")
            self._notify("connected")
        elif self._account is None or connector.account != self._account:
            self._failure(worker.operation, "account_mismatch")
        elif worker.operation in {"list", "search"}:
            search_request = worker.search_request
            if worker.operation == "search":
                if (
                    not isinstance(result, GmailSearchPage)
                    or search_request is None
                    or not self._search_current(search_request)
                ):
                    self._clear_source()
                    self._failure("search", "request_invalid")
                    return
                search_page = GmailSearchPage.model_validate(result.model_dump(warnings=False))
                if search_page.request != search_request:
                    self._clear_source()
                    self._failure("search", "response_invalid")
                    return
                result = search_page.page
            # A port may bypass validation with model_construct/model_copy. Revalidate
            # coverage and omission invariants before the existing owner sees IDs.
            if not isinstance(result, GmailInboxPage):
                self._clear_source()
                self._failure(worker.operation, "response_invalid")
                return
            page = GmailInboxPage.model_validate(result.model_dump(warnings=False))
            if (
                page.catalog.account != self._account.account
                or page.catalog.principal != self.principal
                or tuple(item.source.message_id for item in page.messages)
                != page.catalog.message_ids
                or any(item.source.account != self._account.account for item in page.messages)
            ):
                self._clear_source()
                self._failure(worker.operation, "response_invalid")
                return
            self._owner.admit_catalog(page.catalog)
            self._page = page
            self._catalog_was_observed = True
            self._google_result("responded")
            if page.completeness == "unsupported":
                receipt, status = "headers_unsupported", "headers_unsupported"
            elif page.completeness == "partial":
                receipt, status = "listed_partial", "listed_partial"
            else:
                receipt = "listed" if page.messages else "empty"
                status = "select_messages" if page.messages else "empty"
            if worker.operation == "search":
                assert search_request is not None
                if not self._search_current(search_request):
                    self._clear_source()
                    self._failure("search", "request_invalid")
                    return
                self._search_result_id = search_request.request_id
                receipt, status = {
                    "headers_unsupported": (
                        "search_headers_unsupported",
                        "search_headers_unsupported",
                    ),
                    "listed_partial": ("searched_partial", "search_partial"),
                    "listed": ("searched", "search_results"),
                    "empty": ("search_empty", "search_empty"),
                }[receipt]
            self._record_receipt(worker.operation, receipt)
            self._notify(status)
        elif worker.operation == "read":
            if not isinstance(result, MailReadResult) or worker.request is None:
                raise MailAccessError("result_invalid")
            self._google_result("responded")
            snapshot = self._owner.accept_result(worker.request, result)
            self._show_snapshot = True
            self._preview_was_shown = True
            status = (
                "read_unsupported"
                if not any(message.text for message in snapshot.messages)
                else "ready_partial"
                if any(message.completeness != "complete" for message in snapshot.messages)
                else "ready"
            )
            self._record_receipt("read", status)
            self._notify(status)
