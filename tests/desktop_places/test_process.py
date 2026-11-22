from pathlib import Path

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from satori.desktop.places.contracts import ProtocolError, SearchInput
from satori.desktop.places.process import QProcessPort


@pytest.fixture(scope="session")
def app() -> QApplication:
    instance = QApplication.instance() or QApplication([])
    assert isinstance(instance, QApplication)
    return instance


def test_port_is_inert_until_search(tmp_path: Path, app: QApplication) -> None:
    port = QProcessPort(tmp_path / "not-created-helper")
    assert port._process is None
    app.processEvents()
    assert port._process is None
    request = SearchInput(
        request_id="synthetic",
        session_id="synthetic",
        epoch=0,
        city="CITY",
        district="AREA",
        query="restaurant",
    )
    assert not port.start_search(request)
    assert port._process is None


@pytest.mark.parametrize("valid", [True, False])
def test_owned_fake_process_read_and_eof(tmp_path: Path, app: QApplication, valid: bool) -> None:
    helper = tmp_path / "synthetic-helper"
    payload = (
        '{"event":"protocol_error","code":"synthetic"}' if valid else '{"secret":"PRIVATE FIXTURE"}'
    )
    helper.write_text(
        "#!/bin/sh\nIFS= read -r line\nprintf '%s\\n' '" + payload + "'\n", encoding="utf-8"
    )
    helper.chmod(0o700)
    port = QProcessPort(helper)
    events: list[object] = []
    failures: list[str] = []
    port.event_received.connect(events.append)
    port.failed.connect(failures.append)
    loop = QEventLoop()
    port.stopped.connect(loop.quit)
    timeout = QTimer(loop)
    timeout.setSingleShot(True)
    timeout.timeout.connect(loop.quit)
    timeout.start(3000)
    request = SearchInput(
        request_id="synthetic",
        session_id="synthetic",
        epoch=0,
        city="CITY",
        district="AREA",
        query="restaurant",
    )
    assert port.start_search(request)
    loop.exec()
    timeout.stop()
    assert port._process is None
    if valid:
        assert events == [ProtocolError(event="protocol_error", code="synthetic")]
        assert failures == ["helper_disconnected"]
    else:
        assert events == []
        assert "source_rejected" in failures
        assert "PRIVATE FIXTURE" not in str(failures)
