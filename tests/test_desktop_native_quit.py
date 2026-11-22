"""Application-level Quit must drain the real owned Core before exiting Qt.

The native tray route is reproduced in a separate offscreen Qt process, using a
fresh unactivated public profile and an explicitly delayed synthetic source receipt.
No account, model generation, notification service or native helper is opened.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

import satori

PROBE = r"""
import importlib.util
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from PySide6.QtCore import QCoreApplication, QEvent, QProcess, QTimer
from PySide6.QtWidgets import QSystemTrayIcon
expected_package_root = Path(sys.argv[4]).resolve()
package_spec = importlib.util.find_spec('satori')
assert package_spec is not None and package_spec.origin is not None
assert Path(package_spec.origin).resolve() == expected_package_root/'satori/__init__.py'
from satori.desktop import ui as actual_ui
from satori.desktop.settings import DesktopPreferences
from satori.desktop.ui import DesktopApplication, DesktopWindow
assert Path(actual_ui.__file__).resolve().is_relative_to(expected_package_root/'satori')

root, work, mode = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
quit_events = []
class ObservedApplication(DesktopApplication):
    def event(self, event):
        is_quit = event.type() == QEvent.Type.Quit
        window = self.desktop_window
        before = None if window is None else window._quit_issued
        result = super().event(event)
        if is_quit:
            quit_events.append({'issued_before':before, 'accepted':event.isAccepted()})
        return result

app = ObservedApplication([])
app.setQuitOnLastWindowClosed(False)
state = {'deadline':False, 'about':[], 'worker_exits':[], 'initialized':False,
         'shutdown_ack':False, 'stop_dispatches':0, 'source_shutdowns':0,
         'source_receipt':False, 'forced_cleanup':False}
window = None
if mode != 'no-window':
    QSystemTrayIcon.isSystemTrayAvailable = staticmethod(lambda: True)
    window = DesktopWindow(DesktopPreferences(data_directory=work/'profile',
        repository_directory=root, voice_model_directory=work/'voice',
        speech_model_directory=work/'speech', speech_backend='system', speak_replies=False,
        gmail_client_configuration_path=None, elevenlabs_voice_id=''))
    app.desktop_window = window
    window.poll.stop()
    original_receive, original_stop = window._receive, window.stop
    def receive(message):
        command = window.pending.get(message.get('id'))
        if command == 'initialize' and message.get('event') == 'result':
            state['initialized'] = True
        if command == 'shutdown' and message.get('event') == 'result':
            state['shutdown_ack'] = message.get('data',{}).get('closed') is True
        return original_receive(message)
    def stop():
        state['stop_dispatches'] += 1
        return original_stop()
    window._receive, window.stop = receive, stop
    source = SimpleNamespace(shutdown_complete=False)
    def source_done():
        state['source_receipt'] = True
        source.shutdown_complete = True
        window._try_finish_quit()
    def shutdown_source():
        state['source_shutdowns'] += 1
        QTimer.singleShot(120, source_done)
    source.shutdown = shutdown_source
    window.notification_adapter = source
    window.process.finished.connect(lambda code,status: state['worker_exits'].append(
        {'code':code,'normal':status == QProcess.ExitStatus.NormalExit}))
    window.show()
    window.start()

requested = False
watch = QTimer()
def ready():
    global requested
    if requested or window is None or not state['initialized'] or window.busy:
        return
    requested = True
    watch.stop()
    if mode == 'application-quit':
        QCoreApplication.quit()
    elif mode == 'repeated-send':
        QCoreApplication.sendEvent(app, QEvent(QEvent.Type.Quit))
        QCoreApplication.sendEvent(app, QEvent(QEvent.Type.Quit))
    else:
        QCoreApplication.postEvent(app, QEvent(QEvent.Type.Quit))
watch.timeout.connect(ready)
watch.start(10)
def about():
    stopped = window is None or window.process.state() == QProcess.ProcessState.NotRunning
    state['about'].append({'worker_stopped':stopped,
        'issued':window is None or window._quit_issued,
        'source_complete':window is None or state['source_receipt']})
app.aboutToQuit.connect(about)
def deadline():
    state['deadline'] = True
    app.exit(71)
QTimer.singleShot(20000, deadline)
if mode == 'no-window':
    QTimer.singleShot(0, QCoreApplication.quit)
code = app.exec()
state['application_exit'] = code
state['quit_events'] = quit_events
# Failure cleanup owns only this public test's QProcess; it is not acceptance.
if window is not None:
    if window.process.state() != QProcess.ProcessState.NotRunning:
        window.stop()
        end = time.monotonic()+5
        while window.process.state() != QProcess.ProcessState.NotRunning and time.monotonic()<end:
            QCoreApplication.processEvents()
            time.sleep(.005)
        if window.process.state() != QProcess.ProcessState.NotRunning:
            state['forced_cleanup'] = True
            window.process.kill()
            window.process.waitForFinished(2000)
    window.tray.hide()
print(json.dumps(state), flush=True)
raise SystemExit(code)
"""


def _run_probe(tmp_path: Path, mode: str) -> dict[str, Any]:
    repository = Path(__file__).resolve().parents[1]
    assert satori.__file__ is not None
    package_root = Path(satori.__file__).resolve().parent.parent
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("SATORI_", "OPENAI_", "ELEVENLABS_", "ICLOUD_", "CALDAV_", "PYTHON"))
        and not any(
            part in key.upper() for part in ("API_KEY", "APIKEY", "ACCESS_TOKEN", "AUTH_TOKEN")
        )
    }
    environment.update(
        PYTHONPATH=str(package_root),
        PYTHONDONTWRITEBYTECODE="1",
        QT_QPA_PLATFORM="offscreen",
        SATORI_ENVIRONMENT="test",
    )
    result = subprocess.run(
        [sys.executable, "-c", PROBE, str(repository), str(tmp_path), mode, str(package_root)],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
        timeout=35,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    return cast(dict[str, Any], json.loads(result.stdout.splitlines()[-1]))


@pytest.mark.parametrize("mode", ["posted", "application-quit", "repeated-send"])
def test_application_quit_waits_for_real_core_and_async_source(tmp_path: Path, mode: str) -> None:
    state = _run_probe(tmp_path, mode)
    assert not state["deadline"]
    assert state["initialized"]
    assert state["shutdown_ack"]
    assert state["worker_exits"] == [{"code": 0, "normal": True}]
    assert state["stop_dispatches"] == state["source_shutdowns"] == 1
    assert state["source_receipt"]
    assert state["about"] == [{"worker_stopped": True, "issued": True, "source_complete": True}]
    assert not state["forced_cleanup"]
    requested = [row for row in state["quit_events"] if row["issued_before"] is False]
    assert requested
    assert all(not row["accepted"] for row in requested)
    assert any(row["issued_before"] is True and row["accepted"] for row in state["quit_events"])
    if mode == "repeated-send":
        assert len(requested) == 2


def test_application_without_window_still_honors_quit(tmp_path: Path) -> None:
    state = _run_probe(tmp_path, "no-window")
    assert not state["deadline"]
    assert state["application_exit"] == 0
    assert state["about"] == [{"worker_stopped": True, "issued": True, "source_complete": True}]
    assert state["quit_events"] == [{"issued_before": None, "accepted": True}]
