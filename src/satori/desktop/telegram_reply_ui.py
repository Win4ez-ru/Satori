"""A separate exact reply review, approval, send and read-only reconcile surface."""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from satori.desktop.telegram_reply_handoff import TelegramReplyHandoff
from satori.desktop.telegram_runtime import TelegramRuntimeController

# ruff: noqa: RUF001 - Russian product text


class TelegramReplyPanel(QWidget):
    def __init__(
        self, controller: TelegramRuntimeController, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self.target: tuple[str, str, str] | None = None
        self.handoff: TelegramReplyHandoff | None = None
        self._handoff_epoch = 0
        self._handoff_seen: set[UUID] = set()
        self._handoff_guard: Callable[[], bool] | None = None
        self._handoff_binding: tuple[object, ...] | None = None
        self._handoff_last_clock: tuple[datetime, float] | None = None
        self._changing = False
        layout = QVBoxLayout(self)
        self.heading = QLabel("Ответ на выбранное сообщение")
        layout.addWidget(self.heading)
        self.target_text = QPlainTextEdit()
        self.target_text.setReadOnly(True)
        self.target_text.setMaximumHeight(90)
        self.target_text.setAccessibleName("Точное выбранное сообщение для ответа")
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("Напиши точный текст ответа")
        self.editor.setMaximumHeight(100)
        self.editor.setAccessibleName("Точный текст ответа Telegram")
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMaximumHeight(130)
        self.preview.setAccessibleName(
            "Точный адресат и текст перед отдельным разрешением отправки"
        )
        for widget in (self.target_text, self.editor, self.preview):
            layout.addWidget(widget)
        self.preview_button = QPushButton("Проверить ответ")
        self.approve_button = QPushButton("Разрешить этот ответ на 60 секунд")
        self.send_button = QPushButton("Отправить один раз")
        self.reconcile_button = QPushButton("Проверить результат без отправки")
        for buttons in (
            (self.preview_button, self.approve_button),
            (self.send_button, self.reconcile_button),
        ):
            row = QHBoxLayout()
            for button in buttons:
                row.addWidget(button)
            layout.addLayout(row)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.preview_button.clicked.connect(self._preview)
        self.approve_button.clicked.connect(self._approve)
        self.send_button.clicked.connect(self._send)
        self.reconcile_button.clicked.connect(controller.reconcile_reply)
        self.editor.textChanged.connect(self._edited)
        controller.changed.connect(self.refresh)
        self.refresh()

    def stage_text(
        self,
        request_id: UUID,
        text: str,
        conversation_id: UUID,
        *,
        context_current: Callable[[], bool],
    ) -> bool:
        """Accept only user text; a target must subsequently be selected in this panel."""
        if request_id in self._handoff_seen or len(self._handoff_seen) >= 512:
            return False
        self.retire_handoff()
        try:
            now = datetime.now(UTC)
            started = float(self.controller.clock())
            handoff = TelegramReplyHandoff(
                request_id=request_id,
                principal=self.controller.principal,
                conversation_id=conversation_id,
                epoch=self._handoff_epoch,
                text=text,
                created_at=now,
                expires_at=now + timedelta(minutes=2),
                started_monotonic=started,
                expires_monotonic=started + 120,
            )
            if not context_current():
                return False
        except (ValueError, UnicodeError, OverflowError):
            return False
        self._handoff_seen.add(request_id)
        self.clear_private_text()
        self.controller.discard_reply()
        self.handoff, self._handoff_guard = handoff, context_current
        self._handoff_last_clock = now, started
        self._changing = True
        self.editor.setPlainText(handoff.text)
        self._changing = False
        if self.editor.toPlainText() != handoff.text:
            self.retire_handoff()
            return False
        self.refresh()
        return self.handoff is not None

    def retire_handoff(self) -> None:
        if self.handoff is None:
            return
        self.handoff, self._handoff_guard, self._handoff_binding = None, None, None
        self._handoff_epoch += 1
        self._handoff_last_clock = None
        self.clear_private_text()
        self.controller.discard_reply()

    def _source_binding(self) -> tuple[object, ...] | None:
        state = self.controller.state
        now = self.controller.clock()
        if (
            state is None
            or not self.controller.live
            or not self.controller.visible
            or not state.confirmed
            or state.terminal
            or state.stale
            or not state.page_id
            or not state.selected
            or now >= min(state.session_deadline, state.selection_deadline, state.page_deadline)
        ):
            return None
        return (
            self.controller.principal,
            self.controller.generation,
            state.epoch,
            state.selected,
            state.page_id,
            state.session_deadline,
            state.selection_deadline,
            state.page_deadline,
        )

    def _handoff_current(self) -> bool:
        handoff = self.handoff
        if handoff is None:
            return False
        try:
            now = self.controller.clock()
            wall = datetime.now(UTC)
            current_context = self._handoff_guard is not None and self._handoff_guard()
        except Exception:
            return False
        valid = bool(
            handoff.principal == self.controller.principal
            and handoff.epoch == self._handoff_epoch
            and current_context
            and type(now) in (int, float)
            and math.isfinite(now)
            and handoff.started_monotonic <= now < handoff.expires_monotonic
            and handoff.created_at <= wall < handoff.expires_at
            and self._handoff_last_clock is not None
            and wall >= self._handoff_last_clock[0]
            and now >= self._handoff_last_clock[1]
            and (self._handoff_binding is None or self._handoff_binding == self._source_binding())
        )
        if valid:
            self._handoff_last_clock = wall, float(now)
        return valid

    def select_target(self, handle: str) -> None:
        state = self.controller.state
        if not self.controller.reply_ready or state is None or state.page_id is None:
            return
        message = next((row for row in state.messages if row.handle == handle), None)
        if message is None:
            return
        if self.handoff is not None and not self._handoff_current():
            self.retire_handoff()
            return
        self.controller.discard_reply()
        self.target = state.page_id, message.handle, message.text
        if self.handoff is not None:
            self._handoff_binding = self._source_binding()
        self.target_text.setPlainText(message.sender + "\n" + message.text)
        self.controller.mark_activity()
        self.refresh()

    def _edited(self) -> None:
        if self._changing:
            return
        self.controller.mark_activity()
        # A manual edit becomes a new manually authored reply, with no old handoff/approval.
        if self.handoff is not None:
            self.handoff, self._handoff_guard, self._handoff_binding = None, None, None
            self._handoff_epoch += 1
            self._handoff_last_clock = None
        self.controller.discard_reply()
        self.refresh()

    def clear_private_text(self) -> None:
        self.target = None
        self._changing = True
        self.target_text.clear()
        self.editor.clear()
        self.preview.clear()
        self._changing = False

    def _current(self) -> bool:
        state = self.controller.state
        return bool(
            self.target is not None
            and state
            and self.controller.visible
            and not state.terminal
            and state.page_id == self.target[0]
            and any((row.handle, row.text) == self.target[1:] for row in state.messages)
        )

    def _preview(self) -> None:
        if self.handoff is not None and not self._handoff_current():
            self.retire_handoff()
            return
        if self._current() and self.target is not None:
            self.controller.preview_reply(
                self.target[1],
                self.editor.toPlainText(),
                action_id=self.handoff.request_id if self.handoff is not None else None,
            )

    def _approve(self) -> None:
        if self.handoff is not None and not self._handoff_current():
            self.retire_handoff()
            return
        self.controller.approve_reply()

    def _send(self) -> None:
        if self.handoff is not None and not self._handoff_current():
            self.retire_handoff()
            return
        self.controller.send_reply()

    def refresh(self) -> None:
        if self.handoff is not None and not self._handoff_current():
            self.retire_handoff()
        self.setVisible(self.controller.reviewed_reply_enabled or self.handoff is not None)
        current = self._current()
        if self.target is not None and not current:
            self.retire_handoff()
            self.clear_private_text()
        waiting = self.handoff is not None and self.target is None
        self.heading.setText(
            "Твой текст для ответа · сначала выбери актуальное сообщение"
            if waiting
            else "Ответ на выбранное сообщение"
        )
        self.target_text.setVisible(not waiting)
        self.preview.setVisible(not waiting)
        for button in (
            self.preview_button,
            self.approve_button,
            self.send_button,
            self.reconcile_button,
        ):
            button.setVisible(not waiting)
        view = self.controller.reply_view
        ready = self.controller.reply_ready and current
        self.editor.setReadOnly(waiting)
        self.editor.setEnabled(ready or waiting)
        text = self.editor.toPlainText()
        self.preview_button.setEnabled(ready and bool(text.strip()) and len(text) <= 4000)
        unexpired = view.expires_at is not None and datetime.now(UTC) < view.expires_at
        self.approve_button.setEnabled(
            ready and view.state == "preview" and unexpired and text == view.text
        )
        self.send_button.setEnabled(
            ready and view.state == "approved" and unexpired and text == view.text
        )
        self.reconcile_button.setEnabled(
            ready and view.state == "unknown" and view.action_id is not None
        )
        if current and view.state in {"preview", "approved"}:
            self.preview.setPlainText(
                "Кому: " + view.chat_title + "\n\nТочный ответ:\n" + view.text
            )
        else:
            self.preview.clear()
        if self.handoff is not None and self.target is None:
            self.status.setText(
                "Точный текст из разговора сохранён до двух минут. "
                "Открой список, выбери личный чат, прочитай свежую страницу и нажми "
                "«Ответить на это сообщение». Затем проверь текст и отдельно разреши отправку."
            )
            return
        self.status.setText(
            {
                "idle": "Выбери сообщение и напиши ответ. Чтение не разрешает отправку.",
                "preview": "Проверь адресата, исходное сообщение и точный ответ. Отправки ещё нет.",
                "approved": "Разрешён только этот ответ. Разрешение действует не дольше 60 секунд.",
                "sending": "Одна попытка отправки. Автоматического повтора нет.",
                "sent": "Сервер Telegram принял сообщение. Доставка и прочтение не подтверждены.",
                "failed": "Telegram отклонил попытку. Автоматического повтора нет.",
                "unknown": (
                    "Результат неизвестен. Повторной отправки нет; "
                    "проверка доступна с сохранённым контекстом."
                ),
                "unavailable": "Ответ сейчас недоступен: проверь свежесть выбранной страницы.",
                "discarded": "Предыдущее разрешение снято. Подготовь новый точный ответ.",
            }.get(view.state, "Ответ недоступен.")
        )
