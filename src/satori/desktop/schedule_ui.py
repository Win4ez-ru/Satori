"""Explicit local schedule review; photos and OCR never enter the conversation route."""

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from satori.application.assistant.contracts import CalendarDescriptor, CalendarSourceRef
from satori.application.assistant.schedule import (
    draft_from_ocr,
    dump_schedule_json,
    parse_schedule_json,
)
from satori.application.assistant.schedule_contracts import (
    ReviewedSchedule,
    ScheduleError,
    ScheduleOCRObservation,
    ScheduleRow,
)
from satori.application.assistant.schedule_weekly_ocr import draft_from_weekly_ocr

if TYPE_CHECKING:
    from satori.desktop.calendar_controller import CalendarController

_MAX_FILE_BYTES = 256 * 1024
_HEADERS = ("Дата", "Начало", "Конец", "Дата конца", "Название", "Fold начала", "Fold конца")
_AMBIGUITIES = {
    "human_review_required": "Сверь каждую предложенную смену по фотографии.",
    "month_year_required": "Укажи месяц и год графика.",
    "legend_required": "Уточни смысл обозначений и нужную строку графика.",
    "ocr_limit_reached": "OCR превышает лимит; перенеси нужные смены вручную.",
    "manual_rows_required": "Введи точные смены вручную по исходнику.",
    "unrecognized_layout": "Часть строк не разобрана: сложная таблица или неясные обозначения.",
    "ambiguous_row": "Есть неоднозначная строка; уточни дату, время и дату окончания вручную.",
    "invalid_row": "Есть некорректная строка; уточни дату и точное время вручную.",
    "seven_full_date_headers_required": "Нужны все семь полных дат недели; дополни график вручную.",
    "ambiguous_week_headers": "Заголовки недели неоднозначны; проверь полные даты вручную.",
    "header_metadata_mismatch": "Месяц или год не совпадают по полным датам графика.",
    "explicit_end_date_required": "Для ночной смены укажи дату окончания явно в таблице.",
    "uncertain_date_header": "Есть неуверенно распознанная дата; уточни её по фотографии.",
    "uncertain_time_range": "Есть неуверенно распознанное время; уточни время по фотографии.",
    "ambiguous_time_column": "Неясно, к какой дате относится время; заполни эту смену вручную.",
    "ambiguous_column_geometry": "Столбцы недели расположены неоднозначно; заполни смены вручную.",
    "ambiguous_header_row": "Строка полных дат недели не определена однозначно.",
    "ambiguous_header_bounds": "Полные даты выходят за границы столбцов; проверь график вручную.",
    "time_range_not_below_header": (
        "Неясно, относится ли время к графику; проверь исходник вручную."
    ),
    "multiple_ranges_in_column": "Одна дата содержит несколько интервалов; уточни смену вручную.",
    "ambiguous_time_range": "Есть неоднозначный интервал; уточни начало и конец смены вручную.",
    "timezone_required": "Укажи действительный часовой пояс IANA.",
}
_STATUSES = {
    "idle": "Выбери фото или проверенный файл расписания. Запись ещё не разрешена.",
    "recognizing": "Распознаю выбранное изображение локально через Apple Vision.",
    "review_required": "Проверь даты, время и обозначения; неоднозначные строки заполни вручную.",
    "preview_ready": "Предпросмотр готов. Отдельно проверь дубликаты перед созданием.",
    "preflighting": "Проверяю дубликаты только в выбранном календаре и окнах смен.",
    "ready_to_create": "Проверка завершена. Создание требует отдельного подтверждения ниже.",
    "creating": "Записываю только подтверждённые смены в выбранный календарь.",
    "created": "EventKit подтвердил локальную запись. Синхронизация на iPhone ещё не проверена.",
    "committed": "EventKit подтвердил локальную запись. Синхронизация на iPhone ещё не проверена.",
    "unknown": (
        "Исход записи неизвестен. Повторное создание заблокировано; сначала сверь результат."
    ),
    "reconciling": "Сверяю исход предыдущей записи; новые события не создаются.",
    "reconciled": "Сверка завершена. Проверь показанный результат; автоматического повтора нет.",
    "invalid_preview": "Предпросмотр не прошёл проверку. Исправь данные и подготовь список заново.",
    "target_required": "Выбери точный календарь для записи из текущего списка.",
    "permission_required": "Нужен полный системный доступ и актуальный список календарей.",
    "provider_unavailable": "Календарный компонент недоступен. Создание не выполнено.",
    "cancelled": "Предпросмотр отменён. Для создания нужна новая проверка и подтверждение.",
    "ocr_ready": "OCR готов. Уточни легенду и вручную проверь каждую строку списка смен.",
    "schedule_loaded": "Файл загружен. Проверь смены и явно выбери календарь назначения.",
    "schedule_prepared": "Предпросмотр готов. Отдельно проверь дубликаты перед созданием.",
    "schedule_ready": "Дубликаты проверены. Подтверди одноразовое создание ниже.",
    "schedule_conflict": (
        "Найдены конфликтующие события. Создание заблокировано; автоматической замены нет."
    ),
    "schedule_committed": (
        "EventKit подтвердил локальный результат. Синхронизация на iPhone ещё не проверена."
    ),
    "schedule_unknown": (
        "Исход записи неизвестен. Повторное создание заблокировано; сначала сверь результат."
    ),
    "schedule_reconciled": (
        "Сверка завершена. Проверь результат; повторное создание автоматически не запускается."
    ),
    "recognize_schedule_image": "Распознаю выбранное изображение локально через Apple Vision.",
    "preflight_schedule": "Проверяю дубликаты только в выбранном календаре и окнах смен.",
    "create_schedule": "Записываю только подтверждённые смены в выбранный календарь.",
    "reconcile_schedule": "Сверяю исход предыдущей записи; новые события не создаются.",
    "image_unavailable": "Выбранный файл недоступен или превышает допустимый размер изображения.",
    "schedule_recovery_required": (
        "Есть незавершённая запись. Заново открой проверенный график и выбери тот же "
        "календарь для сверки; создание заблокировано."
    ),
    "schedule_journal_invalid": (
        "Данные предыдущей операции не прошли проверку. Создание заблокировано; "
        "нужно восстановить журнал операции."
    ),
    "schedule_journal_unavailable": (
        "Журнал операций недоступен. Создание заблокировано до восстановления доступа."
    ),
}


