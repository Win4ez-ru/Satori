"""Content-free home wiring; no accounts, providers, models or native source I/O."""

# ruff: noqa: RUF001 - intentional Russian user-facing assertions

import os
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from satori.desktop.assistant_home import AssistantHome
from satori.desktop.source_status import SourceKey, SourceStatus
from satori.desktop.ui import DesktopApplication


@pytest.fixture(scope="session")
def application() -> QApplication:
    return QApplication.instance() or DesktopApplication([])  # type: ignore[return-value]


@pytest.fixture
def home(application: QApplication) -> Iterator[AssistantHome]:
    del application
    result = AssistantHome({}, lambda _source: None, lambda: None)
    result._timer.stop()
    yield result
    result.close()
    result.deleteLater()


def test_status_rejects_source_content_and_ambiguous_times() -> None:
    with pytest.raises(ValidationError):
        SourceStatus.model_validate(
            {"source": "mail", "connection": "connected", "body": "private"}
        )
    with pytest.raises(ValidationError):
        SourceStatus(source="mail", connection="connected", observed_at=datetime(2026, 10, 29))
    with pytest.raises(ValidationError):
        SourceStatus.model_validate({"source": "mail", "connection": "invented"})


def test_construction_refresh_and_navigation_use_only_metadata(application: QApplication) -> None:
    del application
    calls: list[str] = []
    opened: list[SourceKey] = []

    def status() -> SourceStatus:
        calls.append("metadata")
        return SourceStatus(source="mail", connection="connected", selected_count=1)

    widget = AssistantHome({"mail": status}, opened.append, lambda: calls.append("conversation"))
    widget._timer.stop()
    assert calls == ["metadata"]
    widget.refresh()
    widget.open_buttons["mail"].click()
    assert calls == ["metadata", "metadata"]
    assert opened == ["mail"]
    widget.conversation_button.click()
    assert calls[-1] == "conversation"
    widget.close()
    widget.deleteLater()


def test_fresh_partial_stale_and_unknown_are_distinct(home: AssistantHome) -> None:
    value = SourceStatus(
        source="calendar", connection="connected", selected_count=5, snapshot="partial"
    )
    home._readers["calendar"] = lambda: value
    home.refresh()
    assert "часть данных" in home.status_labels["calendar"].text()
    assert "свежий снимок" not in home.status_labels["calendar"].text()
    value = value.model_copy(update={"snapshot": "stale"})
    home.refresh()
    assert "устарели" in home.status_labels["calendar"].text()
    value = value.model_copy(update={"snapshot": "unknown", "selected_count": None})
    home.refresh()
    assert "неизвестна" in home.status_labels["calendar"].text()
    assert "ещё не выбраны" not in home.status_labels["calendar"].text()


def test_bad_or_foreign_projection_does_not_report_connected(home: AssistantHome) -> None:
    home._readers["mail"] = lambda: SourceStatus(source="calendar", connection="connected")
    home.refresh()
    assert "недоступно" in home.status_labels["mail"].text()
    assert "Подключён в этой сессии" not in home.status_labels["mail"].text()


def test_old_profile_status_is_replaced_without_retained_private_fields(
    home: AssistantHome,
) -> None:
    value = SourceStatus(
        source="telegram",
        connection="connected",
        selected_count=1,
        snapshot="fresh",
        observed_at=datetime(2026, 10, 29, 12, tzinfo=UTC),
    )
    home._readers["telegram"] = lambda: value
    home.refresh()
    assert "Последнее чтение" in home.status_labels["telegram"].text()
    value = SourceStatus(source="telegram", connection="disconnected")
    home.refresh()
    assert home.status_labels["telegram"].text() == "Не подключён"
