"""Explicit GPT configuration over private metadata; no key input or API calls."""

# ruff: noqa: RUF001 - intentional Russian user-facing text.

import json
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout, QWidget

from satori.desktop.openai_runtime import (
    CONFIG_NAME,
    DesktopOpenAIConfig,
    ReviewedDesktopPricing,
    inspect_existing_credential_reference,
    load_openai_runtime_config,
    load_reviewed_desktop_pricing,
    write_reviewed_openai_config,
)
from satori.desktop.settings import DesktopPreferences
from satori.server.usage import BudgetSnapshot


def _label(text: str, name: str) -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setAccessibleName(name)
    return label


def _rub(value: int) -> str:
    return f"{Decimal(value) / Decimal(1_000_000):,.2f}".replace(",", " ") + " ₽"


class OpenAISettingsPanel(QFrame):
    """One reviewed action writes references/limits, never credentials or a ledger."""

    memory_preparation_requested = Signal()

    def __init__(
        self,
        preferences: DesktopPreferences,
        *,
        configured: Callable[[Path], bool],
        use_local: Callable[[], bool],
        can_configure: Callable[[], bool],
        parent: QWidget | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        super().__init__(parent)
        self.preferences = preferences
        self._configured, self._use_local, self._can_configure = (
            configured,
            use_local,
            can_configure,
        )
        self._clock = clock
        self._runtime_supported = False
        self._preparing = False
        self.setObjectName("OpenAISettings")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 12)
        title = _label("GPT для Сатори", "Настройка GPT")
        layout.addWidget(title)
        layout.addWidget(
            _label(
                "Диалог и модельные задачи будут работать через GPT API. "
                "Для ответа модель получает нужный контекст разговора и памяти, "
                "а по твоей просьбе — текст найденных писем и сообщений.",
                "Какие данные получает GPT",
            )
        )
        self.budget = _label("", "Месячный лимит GPT API")
        layout.addWidget(self.budget)
        self.pricing = _label("", "Проверенный срок расчёта тарифов")
        layout.addWidget(self.pricing)
        self._review: ReviewedDesktopPricing | None = None
        try:
            self._review = load_reviewed_desktop_pricing()
            review = self._review
            self.budget.setText(
                "Экономичный профиль Luna · не более "
                + _rub(review.recommended_cap_micro_rub)
                + " на запросы Сатори к OpenAI за текущий месяц. "
                "Сервер, голос и другие сервисы считаются отдельно."
            )
            self.pricing.setText(
                f"Тарифы проверены {review.reviewed_at:%d.%m.%Y}; "
                f"расчёт действует до {review.expires_at:%d.%m.%Y %H:%M UTC}. "
                f"Для лимита принят запас: {review.rub_per_usd_ceiling} ₽ за доллар."
            )
        except (ValueError, OSError):
            self.pricing.setText("Проверенные тарифы сейчас недоступны.")
        self.status = _label("", "Состояние настройки GPT")
        layout.addWidget(self.status)
        self.use_saved_button = QPushButton("Использовать сохранённый ключ")
        self.use_saved_button.clicked.connect(self._configure)
        layout.addWidget(self.use_saved_button)
        layout.addWidget(
            _label(
                "Ключ хранится отдельно. Кнопка сохраняет выбор GPT; существующие "
                "ключ, месяц и лимит сохраняются. "
                "повторное нажатие и перезапуск не сбрасывают расходы месяца. "
                "При запуске приложения запросов к API и списаний нет.",
                "Как подключается GPT",
            )
        )
        self.usage = _label("", "Учёт расходов GPT API")
        layout.addWidget(self.usage)
        self.memory_notice = _label(
            "Для поиска по прежним записям через GPT может понадобиться подготовка. "
            "Текущий контекст разговора доступен сразу. "
            "По кнопке краткие записи памяти передаются OpenAI для подготовки поиска; "
            "это использует тот же лимит API.",
            "Подготовка поиска по памяти",
        )
        layout.addWidget(self.memory_notice)
        self.prepare_memory_button = QPushButton("Подготовить поиск по памяти (до 100 записей)")
        self.prepare_memory_button.clicked.connect(self._prepare_memory)
        layout.addWidget(self.prepare_memory_button)
        self.memory_status = _label("", "Результат подготовки поиска по памяти")
        layout.addWidget(self.memory_status)
        self.local_button = QPushButton("Использовать локальную модель")
        self.local_button.clicked.connect(self._select_local)
        layout.addWidget(self.local_button)
        self.set_preferences(preferences)

    def set_preferences(self, preferences: DesktopPreferences) -> None:
        self.preferences = preferences
        self._runtime_supported = False
        self._preparing = False
        self.usage.clear()
        self.memory_status.clear()
        self.status.setText(
            "Выбран GPT API. Подключение к сервису проверяется при следующем запросе."
            if preferences.runtime_provider == "openai"
            else "Сейчас используется локальная модель. Для анализа почты и Telegram подключи GPT."
        )
        self.refresh()

    def _existing_config(self) -> DesktopOpenAIConfig | None:
        path = self.preferences.data_directory / CONFIG_NAME
        if path.exists() or path.is_symlink():
            return load_openai_runtime_config(path, self.preferences.data_directory)
        return None

    def refresh(self) -> None:
        review = self._review
        if review is not None:
            try:
                existing = self._existing_config()
                cap = (
                    existing.cap_micro_rub
                    if existing is not None
                    else review.recommended_cap_micro_rub
                )
                period = (
                    existing.budget_period
                    if existing is not None
                    else self._clock().strftime("%Y-%m")
                )
                self.budget.setText(
                    f"Экономичный профиль Luna · не более {_rub(cap)} на запросы "
                    f"Сатори к OpenAI за {period}. Сервер, голос и другие сервисы "
                    "считаются отдельно. Месяц и лимит автоматически не продлеваются."
                )
            except (ValueError, OSError):
                self.budget.setText(
                    "Сохранённая настройка GPT не прошла проверку; лимит не изменён."
                )
        current = bool(
            review is not None and review.reviewed_at <= self._clock() < review.expires_at
        )
        self.use_saved_button.setEnabled(current and self._can_configure() and not self._preparing)
        self.local_button.setEnabled(self._can_configure() and not self._preparing)
        self.local_button.setVisible(self.preferences.runtime_provider == "openai")
        self.prepare_memory_button.setEnabled(
            self._runtime_supported and self._can_configure() and not self._preparing
        )
        if review is not None and not current:
            self.status.setText("Нужно проверить актуальные тарифы GPT, прежде чем продолжить.")

    def _configure(self) -> None:
        review = self._review
        if (
            review is None
            or not self._can_configure()
            or self._preparing
            or not review.reviewed_at <= self._clock() < review.expires_at
        ):
            self.refresh()
            return
        try:
            existing = self._existing_config()
            reference = (
                existing.credential
                if existing is not None
                else inspect_existing_credential_reference(self.preferences.data_directory)
            )
            if reference is None:
                self.status.setText(
                    "Сохранённый файл ключа не найден. Подключение пока не выполнено."
                )
                return
            config = DesktopOpenAIConfig(
                profile_id=uuid5(NAMESPACE_URL, str(self.preferences.data_directory)),
                model_profile="all_luna",
                credential=reference,
                budget_period=(
                    existing.budget_period
                    if existing is not None
                    else self._clock().strftime("%Y-%m")
                ),
                cap_micro_rub=(
                    existing.cap_micro_rub
                    if existing is not None
                    else review.recommended_cap_micro_rub
                ),
                rub_per_usd_ceiling=review.rub_per_usd_ceiling,
                pricing_review_id=review.review_id,
                reviewed_at=review.reviewed_at,
                expires_at=review.expires_at,
                authorized=True,
            )
            path = write_reviewed_openai_config(self.preferences.data_directory, config)
            if path != self.preferences.data_directory / CONFIG_NAME or not self._configured(path):
                raise ValueError("desktop_openai_selection_not_saved")
        except (ValueError, OSError):
            self.status.setText("Не получилось сохранить настройку GPT. Подключение не изменено.")
            return
        self.status.setText(
            "Настройка GPT сохранена. Ключ и доступ к сервису "
            "будут проверены при следующем сообщении."
        )
        self.refresh()

    def _select_local(self) -> None:
        if self._can_configure() and not self._preparing and self._use_local():
            self.status.setText("Выбрана локальная модель.")

    def _prepare_memory(self) -> None:
        if not self._runtime_supported or not self._can_configure() or self._preparing:
            return
        self._preparing = True
        self.memory_status.setText("Готовлю поиск по памяти…")
        self.refresh()
        self.memory_preparation_requested.emit()

    def set_runtime_status(self, data: dict[str, object]) -> None:
        self._runtime_supported = data.get("supports_source_answers") is True
        runtime = data.get("openai_runtime")
        if isinstance(runtime, dict):
            try:
                snapshot = BudgetSnapshot.model_validate_json(json.dumps(runtime.get("usage")))
                self.usage.setText(
                    f"Учтено и зарезервировано за {snapshot.period}: "
                    f"{_rub(snapshot.liability_micro_rub)} из {_rub(snapshot.cap_micro_rub)}. "
                    "Внутренний учёт Сатори; счёт провайдера проверяется отдельно."
                )
            except (TypeError, ValueError):
                self.usage.clear()
        else:
            self.usage.clear()
        self.refresh()

    def memory_preparation_finished(self, text: str) -> None:
        self._preparing = False
        self.memory_status.setText(text)
        self.refresh()

    def core_stopped(self) -> None:
        self._runtime_supported = False
        if self._preparing:
            self.memory_preparation_finished(
                "Результат подготовки памяти пока не подтверждён. Автоповтора нет."
            )
        self.refresh()