def _label(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


def _target_label(descriptor: CalendarDescriptor, *, ambiguous: bool = False) -> str:
    # Distinguish same-name calendars without showing or persisting native identifiers.
    source = descriptor.source
    token = hashlib.sha256(
        json.dumps([source.account_id, source.calendar_id], ensure_ascii=False).encode()
    ).hexdigest()[:8]
    title = f"{descriptor.account_title} / {descriptor.title}"
    return f"{title} · {token}" if ambiguous else title


def _writable_target(descriptor: CalendarDescriptor) -> bool:
    return descriptor.writable is True and descriptor.write_capability == "writable"


class ScheduleImportPanel(QWidget):
    """A review surface; existing read grants never enable its Create action."""

    def __init__(self, calendar: "CalendarController", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.calendar = calendar
        self.controller = calendar.schedule
        self._catalog: tuple[CalendarDescriptor, ...] = ()
        self._ocr_seen: tuple[tuple[str, float], ...] = ()
        self._ocr_partial_seen = False
        self._mutating = False
        self._prepared = False
        self._confirmed_create_available = False
        self._principal = calendar.principal
        self._calendar_status = calendar.status
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        content = QWidget()
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        self.disclosure = _label(
            "Фото и распознанный текст остаются в этой локальной форме: без модели, истории "
            "чата и озвучивания. OCR не определяет за тебя строки сложной таблицы. Укажи месяц, "
            "год, часовой пояс и смысл обозначений, затем проверь каждую смену. "
            "Разрешение на чтение календарей не разрешает запись. Создание — отдельное "
            "одноразовое действие, без гостей, уведомлений, изменения и удаления событий."
        )
        layout.addWidget(self.disclosure)
        files = QHBoxLayout()
        self.image_button = QPushButton("Выбрать фото…")
        self.json_button = QPushButton("Открыть .satori-schedule.json…")
        files.addWidget(self.image_button)
        files.addWidget(self.json_button)
        layout.addLayout(files)
        self.draft_status = _label()
        layout.addWidget(self.draft_status)
        self.ocr = QPlainTextEdit()
        self.ocr.setPlaceholderText("Здесь появится локальный OCR. Исправь ошибки перед разбором.")
        self.ocr.setAccessibleName("Локальный редактируемый текст фотографии графика")
        self.ocr.setMaximumHeight(110)
        layout.addWidget(self.ocr)
        form = QFormLayout()
        self.year = QLineEdit()
        self.year.setMaxLength(4)
        self.year.setPlaceholderText("Год графика, например 2026")
        self.month = QLineEdit()
        self.month.setMaxLength(2)
        self.month.setPlaceholderText("Месяц: 1-12")
        self.timezone = QLineEdit()
        self.timezone.setMaxLength(128)
        self.timezone.setText("Europe/Moscow")
        self.timezone.setPlaceholderText("Например Europe/Moscow")
        self.legend = QLineEdit()
        self.legend.setMaxLength(1000)
        self.legend.setPlaceholderText("Что означают буквы, цвета и часы; какую строку переносим")
        for title, widget in (
            ("Год:", self.year),
            ("Месяц:", self.month),
            ("Часовой пояс IANA:", self.timezone),
            ("Легенда и нужная строка:", self.legend),
        ):
            form.addRow(title, widget)
            widget.setAccessibleName(title.removesuffix(":"))
        layout.addLayout(form)
        self.parse_button = QPushButton("Разобрать график")
        layout.addWidget(self.parse_button)
        self.ambiguities = _label(
            "Если месяц, год или смысл таблицы неясны, уточни их вручную. "
            "Ночная смена требует явной даты окончания. Повторяющийся час при переводе "
            "часов требует Fold 0 (первый) или 1 (второй); обычно оставь Fold пустым."
        )
        layout.addWidget(self.ambiguities)
        self.rows = QTableWidget(0, len(_HEADERS))
        self.rows.setHorizontalHeaderLabels(list(_HEADERS))
        self.rows.setAccessibleName("Редактируемые точные даты и время смен")
        self.rows.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.rows.horizontalHeader().setStretchLastSection(True)
        self.rows.setMinimumHeight(150)
        layout.addWidget(self.rows)
        row_actions = QHBoxLayout()
        self.add_button = QPushButton("Добавить смену")
        self.remove_button = QPushButton("Убрать выбранные строки из списка")
        row_actions.addWidget(self.add_button)
        row_actions.addWidget(self.remove_button)
        layout.addLayout(row_actions)
        self.target = QComboBox()
        self.target.setAccessibleName("Точный существующий календарь для создания смен")
        layout.addWidget(_label("Календарь назначения (проверь аккаунт и название):"))
        self.catalog_refresh_button = QPushButton("Обновить список календарей")
        self.catalog_refresh_button.setToolTip(
            "Явно проверить доступ macOS и обновить список; строки графика останутся в форме"
        )
        layout.addWidget(self.catalog_refresh_button)
        self.catalog_status = _label()
        layout.addWidget(self.catalog_status)
        layout.addWidget(self.target)
        self.reviewed = QCheckBox(
            "Проверены каждая дата, начало, конец, часовой пояс и легенда по исходнику"
        )
        layout.addWidget(self.reviewed)
        checks = QHBoxLayout()
        self.prepare_button = QPushButton("Подготовить предпросмотр")
        self.preflight_button = QPushButton("Проверить дубликаты")
        checks.addWidget(self.prepare_button)
        checks.addWidget(self.preflight_button)
        layout.addLayout(checks)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setAccessibleName("Проверяемый предпросмотр точных смен")
        self.preview.setMaximumHeight(140)
        layout.addWidget(self.preview)
        self.confirm = QCheckBox("Подтвердить создание показанных смен в выбранном календаре")
        layout.addWidget(self.confirm)
        actions = QHBoxLayout()
        self.create_button = QPushButton("Создать показанные смены")
        self.reconcile_button = QPushButton("Сверить неизвестный исход")
        actions.addWidget(self.create_button)
        actions.addWidget(self.reconcile_button)
        layout.addLayout(actions)
        export = QHBoxLayout()
        self.export_json_button = QPushButton("Сохранить проверенный JSON…")
        self.export_ics_button = QPushButton("Экспорт ICS…")
        export.addWidget(self.export_json_button)
        export.addWidget(self.export_ics_button)
        layout.addLayout(export)
        self.status = _label()
        self.receipt = _label()
        self.sync = _label(
            "Локальное сохранение EventKit не подтверждает доставку в iCloud или на iPhone. "
            "После создания проверь те же смены в выбранном календаре на iPhone. "
            "Экспорт ICS ничего не импортирует и не синхронизирует."
        )
        for label in (self.status, self.receipt, self.sync):
            layout.addWidget(label)
        self.image_button.clicked.connect(self._choose_image)
        self.json_button.clicked.connect(self._choose_json)
        self.catalog_refresh_button.clicked.connect(self._refresh_catalog)
        self.parse_button.clicked.connect(self._parse_ocr)
        self.add_button.clicked.connect(self._add_row)
        self.remove_button.clicked.connect(self._remove_rows)
        self.prepare_button.clicked.connect(self._prepare)
        self.preflight_button.clicked.connect(self._preflight)
        self.create_button.clicked.connect(self._create)
        self.reconcile_button.clicked.connect(self.controller.reconcile)
        self.export_json_button.clicked.connect(self._export_json)
        self.export_ics_button.clicked.connect(self._export_ics)
        self.rows.itemChanged.connect(self._edited)
        self.ocr.textChanged.connect(self._edited)
        self.target.currentIndexChanged.connect(self._edited)
        for field in (self.year, self.month, self.timezone, self.legend):
            field.textChanged.connect(self._edited)
        self.reviewed.toggled.connect(self._review_changed)
        self.confirm.toggled.connect(self._render)
        self.controller.changed.connect(self._render)
        self.calendar.changed.connect(self._render)
        self._render()

    def _reset_confirmation(self) -> None:
        self._prepared = False
        self.confirm.blockSignals(True)
        self.confirm.setChecked(False)
        self.confirm.blockSignals(False)
        self.preview.clear()

    def _edited(self) -> None:
        if self._mutating:
            return
        self._reset_confirmation()
        self.reviewed.blockSignals(True)
        self.reviewed.setChecked(False)
        self.reviewed.blockSignals(False)
        self.controller.invalidate_preview()
        self._render()

    def _review_changed(self) -> None:
        if self._mutating:
            return
        self._reset_confirmation()
        self.controller.invalidate_preview()
        self._render()

    def _refresh_catalog(self) -> None:
        """One explicit catalog gesture preserves editable rows but grants no write."""
        if self.calendar.busy or self.controller.busy:
            return
        self._reset_confirmation()
        self._mutating = True
        self.target.setCurrentIndex(0)
        self._mutating = False
        self.controller.invalidate_preview()
        self.calendar.connect_calendar()

    def _catalog_message(self) -> str:
        diagnostic = self.calendar.catalog_diagnostics
        lead = {
            "never_loaded": (
                "Список календарей ещё не загружен. Нажми «Обновить список календарей»."
            ),
            "fresh": "Получен актуальный локальный список календарей.",
            "stale": (
                "Список календарей устарел. Нажми «Обновить список календарей» "
                "и заново выбери назначение."
            ),
            "loading": "Обновляю список календарей. Строки графика остаются в форме.",
            "error": ("Список календарей получить не удалось. Можно явно обновить список ещё раз."),
            "disconnected": (
                "Календарь отключён. Для выбора назначения нажми «Обновить список календарей»."
            ),
        }[diagnostic.state]
        if diagnostic.observed_at is None and diagnostic.state != "fresh":
            return lead
        counts = (
            f"Календарей: {diagnostic.total_count}; доступна запись: {diagnostic.writable_count}; "
            f"создание не разрешено: {diagnostic.read_only_count}; "
            f"возможность записи не сообщена: {diagnostic.unreported_count}; "
            f"неподдерживаемые ресурсы: {diagnostic.unsupported_count}."
        )
        age = (
            f" Возраст списка: {max(0, int(diagnostic.age_seconds))} сек."
            if diagnostic.age_seconds is not None
            else ""
        )
        observed = (
            " Последняя проверка: " + diagnostic.observed_at.isoformat(timespec="seconds") + "."
            if diagnostic.observed_at is not None
            else " Время проверки не сообщено."
        )
        detail = observed + age + " " + counts
        if diagnostic.state == "fresh":
            if diagnostic.total_count == 0:
                detail += " Календари не найдены."
            elif diagnostic.unreported_count:
                detail += (
                    " Компонент не сообщил возможность записи для части календарей. "
                    "Возможность записи пока неизвестна; запрет записи не подтверждён."
                )
            elif diagnostic.read_only_count == diagnostic.total_count:
                detail += " Создание событий не разрешено ни одним календарём в этом списке."
            if diagnostic.unsupported_count:
                detail += " Часть ресурсов не поддерживает создание событий."
        return lead + detail

    def _add_row(self) -> None:
        if self.rows.rowCount() >= 100:
            return
        row = self.rows.rowCount()
        self.rows.insertRow(row)
        for column in range(len(_HEADERS)):
            self.rows.setItem(row, column, QTableWidgetItem(""))
        self._edited()

    def _remove_rows(self) -> None:
        for row in sorted({item.row() for item in self.rows.selectedIndexes()}, reverse=True):
            self.rows.removeRow(row)
        self._edited()

    def _choose_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Выбрать фотографию графика",
            "",
            "Изображения (*.png *.jpg *.jpeg *.heic *.tif *.tiff)",
        )
        if not path:
            return
        self._mutating = True
        self.rows.setRowCount(0)
        self.ocr.clear()
        for field in (self.year, self.month, self.legend):
            field.clear()
        self._mutating = False
        self._edited()
        self.controller.recognize_image(Path(path))

    def _choose_json(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Открыть проверенный график", "", "График Сатори (*.satori-schedule.json)"
        )
        if not path:
            return
        self._edited()
        try:
            with Path(path).open("rb") as stream:
                payload = stream.read(_MAX_FILE_BYTES + 1)
            if len(payload) > _MAX_FILE_BYTES:
                raise ValueError("schedule_file_limit")
            document = parse_schedule_json(payload)
        except (OSError, ValueError):
            self.status.setText(
                "Файл не прошёл проверку. Нужен проверенный .satori-schedule.json v1."
            )
            return
        self.controller.load_reviewed(payload)
        self._show_document(document)

    def _show_document(self, document: ReviewedSchedule) -> None:
        self._mutating = True
        self.year.setText(str(document.year))
        self.month.setText(str(document.month))
        self.timezone.setText(document.timezone)
        self.legend.setText(document.legend)
        self._fill_rows(document.rows)
        self.reviewed.setChecked(False)
        self._mutating = False
        self._edited()

    def _fill_rows(self, rows: tuple[ScheduleRow, ...]) -> None:
        self.rows.setRowCount(len(rows))
        for index, row in enumerate(rows):
            values = (
                row.date.isoformat(),
                row.start.isoformat(timespec="seconds"),
                row.end.isoformat(timespec="seconds"),
                row.end_date.isoformat(),
                row.title,
                "" if row.start_fold is None else str(row.start_fold),
                "" if row.end_fold is None else str(row.end_fold),
            )
            for column, value in enumerate(values):
                self.rows.setItem(index, column, QTableWidgetItem(value))

    def _parse_ocr(self) -> None:
        try:
            text = self.ocr.toPlainText()
            original = self.controller.ocr_lines
            use_bounds = (
                bool(original)
                and text == "\n".join(line.text for line in original)
                and all(line.bounds is not None for line in original)
            )
            year = int(self.year.text()) if self.year.text() else None
            month = int(self.month.text()) if self.month.text() else None
            if use_bounds:
                observations = tuple(
                    ScheduleOCRObservation(
                        text=line.text, confidence=line.confidence, bounds=line.bounds
                    )
                    for line in original
                    if line.bounds is not None
                )
                draft = draft_from_weekly_ocr(
                    observations,
                    year=year,
                    month=month,
                    timezone=self.timezone.text(),
                    legend=self.legend.text(),
                )
            else:
                draft = draft_from_ocr(
                    text.splitlines(),
                    year=year,
                    month=month,
                    timezone=self.timezone.text(),
                    legend=self.legend.text(),
                )
        except ValueError:
            self.status.setText(
                "Уточни год, месяц, часовой пояс и легенду; строки можно ввести вручную."
            )
            return
        self._mutating = True
        if use_bounds:
            if not self.year.text() and draft.year is not None:
                self.year.setText(str(draft.year))
            if not self.month.text() and draft.month is not None:
                self.month.setText(str(draft.month))
        self._fill_rows(draft.rows)
        self._mutating = False
        self._edited()
        issues = [
            _AMBIGUITIES.get(code, "Есть неразобранные строки; проверь исходник вручную.")
            for code in draft.ambiguities
        ]
        if self.controller.ocr_partial:
            issues.append(
                "OCR получен не полностью. Проверь фотографию и добавь пропущенные смены вручную."
            )
        self.ambiguities.setText(
            "\n".join(dict.fromkeys(issues))
            or "Распознаны только явные строки. Сверь каждую смену и подтверди проверку."
        )

    def _document(self) -> ReviewedSchedule:
        values: list[dict[str, object]] = []
        for row in range(self.rows.rowCount()):
            cells = [
                cell.text() if (cell := self.rows.item(row, column)) is not None else ""
                for column in range(len(_HEADERS))
            ]
            values.append(
                dict(
                    zip(
                        ("date", "start", "end", "end_date", "title", "start_fold", "end_fold"),
                        (
                            *cells[:5],
                            int(cells[5]) if cells[5] else None,
                            int(cells[6]) if cells[6] else None,
                        ),
                        strict=True,
                    )
                )
            )
        return ReviewedSchedule.model_validate_json(
            json.dumps(
                {
                    "schema": "satori-schedule",
                    "version": 1,
                    "year": int(self.year.text()),
                    "month": int(self.month.text()),
                    "timezone": self.timezone.text(),
                    "legend": self.legend.text(),
                    "reviewed": self.reviewed.isChecked(),
                    "rows": values,
                },
                ensure_ascii=False,
            )
        )

    def _prepare(self) -> None:
        self._reset_confirmation()
        try:
            document = self._document()
        except ValueError:
            self.status.setText(
                "Проверь месяц, год, легенду, точные даты, время, IANA-зону и отметку проверки. "
                "Ночная смена требует явной следующей даты; неоднозначный час — Fold 0 или 1."
            )
            return
        target = self.target.currentData()
        if not isinstance(target, CalendarSourceRef):
            self.status.setText(_STATUSES["target_required"])
            return
        if not self.controller.prepare(document, target):
            self._render()
            return
        self._prepared = True
        zone = ZoneInfo(document.timezone)
        rows = [f"Календарь: {self.target.currentText()}", f"Часовой пояс: {document.timezone}"]
        for row in document.rows:
            start = datetime.combine(row.date, row.start, zone).replace(fold=row.start_fold or 0)
            end = datetime.combine(row.end_date, row.end, zone).replace(fold=row.end_fold or 0)
            rows.append(f"{start.isoformat()} → {end.isoformat()} · {row.title}")
        self.preview.setPlainText("\n".join(rows))
        self._render()

    def _preflight(self) -> None:
        self.confirm.setChecked(False)
        if self._prepared:
            self.controller.preflight()

    def _create(self) -> None:
        if self._prepared and self.confirm.isChecked() and self.controller.can_create:
            self.confirm.setChecked(False)
            self.controller.create()

    def _export_json(self) -> None:
        try:
            payload = dump_schedule_json(self._document())
        except ValueError:
            self.status.setText("Сначала проверь все строки и отметь проверку расписания.")
            return
        self._save(
            payload,
            "Сохранить проверенный график",
            "schedule.satori-schedule.json",
            "JSON (*.satori-schedule.json)",
        )

    def _export_ics(self) -> None:
        if not self._prepared:
            return
        try:
            payload = self.controller.export_ics()
        except (ScheduleError, ValueError):
            self.status.setText("Сначала подготовь актуальный список смен для экспорта.")
            return
        self._save(payload, "Экспортировать календарный файл", "schedule.ics", "Календарь (*.ics)")

    def _save(self, payload: bytes, title: str, name: str, file_filter: str) -> None:
        path, _ = QFileDialog.getSaveFileName(self, title, name, file_filter)
        if not path:
            return
        try:
            Path(path).write_bytes(payload)
        except OSError:
            self.status.setText("Ошибка сохранения выбранного файла.")
            return
        self.status.setText(
            "Файл сохранён по выбранному пути. Импорт и синхронизация не запускались."
        )

    def _render(self) -> None:
        if self._principal != self.calendar.principal or (
            self.calendar.status == "disconnected" and self._calendar_status != "disconnected"
        ):
            self._mutating = True
            self.rows.setRowCount(0)
            self.ocr.clear()
            for field in (self.year, self.month, self.timezone, self.legend):
                field.clear()
            self.timezone.setText("Europe/Moscow")
            self.reviewed.setChecked(False)
            self._mutating = False
            self._reset_confirmation()
        self._principal = self.calendar.principal
        self._calendar_status = self.calendar.status
        catalog = self.calendar.catalog
        if catalog != self._catalog:
            self._mutating = True
            self.target.clear()
            self.target.addItem("Выбери существующий календарь…", None)
            for descriptor in catalog:
                if _writable_target(descriptor):
                    ambiguous = (
                        sum(
                            _writable_target(item)
                            and item.title == descriptor.title
                            and item.account_title == descriptor.account_title
                            for item in catalog
                        )
                        > 1
                    )
                    self.target.addItem(
                        _target_label(descriptor, ambiguous=ambiguous), descriptor.source
                    )
            self._catalog = catalog
            self._mutating = False
            self._reset_confirmation()
        if self._prepared and self.controller.prepared_plan is None:
            self._reset_confirmation()
        lines = tuple((line.text, float(line.confidence)) for line in self.controller.ocr_lines)
        if lines != self._ocr_seen or self.controller.ocr_partial != self._ocr_partial_seen:
            self._ocr_seen = lines
            self._ocr_partial_seen = self.controller.ocr_partial
            self._mutating = True
            self.ocr.setPlainText("\n".join(text for text, _ in lines))
            self._mutating = False
            self._reset_confirmation()
            if self.controller.ocr_partial:
                self.ambiguities.setText(
                    "OCR получен не полностью. Проверь фотографию "
                    "и добавь пропущенные смены вручную."
                )
            elif any(confidence < 0.9 for _, confidence in lines):
                self.ambiguities.setText(
                    "OCR содержит неуверенные строки. Сверь их по фотографии; "
                    "даты и время не додумываются."
                )
        busy = self.controller.busy or self.calendar.busy
        self.catalog_refresh_button.setEnabled(not busy)
        self.catalog_status.setText(self._catalog_message())
        unknown = self.controller.state == "unknown"
        can_create = self._prepared and self.controller.can_create and not busy and not unknown
        if can_create and not self._confirmed_create_available:
            self.confirm.blockSignals(True)
            self.confirm.setChecked(False)
            self.confirm.blockSignals(False)
        self._confirmed_create_available = can_create
        self.confirm.setEnabled(can_create)
        if self._prepared:
            self.confirm.setText(
                f"Создать {self.rows.rowCount()} показанных смен: {self.target.currentText()} "
                f"({self.timezone.text()}) — один раз"
            )
        self.create_button.setEnabled(can_create and self.confirm.isChecked())
        self.reconcile_button.setEnabled(self.controller.can_reconcile and not busy)
        self.preflight_button.setEnabled(self._prepared and not busy and not unknown)
        self.export_ics_button.setEnabled(self._prepared and not busy)
        self.prepare_button.setEnabled(not busy and not unknown)
        for widget in (
            self.image_button,
            self.json_button,
            self.parse_button,
            self.add_button,
            self.remove_button,
            self.rows,
            self.target,
            self.reviewed,
            self.year,
            self.month,
            self.timezone,
            self.legend,
            self.ocr,
            self.export_json_button,
        ):
            widget.setEnabled(not busy and not unknown)
        self.add_button.setEnabled(self.add_button.isEnabled() and self.rows.rowCount() < 100)
        row_count = self.rows.rowCount()
        self.draft_status.setText(
            f"Строк смен в форме: {row_count}. Даты и время доступны для проверки."
            if row_count
            else "Строк смен в форме: 0. Загрузи график или добавь точные строки вручную."
        )
        status = _STATUSES.get(
            self.controller.status,
            "Операция не завершена. Проверь список смен и состояние календаря; "
            "автоматического повтора нет.",
        )
        if self.controller.status == "idle":
            if row_count:
                status = (
                    f"Строк смен в форме: {row_count}. Проверь данные и выбери календарь "
                    "из актуального списка; создание ещё не разрешено."
                )
            elif self.ocr.toPlainText():
                status = "Текст находится в форме. Разбери график или введи точные смены вручную."
        self.status.setText(status)
        receipt = self.controller.receipt
        if receipt:
            outcomes = receipt.get("outcomes")
            labels = {
                "absent": "не найдены",
                "owned_exact": "уже присутствуют",
                "conflict": "конфликтуют",
                "created": "созданы",
                "unknown": "неизвестный исход",
            }
            counts = (
                [
                    f"{title}: {outcomes[name]}"
                    for name, title in labels.items()
                    if type(outcomes.get(name)) is int
                ]
                if isinstance(outcomes, dict)
                else [
                    f"{key}: {value}"
                    for key, value in receipt.items()
                    if isinstance(value, (int, bool))
                ]
            )
            self.receipt.setText("Результат локальной операции: " + "; ".join(counts))
        else:
            self.receipt.clear()
