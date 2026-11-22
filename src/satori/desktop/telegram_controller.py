"""A local Telegram presentation owner with an explicitly absent production port.

Root integration supplies an admitted owned transport and protected authentication.
Construction and polling perform no I/O. No r7 wire compatibility is claimed here.
"""

import math
import os
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import TypeVar
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import ValidationError
from PySide6.QtCore import QObject, QTimer, Signal

from satori.application.assistant.contracts import LocalPrincipal
from satori.desktop.telegram_contracts import (
    AUTH_TIMEOUT,
    MAX_ACCEPTED_PAGES,
    MAX_CATALOG_AGE,
    MAX_PAGE_AGE,
    MAX_SESSION_AGE,
    REQUEST_TIMEOUT,
    Operation,
    TelegramCatalog,
    TelegramChat,
    TelegramCommand,
    TelegramPage,
    TelegramPendingToken,
    TelegramRuntimeAdmission,
    TelegramSelection,
    TelegramSession,
    TelegramViewport,
    _Record,
)

Record = TypeVar("Record", bound=_Record)
Dispatcher = Callable[[TelegramCommand], None]
FAILURES = frozenset({"source_rejected", "auth_failed", "runtime_failed", "timeout"})


class TelegramController(QObject):
    """Own one transient surface; adapters must echo the original one-use token.

    configure_runtime is a trusted composition hook, never a worker/UI callback.
    It requires verified packaged bytes, local-read policy and a selected-read
    acceptance receipt. A DTO, checkbox or READY frame cannot create admission.
    The future port owns process retention and acknowledges close only on finish.
    """

    changed = Signal()
    shutdown_finished = Signal()

    def __init__(
        self,
        data_directory: Path,
        parent: QObject | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(parent)
        self._clock = clock
        self._last_clock: float | None = None
        self._principal = self._make_principal(data_directory)
        self._generation = 0
        self._admission: TelegramRuntimeAdmission | None = None
        self._dispatch: Dispatcher | None = None
        self._session: TelegramSession | None = None
        self._connection: TelegramPendingToken | None = None
        self._catalog: TelegramCatalog | None = None
        self._selection: TelegramSelection | None = None
        self._page: TelegramPage | None = None
        self._seen_pages: set[UUID] = set()
        self._pending: TelegramPendingToken | None = None
        self._pending_deadline = 0.0
        self._close_token: TelegramPendingToken | None = None
        self._owned = False
        self._terminal = False
        self._shutting_down = False
        self._shutdown_emitted = False
        self._visible = True
        self._last_viewport: TelegramViewport | None = None
        self._last_viewport_at = 0.0
        self.status = "runtime_unavailable"
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self.poll)
        self._timer.start()

    @staticmethod
    def _make_principal(path: Path) -> LocalPrincipal:
        return LocalPrincipal(
            os_uid=os.getuid(),
            profile_id=uuid5(NAMESPACE_URL, str(path.expanduser().resolve(strict=False))),
            session_id=uuid4(),
        )

    @property
    def principal(self) -> LocalPrincipal:
        return self._principal

    @property
    def busy(self) -> bool:
        return self._pending is not None or self._close_token is not None

    @property
    def connect_enabled(self) -> bool:
        return (
            self._admission is not None
            and self._dispatch is not None
            and not self._owned
            and not self.busy
            and not self._terminal
            and not self._shutting_down
        )

    @property
    def list_enabled(self) -> bool:
        return self._current_session() and not self.busy

    @property
    def select_enabled(self) -> bool:
        return self.list_enabled and self._catalog is not None

    @property
    def read_enabled(self) -> bool:
        return self.select_enabled and self._selection is not None and self._visible

    @property
    def account_label(self) -> str:
        return self._session.account_label if self._current_session() and self._session else ""

    @property
    def chats(self) -> tuple[TelegramChat, ...]:
        return self._catalog.chats if self._current_session() and self._catalog else ()

    @property
    def selected_handle(self) -> UUID | None:
        return self._selection.chat_handle if self._current_session() and self._selection else None

    @property
    def page(self) -> TelegramPage | None:
        self.poll()
        return self._page if self._visible and self._current_session() else None

    @property
    def shutdown_complete(self) -> bool:
        return not self._owned and self._close_token is None

    def configure_runtime(
        self, admission: TelegramRuntimeAdmission, dispatcher: Dispatcher
    ) -> bool:
        """Root-only hook after runtime/policy/acceptance verification, with no startup I/O."""
        if self._owned or self.busy or self._shutting_down or not callable(dispatcher):
            return False
        try:
            checked = TelegramRuntimeAdmission.model_validate(admission)
        except ValidationError:
            return False
        if checked.principal != self._principal:
            return False
        self._admission, self._dispatch = checked, dispatcher
        self._terminal = False
        self.status = "disconnected"
        self.changed.emit()
        return True

    def _now(self) -> float:
        value = self._clock()
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError("telegram_clock_invalid")
        now = float(value)
        if self._last_clock is not None and now < self._last_clock:
            raise ValueError("telegram_clock_invalid")
        self._last_clock = now
        return now

    def _current_session(self) -> bool:
        if self._session is None or self._terminal or self._close_token is not None:
            return False
        try:
            return self._now() < self._session.expires_mono
        except ValueError:
            return False

    def _token(self, operation: Operation, *, chat: UUID | None = None) -> TelegramPendingToken:
        return TelegramPendingToken(
            principal=self._principal,
            generation=self._generation,
            request_id=uuid4(),
            operation=operation,
            session_id=self._session.session_id if self._session else None,
            account_epoch=self._session.account_epoch if self._session else None,
            catalog_id=self._catalog.catalog_id if self._catalog else None,
            lease_id=self._selection.lease_id if self._selection else None,
            chat_handle=chat if chat is not None else self.selected_handle,
            page_id=self._page.page_id if self._page else None,
        )

    def _send(self, command: TelegramCommand) -> bool:
        if self._dispatch is None:
            return False
        try:
            self._dispatch(command)
            return True
        except Exception:
            self._retire("runtime_failed", terminal=True)
            return False

    def _begin(self, operation: Operation, *, chat: UUID | None = None) -> bool:
        token = self._token(operation, chat=chat)
        timeout = AUTH_TIMEOUT if operation == "connect" else REQUEST_TIMEOUT
        self._pending, self._pending_deadline = token, self._now() + timeout
        self.status = {
            "connect": "connecting",
            "catalog": "listing",
            "select": "selecting",
            "read": "reading",
        }[operation]
        self.changed.emit()
        return self._send(TelegramCommand(token, consent=True))

    def connect_account(self, consent: bool = False) -> bool:
        self.poll()
        if type(consent) is not bool or not consent or not self.connect_enabled:
            return False
        self._generation += 1
        self._owned = True
        return self._begin("connect")

    def list_chats(self) -> bool:
        self.poll()
        if not self.list_enabled:
            return False
        self._clear_source(catalog=True)
        return self._begin("catalog")

    def select_chat(self, handle: UUID) -> bool:
        self.poll()
        if not self.select_enabled or self._catalog is None:
            return False
        if not any(chat.handle == handle and chat.eligible for chat in self._catalog.chats):
            return False
        self._clear_source()
        return self._begin("select", chat=handle)

    def read_selected(self) -> bool:
        self.poll()
        if not self.read_enabled:
            return False
        self._page = None
        self._last_viewport = None
        return self._begin("read")

    def _accept(self, token: TelegramPendingToken, operation: Operation) -> bool:
        self.poll()
        return (
            self._pending is not None
            and token is self._pending
            and token.operation == operation
            and token.principal == self._principal
            and token.generation == self._generation
            and self._close_token is None
            and not self._terminal
            and not self._shutting_down
        )

    def _checked(self, model: type[Record], value: Record) -> Record | None:
        if type(value) is not model:
            self._retire("source_rejected", terminal=True)
            return None
        try:
            return model.model_validate(value)
        except ValidationError:
            self._retire("source_rejected", terminal=True)
            return None

    def _finish(self, status: str) -> None:
        self._pending = None
        self._pending_deadline = 0
        self.status = status
        self.changed.emit()

    def accept_connected(self, token: TelegramPendingToken, session: TelegramSession) -> bool:
        if not self._accept(token, "connect"):
            return False
        checked = self._checked(TelegramSession, session)
        if checked is None:
            return False
        if not self._now() < checked.expires_mono <= self._now() + MAX_SESSION_AGE:
            self._retire("source_rejected", terminal=True)
            return False
        self._session, self._connection = checked, token
        self._seen_pages.clear()
        self._finish("connected")
        return True

    def accept_catalog(self, token: TelegramPendingToken, catalog: TelegramCatalog) -> bool:
        if not self._accept(token, "catalog") or self._session is None:
            return False
        checked = self._checked(TelegramCatalog, catalog)
        if checked is None:
            return False
        if (
            not self._now()
            < checked.expires_mono
            <= min(self._session.expires_mono, self._now() + MAX_CATALOG_AGE)
        ):
            self._retire("source_rejected", terminal=True)
            return False
        self._catalog = checked
        self._finish("choose_chat")
        return True

    def accept_selection(self, token: TelegramPendingToken, selection: TelegramSelection) -> bool:
        if not self._accept(token, "select") or self._session is None or self._catalog is None:
            return False
        checked = self._checked(TelegramSelection, selection)
        if checked is None:
            return False
        if (
            checked.catalog_id != token.catalog_id
            or checked.chat_handle != token.chat_handle
            or not self._now() < checked.expires_mono <= self._session.expires_mono
        ):
            self._retire("source_rejected", terminal=True)
            return False
        self._selection = checked
        self._finish("selected")
        return True

    def accept_page(self, token: TelegramPendingToken, page: TelegramPage) -> bool:
        if not self._accept(token, "read") or self._selection is None or not self._visible:
            return False
        checked = self._checked(TelegramPage, page)
        if checked is None:
            return False
        if (
            checked.lease_id != token.lease_id
            or checked.chat_handle != token.chat_handle
            or checked.page_id in self._seen_pages
            or len(self._seen_pages) >= MAX_ACCEPTED_PAGES
            or not self._now()
            < checked.expires_mono
            <= min(self._selection.expires_mono, self._now() + MAX_PAGE_AGE)
        ):
            self._retire("source_rejected", terminal=True)
            return False
        self._page = checked
        self._seen_pages.add(checked.page_id)
        self._finish("ready")
        return True

    def accept_failure(self, token: TelegramPendingToken, code: str = "runtime_failed") -> bool:
        if self._pending is None or token is not self._pending:
            return False
        self._retire(
            code if type(code) is str and code in FAILURES else "runtime_failed", terminal=True
        )
        return True

    def invalidate_source(self, connection: TelegramPendingToken) -> bool:
        if (
            self._connection is None
            or connection is not self._connection
            or not self._current_session()
        ):
            return False
        self._clear_source(catalog=True)
        self._pending = None
        self.status = "source_changed"
        self.changed.emit()
        return True

    def fail_connection(self, connection: TelegramPendingToken, code: str) -> bool:
        if self._connection is None or connection is not self._connection:
            return False
        self._retire(
            code if type(code) is str and code in FAILURES else "runtime_failed", terminal=True
        )
        return True

    def _clear_source(self, *, catalog: bool = False) -> None:
        self._page = None
        self._selection = None
        self._last_viewport = None
        if catalog:
            self._catalog = None

    def conceal(self) -> None:
        if (
            not self._visible
            and self._page is None
            and (self._pending is None or self._pending.operation != "read")
        ):
            return
        control = self._token("conceal") if self._current_session() else None
        self._visible = False
        self._page = None
        self._last_viewport = None
        if self._pending is not None and self._pending.operation == "read":
            self._pending = None
            self.status = "selected" if self._selection else "connected"
        if control is not None and self._dispatch is not None:
            self._send(TelegramCommand(control))
        self.changed.emit()

    def set_foreground(self, visible: bool) -> None:
        if type(visible) is not bool:
            return
        if not visible:
            if self._visible:
                self.conceal()
        else:
            self._visible = True

    def report_viewport(self, viewport: TelegramViewport) -> bool:
        self.poll()
        if not self._visible or not self._current_session() or self._page is None:
            return False
        try:
            checked = TelegramViewport.model_validate(viewport)
        except ValidationError:
            self._retire("source_rejected", terminal=True)
            return False
        if checked.page_id != self._page.page_id or any(
            row.handle not in {message.handle for message in self._page.messages}
            for row in checked.rows
        ):
            return False
        now = self._now()
        if checked == self._last_viewport and now - self._last_viewport_at < 0.2:
            return False
        self._last_viewport, self._last_viewport_at = checked, now
        return self._send(TelegramCommand(self._token("viewport"), viewport=checked))

    def _retire(self, status: str, *, terminal: bool = False) -> None:
        close = self._token("close") if self._owned and self._close_token is None else None
        self._clear_source(catalog=True)
        self._session = None
        self._connection = None
        self._seen_pages.clear()
        self._pending = None
        self._generation += 1
        if not self._terminal:
            self.status = status
        self._terminal = self._terminal or terminal
        if terminal:
            self._admission = None
        if close is not None:
            self._close_token = close
            if self._dispatch is not None:
                # A failed close dispatch is an unknown transport outcome, not completion.
                with suppress(Exception):
                    self._dispatch(TelegramCommand(close))
        self.changed.emit()
        self._finish_shutdown()

    def accept_closed(self, token: TelegramPendingToken) -> bool:
        """The trusted port calls this only after the owned worker actually finishes."""
        if self._close_token is None or token is not self._close_token:
            return False
        self._close_token = None
        self._owned = False
        self.changed.emit()
        self._finish_shutdown()
        return True

    def disconnect_account(self) -> None:
        self._retire("disconnected" if self._admission else "runtime_unavailable")

    def cancel(self) -> None:
        self._retire("cancelled")

    def set_profile(self, data_directory: Path) -> None:
        self._retire("runtime_unavailable")
        self._principal = self._make_principal(data_directory)
        self._admission = None
        self._terminal = False
        self.changed.emit()

    def shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        self._timer.stop()
        self._retire("shutting_down")

    def _finish_shutdown(self) -> None:
        if self._shutting_down and self.shutdown_complete and not self._shutdown_emitted:
            self._shutdown_emitted = True
            self.shutdown_finished.emit()

    def poll(self) -> None:
        if self._shutting_down or self._terminal:
            return
        try:
            now = self._now()
        except ValueError:
            self._retire("runtime_failed", terminal=True)
            return
        if self._pending is not None and now >= self._pending_deadline:
            self._retire("timeout", terminal=True)
        elif self._session is not None and now >= self._session.expires_mono:
            self._retire("expired")
        elif self._catalog is not None and now >= self._catalog.expires_mono:
            control = self._token("conceal") if self._page is not None else None
            self._clear_source(catalog=True)
            self._pending = None
            self.status = "catalog_expired"
            if control is not None:
                self._send(TelegramCommand(control))
            self.changed.emit()
        elif self._selection is not None and now >= self._selection.expires_mono:
            control = self._token("conceal") if self._page is not None else None
            self._clear_source()
            self._pending = None
            self.status = "selection_expired"
            if control is not None:
                self._send(TelegramCommand(control))
            self.changed.emit()
        elif self._page is not None and now >= self._page.expires_mono:
            control = self._token("conceal")
            self._page = None
            self._last_viewport = None
            self.status = "page_expired"
            self._send(TelegramCommand(control))
            self.changed.emit()
