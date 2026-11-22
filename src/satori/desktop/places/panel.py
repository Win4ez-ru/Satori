"""Explicit places controls. Source text has no chat/model/history/TTS connection."""

from __future__ import annotations

import math
from pathlib import Path

from pydantic import ValidationError
from PySide6.QtCore import QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtGui import QHideEvent
from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .contracts import Event, PlacesRejected, Principal
from .controller import PlacesController
from .process import PlacesPort, QProcessPort

# ruff: noqa: RUF001 - Russian user-facing copy

_STATUSES = {
    "idle": "Укажи город, район и запрос. Поиск запустится только по кнопке.",
    "searching": "Ищу указанную область. Откроется отдельное окно Apple-карты.",
    "searching_places": "Ищу места вокруг подтверждённой точки.",
    "needs_origin_confirmation": "В отдельном окне карты выбери и подтверди точку начала поиска.",
    "complete": "Поиск завершён. Кухня, часы работы и бюджет требуют отдельной проверки.",
    "partial": "Показана часть результатов. Кухня, часы работы и бюджет неизвестны.",
    "unavailable": "Поиск недоступен или область не найдена. Автоматического повтора нет.",
    "runtime_unavailable": "Поиск мест недоступен в этой сборке.",
    "invalid_request": (
        "Заполни город, район и запрос без переводов строк. Сократи слишком длинный текст."
    ),
    "cancelled": "Поиск отменён, временные результаты убраны.",
    "query_changed": "Запрос изменился. Нажми поиск для нового запроса.",
    "timeout": "Время ожидания истекло. Новый поиск запускается вручную.",
    "expired": "Результаты устарели и убраны. При необходимости повтори поиск.",
    "map_closed": "Окно карты скрыто или закрыто. Временные результаты убраны.",
    "cleared": "Временные результаты убраны.",
    "hidden": "Панель скрыта. Поиск остановлен, результаты убраны.",
    "disconnected": "Поиск мест отключён. Подключение запускается новым поиском.",
    "profile_changed": "Профиль изменился. Запрос и результаты очищены.",
    "closed": "Поиск мест закрыт.",
    "clock_invalid": "Срок сессии нельзя проверить. Закрой и открой приложение.",
    "source_rejected": "Ответ не прошёл проверку. Карточки убраны.",
    "helper_failed": "Поиск остановлен из-за ошибки помощника.",
    "helper_disconnected": "Помощник отключился. Временные карточки убраны.",
    "helper_write_failed": "Команда не передана помощнику. Автоматического повтора нет.",
    "maps_handoff_accepted": (
        "Запрос маршрута передан приложению «Карты». Расчёт пути не подтверждён."
    ),
    "maps_handoff_failed": "Приложение «Карты» не приняло запрос маршрута.",
    "route_outcome_unknown": (
        "Передача маршрута не подтверждена. Повтор автоматически не выполняется."
    ),
}


def _label(value: str = "") -> QLabel:
    result = QLabel()
    result.setTextFormat(Qt.TextFormat.PlainText)
    result.setOpenExternalLinks(False)
    result.setWordWrap(True)
    result.setText(value)
    return result


