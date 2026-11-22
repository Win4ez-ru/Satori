"""Automatic metadata refresh cannot enter the explicit OS-permission prompt path."""

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from satori.desktop.calendar_controller import CalendarController
from tests import test_calendar_chat_window as window_fixtures

application = window_fixtures.application


@pytest.mark.parametrize("access", ["full_access", "not_determined"])
def test_foreground_catalog_refresh_uses_listing_without_requesting_os_access(
    application: QApplication,
    tmp_path: Path,
    access: str,
) -> None:
    program = tmp_path / "public-calendar-catalog.py"
    log = tmp_path / "public-calendar-operations.json"
    program.write_text(
        "import json,sys\nfrom pathlib import Path\n"
        "r=json.loads(sys.stdin.buffer.read())\n"
        "Path(sys.argv[1]).write_text(json.dumps([r['operation']]))\n"
        "print(json.dumps({'schema_version':1,'request_id':r['request_id'],"
        "'operation':r['operation'],'status':'ok','access':sys.argv[2],"
        "'error':None,'observed_at':'2026-11-05T12:00:00Z','calendars':[]}))\n"
    )
    calendar = CalendarController(
        tmp_path,
        clock=lambda: datetime(2026, 11, 5, 12, tzinfo=UTC),
        helper_command=lambda: (sys.executable, "-B", str(program), str(log), access),
    )
    try:
        calendar.refresh_catalog()
        deadline = time.monotonic() + 5
        while calendar.busy and time.monotonic() < deadline:
            application.processEvents()
            time.sleep(0.005)
        assert not calendar.busy
        assert json.loads(log.read_text()) == ["list_calendars"]
        assert calendar.permission_status == access
        assert calendar.grant is None
    finally:
        calendar.close()
        calendar.deleteLater()
        application.processEvents()
