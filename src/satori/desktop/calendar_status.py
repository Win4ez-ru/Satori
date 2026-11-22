"""Content-free Calendar status for local home/assistant surfaces; no capabilities."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

CalendarConnection = Literal[
    "disconnected",
    "connecting",
    "permission_required",
    "permission_denied",
    "unavailable",
    "needs_selection",
    "session_expired",
    "connected",
    "error",
]
CalendarFreshness = Literal["not_read", "fresh", "stale"]
CalendarOutcome = Literal["none", "ok", "empty", "partial"]


@dataclass(frozen=True, slots=True)
class CalendarStatusProjection:
    """Display metadata only. Reading this object never connects or reads a source.

    Counts describe the active session selection, not remembered preferences. OS access
    is the last observed permission, not a fresh OS check. Local freshness does not
    establish remote calendar synchronization or the user's personal availability.
    """

    connection: CalendarConnection
    permission: Literal[
        "not_determined", "restricted", "denied", "write_only", "full_access", "unknown"
    ]
    catalog_state: Literal["never_loaded", "fresh", "stale", "loading", "error", "disconnected"]
    selected_count: int
    freshness: CalendarFreshness
    outcome: CalendarOutcome
    busy: bool
    reading: bool
    can_connect: bool
    can_refresh: bool
    can_query_dialogue: bool
    last_successful_read_at: datetime | None
    observed_at: datetime | None
    fresh_until: datetime | None
    age_seconds: float | None
    status_text: str


def calendar_status_text(
    connection: CalendarConnection,
    freshness: CalendarFreshness,
    outcome: CalendarOutcome,
    *,
    selected_count: int,
    busy: bool,
    reading: bool,
) -> str:
    """Closed local wording, with no names, titles, identifiers or raw error strings."""

    states: dict[CalendarConnection, str] = {
        "disconnected": "Календарь не подключён. Чтение не выполняется.",
        "connecting": "Календарь: проверяю доступ и список. События ещё не читаются.",
        "permission_required": "Календарь: требуется явное разрешение macOS на чтение.",
        "permission_denied": "Календарь: macOS не разрешает чтение. События не проверены.",
        "unavailable": "Календарный источник недоступен. События не проверены.",
        "needs_selection": "Календарь: выбери календари и разреши чтение в этой сессии.",
        "session_expired": "Календарь: разрешение сессии истекло. Выбери календари заново.",
        "error": "Календарный запрос не выполнен. Отсутствие событий не установлено.",
        "connected": "",
    }
    if connection != "connected":
        return states[connection]
    prefix = f"Календарь: разрешено для чтения — {selected_count}. "
    if reading:
        return prefix + "Обновляю локальный снимок."
    if busy:
        return prefix + "Выполняется календарная операция."
    if freshness == "stale":
        return prefix + "Срок локального снимка истёк; обнови снимок явно."
    if freshness != "fresh":
        return prefix + "Свежего снимка нет; выбери день и запусти чтение явно."
    if outcome == "partial":
        return prefix + "Локальный ответ неполный; отсутствие событий не установлено."
    if outcome == "empty":
        return prefix + "Выбранное окно не содержит событий; локальный снимок свежий."
    return prefix + "Локальный снимок свежий."