class PlacesPanel(QWidget):
    shutdown_finished = Signal()

    def __init__(
        self,
        principal: Principal,
        helper_path: Path | None = None,
        parent: QWidget | None = None,
        *,
        controller: PlacesController | None = None,
        port: PlacesPort | None = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller if controller is not None else PlacesController(principal)
        if self.controller.principal != principal:
            raise ValueError("principal_mismatch")
        self.port = port if port is not None else QProcessPort(helper_path, self)
        self._rendered_receipt: str | None = None
        self._shutting_down = False
        self._expiry_timer = QTimer(self)
        self._expiry_timer.setSingleShot(True)
        self._expiry_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._expiry_timer.timeout.connect(self._tick)
        layout = QVBoxLayout(self)
        self.disclosure = _label(
            "Поиск передаст Apple введённые город, район, запрос и подтверждённую область. "
            "Геолокация устройства не запрашивается. Результаты временные, рядом откроется "
            "Apple-карта. Они не добавляются в разговор, память или озвучивание. "
            "Кухня, часы работы и бюджет могут быть неизвестны."
        )
        layout.addWidget(self.disclosure)
        form = QFormLayout()
        self.city_edit = QLineEdit()
        self.city_edit.setPlaceholderText("Введи город")
        self.district_edit = QLineEdit()
        self.district_edit.setPlaceholderText("Район или место поиска")
        self.query_edit = QLineEdit()
        self.query_edit.setPlaceholderText("Например: китайский ресторан")
        self.radius_spin = QSpinBox()
        self.radius_spin.setRange(100, 5000)
        self.radius_spin.setValue(2000)
        self.radius_spin.setSuffix(" м по прямой")
        form.addRow("Город", self.city_edit)
        form.addRow("Район / место", self.district_edit)
        form.addRow("Запрос", self.query_edit)
        form.addRow("Радиус", self.radius_spin)
        layout.addLayout(form)
        actions = QHBoxLayout()
        self.search_button = QPushButton("Найти на карте")
        self.cancel_button = QPushButton("Отменить")
        actions.addWidget(self.search_button)
        actions.addWidget(self.cancel_button)
        layout.addLayout(actions)
        self.status_label = _label()
        layout.addWidget(self.status_label)
        self.origin_label = _label()
        layout.addWidget(self.origin_label)
        self.results_list = QListWidget()
        self.results_list.setMinimumHeight(100)
        layout.addWidget(self.results_list, 1)
        self.details_label = _label()
        layout.addWidget(self.details_label)
        self.route_button = QPushButton("Маршрут от выбранной точки")
        layout.addWidget(self.route_button)
        self.route_disclosure = _label(
            "Кнопка передаст выбранные начало и место в приложение «Карты». "
            "Текущее положение не подставляется. Расчёт маршрута и время в пути пока неизвестны."
        )
        layout.addWidget(self.route_disclosure)
        self.search_button.clicked.connect(self._search)
        self.cancel_button.clicked.connect(self._cancel)
        self.route_button.clicked.connect(self._route)
        self.results_list.currentRowChanged.connect(self._selection)
        for edit in (self.city_edit, self.district_edit, self.query_edit):
            edit.textChanged.connect(self._edited)
        self.radius_spin.valueChanged.connect(self._edited)
        self.port.event_received.connect(self._event)
        self.port.failed.connect(self._failed)
        self.port.stopped.connect(self._stopped)
        self._sync()

    def _search(self) -> None:
        if self._shutting_down:
            return
        try:
            request = self.controller.start_search(
                self.city_edit.text(),
                self.district_edit.text(),
                self.query_edit.text(),
                radius_meters=self.radius_spin.value(),
            )
        except (ValidationError, PlacesRejected):
            self.controller.cancel("invalid_request")
            self.port.cancel()
            self._sync()
            return
        if not self.port.start_search(request):
            self.controller.cancel("runtime_unavailable")
            self.port.close()
        self._sync()

    def _cancel(self) -> None:
        self.controller.cancel("cancelled")
        self.port.cancel()
        self._sync()

    def _edited(self) -> None:
        if self.controller.request is not None:
            self.controller.cancel("query_changed")
            self.port.cancel()
            self._sync()

    def _event(self, event: Event) -> None:
        active = self.controller.request is not None
        changed = self.controller.receive(event)
        if active and self.controller.request is None:
            self.port.cancel()
        if changed or active:
            self._sync()

    def _failed(self, code: str) -> None:
        self.controller.cancel(code if code in _STATUSES else "helper_failed")
        self._sync()

    def _stopped(self) -> None:
        if self._shutting_down:
            self.shutdown_finished.emit()

    def _tick(self) -> None:
        if self.controller.tick():
            self.port.cancel()
        self._sync()

    def _selected_id(self) -> str | None:
        item = self.results_list.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole)) if item is not None else None

    def _selection(self) -> None:
        if self.controller.tick():
            self.port.cancel()
            self._sync()
            return
        self._render_selection()

    def _render_selection(self) -> None:
        place_id = self._selected_id()
        result = self.controller.output
        place = next((p for p in result.places if p.place_id == place_id), None) if result else None
        self.details_label.setText(
            f"{place.name}\n{place.address}\n"
            "Кухня: неизвестна · Сейчас открыто: неизвестно · Бюджет: неизвестен"
            if place is not None
            else ""
        )
        self.route_button.setEnabled(
            place_id is not None
            and self.controller.route_ready(place_id)
            and not self._shutting_down
        )

    def _route(self) -> None:
        place_id = self._selected_id()
        if place_id is None:
            return
        try:
            command = self.controller.begin_route(place_id)
        except PlacesRejected:
            self._sync()
            return
        if not self.port.route(command):
            # A queued command may have crossed the boundary; never retry it automatically.
            self.controller.cancel("route_outcome_unknown")
            self.port.close()
        self._sync()

    def _sync(self) -> None:
        self._expiry_timer.stop()
        if self.controller.tick():
            self.port.cancel()
        result = self.controller.output
        receipt = result.receipt_id if result is not None else None
        if receipt != self._rendered_receipt:
            self._rendered_receipt = receipt
            blocker = QSignalBlocker(self.results_list)
            self.results_list.clear()
            self.details_label.clear()
            if result is not None:
                for place in result.places:
                    item = QListWidgetItem(
                        f"{place.name}\n{place.address} · {place.distance_meters} м по прямой"
                    )
                    item.setData(Qt.ItemDataRole.UserRole, place.place_id)
                    self.results_list.addItem(item)
            del blocker
        origin = result.origin if result is not None else None
        self.origin_label.setText(
            f"Начало: {result.origin_label}\n{origin.latitude:.5f}, {origin.longitude:.5f} · "
            f"Apple MapKit · {result.observed_at.isoformat()}"
            if result is not None and origin is not None
            else ""
        )
        status = self.controller.status
        if not self.port.available and status == "idle":
            status = "runtime_unavailable"
        message = _STATUSES.get(status, _STATUSES["cleared"])
        if self.controller.pending_route is not None:
            message = "Передаю явно выбранный маршрут приложению «Карты»."
        elif self.controller.native_route_status is not None:
            message += "\nДействие в окне карты: " + _STATUSES[self.controller.native_route_status]
        self.status_label.setText(message)
        self.search_button.setEnabled(self.port.available and not self._shutting_down)
        self.cancel_button.setEnabled(
            self.controller.request is not None and not self._shutting_down
        )
        self._render_selection()
        remaining = self.controller.seconds_remaining()
        if remaining is not None and not self._shutting_down:
            self._expiry_timer.start(max(1, math.ceil(remaining * 1000)))

    def set_principal(self, principal: Principal) -> None:
        self.controller.set_principal(principal)
        self.port.close()
        for edit in (self.city_edit, self.district_edit, self.query_edit):
            blocker = QSignalBlocker(edit)
            edit.clear()
            del blocker
        self._sync()

    def conceal(self) -> None:
        self.controller.cancel("hidden")
        self.port.close()
        self._sync()

    def disconnect_source(self) -> None:
        self.controller.cancel("disconnected")
        self.port.close()
        self._sync()

    def shutdown(self) -> None:
        self._shutting_down = True
        self.controller.close()
        self._sync()
        self.port.close()

    def hideEvent(self, event: QHideEvent) -> None:
        if not self._shutting_down:
            self.conceal()
        super().hideEvent(event)
