from collections.abc import Iterator

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from satori.desktop.places.contracts import (
    Principal,
    RouteCommand,
    Searching,
    SearchInput,
    SearchResult,
)
from satori.desktop.places.controller import PlacesController
from satori.desktop.places.panel import PlacesPanel
from satori.desktop.places.process import PlacesPort, QProcessPort

from .conftest import Clock, output_for


class FakePort(PlacesPort):
    def __init__(self) -> None:
        super().__init__()
        self.requests: list[SearchInput] = []
        self.routes: list[RouteCommand] = []
        self.cancel_count = 0
        self.close_count = 0

    @property
    def available(self) -> bool:
        return True

    def start_search(self, request: SearchInput) -> bool:
        self.requests.append(request)
        return True

    def route(self, command: RouteCommand) -> bool:
        self.routes.append(command)
        return True

    def cancel(self) -> None:
        self.cancel_count += 1

    def close(self) -> None:
        self.close_count += 1
        self.stopped.emit()


@pytest.fixture(scope="session")
def app() -> QApplication:
    instance = QApplication.instance() or QApplication([])
    assert isinstance(instance, QApplication)
    return instance


@pytest.fixture
def panel(
    app: QApplication, principal: Principal, controller: PlacesController
) -> Iterator[PlacesPanel]:
    port = FakePort()
    view = PlacesPanel(principal, controller=controller, port=port)
    view.show()
    app.processEvents()
    yield view
    view.shutdown()
    view.close()
    view.deleteLater()
    app.processEvents()


def fill(panel: PlacesPanel) -> FakePort:
    panel.city_edit.setText("PUBLIC CITY")
    panel.district_edit.setText("PUBLIC AREA")
    panel.query_edit.setText("restaurant")
    assert isinstance(panel.port, FakePort)
    return panel.port


def deliver_places(port: FakePort, clock: Clock) -> SearchResult:
    request = port.requests[-1]
    port.event_received.emit(
        SearchResult(event="search_result", result=output_for(request, clock, area=True))
    )
    port.event_received.emit(
        Searching(
            event="searching",
            request_id=request.request_id,
            session_id=request.session_id,
            epoch=request.epoch,
        )
    )
    event = SearchResult(event="search_result", result=output_for(request, clock))
    port.event_received.emit(event)
    return event


def test_blank_city_and_no_startup_io(panel: PlacesPanel) -> None:
    port = panel.port
    assert isinstance(port, FakePort)
    assert panel.city_edit.text() == ""
    assert panel.district_edit.text() == ""
    assert port.requests == []
    assert port.routes == []
    assert not panel.route_button.isEnabled()
    panel.search_button.click()
    assert port.requests == []


def test_render_expiry_clears_snapshot_before_render_and_cancels_source(
    panel: PlacesPanel, clock: Clock
) -> None:
    port = fill(panel)
    panel.search_button.click()
    deliver_places(port, clock)
    panel.results_list.setCurrentRow(0)
    before = port.cancel_count
    clock.advance(60)
    panel._sync()
    assert panel.controller.output is None
    assert panel.results_list.count() == 0
    assert panel.details_label.text() == ""
    assert "устарели" in panel.status_label.text()
    assert not panel._expiry_timer.isActive()
    assert port.cancel_count == before + 1
    assert port.routes == []
    assert not panel.route_button.isEnabled()
    assert len(port.requests) == 1


def test_search_area_action_and_plain_results(panel: PlacesPanel, clock: Clock) -> None:
    port = fill(panel)
    panel.search_button.click()
    request = port.requests[-1]
    port.event_received.emit(
        SearchResult(event="search_result", result=output_for(request, clock, area=True))
    )
    assert "окне карты" in panel.status_label.text()
    assert panel.results_list.count() == 0
    assert not panel.route_button.isEnabled()
    # Native confirmation flow is covered by the pure controller corpus.
    panel._cancel()
    panel.search_button.click()
    request = port.requests[-1]
    deliver_places(port, clock)
    assert panel.results_list.count() == 1
    assert port.routes == []
    panel.results_list.setCurrentRow(0)
    assert panel.route_button.isEnabled()
    assert "<script>" in panel.details_label.text()
    assert panel.details_label.textFormat() == Qt.TextFormat.PlainText
    assert "неизвест" in panel.details_label.text()
    panel.route_button.click()
    assert len(port.routes) == 1
    assert not panel.route_button.isEnabled()


@pytest.mark.parametrize("transition", ["cancel", "hide", "edit", "profile", "disconnect"])
def test_clear_and_late_callbacks(
    panel: PlacesPanel, principal: Principal, clock: Clock, transition: str
) -> None:
    port = fill(panel)
    panel.search_button.click()
    event = deliver_places(port, clock)
    if transition == "cancel":
        panel.cancel_button.click()
    elif transition == "hide":
        panel.hide()
    elif transition == "edit":
        panel.query_edit.setText("coffee")
    elif transition == "profile":
        panel.set_principal(principal.model_copy(update={"profile_id": "other"}))
    else:
        panel.disconnect_source()
    port.event_received.emit(event)
    assert panel.results_list.count() == 0
    assert not panel.route_button.isEnabled()
    assert panel.controller.output is None
    assert port.routes == []


def test_expiry_clears_and_cancels_helper(panel: PlacesPanel, clock: Clock) -> None:
    port = fill(panel)
    panel.search_button.click()
    deliver_places(port, clock)
    clock.advance(60)
    panel._tick()
    assert panel.results_list.count() == 0
    assert port.cancel_count >= 1


def test_shutdown_signal_and_unconfigured_port(panel: PlacesPanel, principal: Principal) -> None:
    finished: list[bool] = []
    panel.shutdown_finished.connect(lambda: finished.append(True))
    panel.shutdown()
    assert finished == [True]
    assert not panel.search_button.isEnabled()
    other = PlacesPanel(principal)
    assert not other.search_button.isEnabled()
    assert isinstance(other.port, QProcessPort)
    assert other.port._process is None
    other.shutdown()
    other.deleteLater()
