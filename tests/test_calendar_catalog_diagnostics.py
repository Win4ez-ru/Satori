"""Catalog capability/freshness metadata, with synthetic helpers only."""

import json
import os
import sys
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from satori.application.assistant.calendar import CalendarAccessCoordinator
from satori.application.assistant.contracts import (
    CalendarCatalog,
    CalendarDescriptor,
    CalendarSourceRef,
    LocalPrincipal,
)
from satori.desktop.calendar_controller import CalendarController
from satori.desktop.ui import DesktopApplication

NOW = datetime(2026, 10, 26, 12, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.value = NOW

    def __call__(self) -> datetime:
        return self.value


def descriptor(index: int, **capability: Any) -> CalendarDescriptor:
    return CalendarDescriptor(
        source=CalendarSourceRef(account_id="PUBLIC-SOURCE", calendar_id=f"PUBLIC-{index}"),
        title=f"PUBLIC CALENDAR {index}",
        account_title="PUBLIC ACCOUNT",
        **capability,
    )


@pytest.mark.parametrize(
    ("capability", "expected"),
    [
        ({}, "not_reported"),
        ({"writable": None, "allows_modification": True, "supports_events": True}, "not_reported"),
        ({"writable": False}, "read_only"),
        ({"writable": True}, "writable"),
        ({"writable": False, "allows_modification": False, "supports_events": True}, "read_only"),
        (
            {"writable": False, "allows_modification": True, "supports_events": False},
            "unsupported_entity",
        ),
    ],
)
def test_reported_missing_and_unsupported_capabilities_are_distinct(
    capability: dict[str, Any], expected: str
) -> None:
    item = descriptor(0, **capability)
    assert item.write_capability == expected
    assert (item.writable is True) == (expected == "writable")


@pytest.mark.parametrize(
    "capability",
    [
        {"writable": True, "allows_modification": False},
        {"writable": True, "supports_events": False},
        {"writable": False, "allows_modification": True, "supports_events": True},
        {"writable": 1},
        {"writable": True, "allows_modification": "yes"},
        {"supports_events": 0},
    ],
)
def test_incoherent_or_nonboolean_capability_fails_closed(capability: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        descriptor(0, **capability)


def test_catalog_expiry_retains_counts_only_and_never_authorizes_a_read() -> None:
    clock = Clock()
    principal = LocalPrincipal(os_uid=os.getuid(), profile_id=uuid4(), session_id=uuid4())
    owner = CalendarAccessCoordinator(principal, clock=clock)
    assert owner.catalog_diagnostics.state == "never_loaded"
    calendars = tuple(descriptor(index, writable=index < 5) for index in range(7))
    owner.accept_catalog(CalendarCatalog(request_id=uuid4(), observed_at=NOW, calendars=calendars))
    metadata = owner.catalog_diagnostics
    assert metadata.state == "fresh"
    assert metadata.total_count == 7
    assert metadata.writable_count == 5
    assert metadata.read_only_count == 2
    assert owner.grant is None
    clock.value += timedelta(minutes=5)
    assert owner.catalog == ()
    expired = owner.catalog_diagnostics
    assert expired.state == "stale"
    assert expired.total_count == 7
    assert expired.writable_count == 5
    assert expired.age_seconds == 300
    assert expired.observed_at == NOW
    assert "PUBLIC" not in expired.model_dump_json()
    assert owner.grant is None
    owner.revoke()
    assert owner.catalog_diagnostics.total_count == 0
    assert owner.catalog_diagnostics.observed_at is None


@pytest.fixture(scope="module")
def app() -> QApplication:
    application = QApplication.instance() or DesktopApplication([])
    assert isinstance(application, QApplication)
    return application


@pytest.fixture
def controllers() -> Iterator[list[CalendarController]]:
    values: list[CalendarController] = []
    yield values
    for owner in values:
        owner.close()
        for job in tuple(owner._retired):
            if not job.disposed:
                job.process.waitForFinished(1000)
        owner.deleteLater()
    QApplication.processEvents()


@pytest.fixture
def helper(tmp_path: Path) -> Path:
    helper = tmp_path / "public-catalog-helper.py"
    helper.write_text(
        "import json,sys,time\n"
        "r=json.loads(sys.stdin.buffer.read());mode=sys.argv[1];op=r['operation']\n"
        "o={'schema_version':1,'request_id':r['request_id'],'operation':op,'status':'ok',"
        "'access':'full_access','error':None,'observed_at':'2026-10-26T12:00:00Z'}\n"
        "if mode=='slow' and op=='status':time.sleep(30)\n"
        "if op=='list_calendars':\n"
        " o['calendars']=[]\n"
        " for i in range(7):\n"
        "  allow=i<5 if mode!='readonly' else False\n"
        "  c={'source_id':'PUBLIC-SOURCE','calendar_id':'PUBLIC-'+str(i),"
        "'title':'PUBLIC CALENDAR','source_title':'PUBLIC ACCOUNT','writable':allow,"
        "'allows_modification':allow,'supports_events':True}\n"
        "  if mode=='missing':\n"
        "   for key in ('writable','allows_modification','supports_events'):c.pop(key)\n"
        "  if mode=='incoherent':c['writable']=True;c['allows_modification']=False\n"
        "  o['calendars'].append(c)\n"
        "print(json.dumps(o))\n"
    )
    return helper


def wait(app: QApplication, condition: Any) -> None:
    deadline = time.monotonic() + 5
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    assert condition()


def catalog_state(owner: CalendarController) -> str:
    return owner.catalog_diagnostics.state


@pytest.mark.parametrize(
    ("mode", "writable", "read_only", "unreported"),
    [("fresh", 5, 2, 0), ("readonly", 0, 7, 0), ("missing", 0, 0, 7)],
)
def test_full_native_wire_to_domain_counts(
    app: QApplication,
    helper: Path,
    controllers: list[CalendarController],
    mode: str,
    writable: int,
    read_only: int,
    unreported: int,
) -> None:
    clock = Clock()
    owner = CalendarController(
        helper.parent / "PUBLIC-PROFILE",
        clock=clock,
        helper_command=lambda: (sys.executable, str(helper), mode),
    )
    controllers.append(owner)
    owner.connect_calendar()
    wait(app, lambda: not owner.busy)
    metadata = owner.catalog_diagnostics
    assert metadata.state == "fresh"
    assert metadata.total_count == 7
    assert metadata.writable_count == writable
    assert metadata.read_only_count == read_only
    assert metadata.unreported_count == unreported
    assert sum(item.writable is True for item in owner.catalog) == writable
    assert "PUBLIC" not in json.dumps(metadata.model_dump(mode="json"))
    clock.value += timedelta(minutes=5)
    owner.tick()
    assert catalog_state(owner) == "stale"
    assert owner.catalog == ()
    owner.disconnect_calendar()
    assert catalog_state(owner) == "disconnected"


@pytest.mark.parametrize("mode", ["slow", "incoherent"])
def test_catalog_loading_and_invalid_metadata_have_explicit_closed_state(
    app: QApplication, helper: Path, controllers: list[CalendarController], mode: str
) -> None:
    owner = CalendarController(
        helper.parent / "PUBLIC-PROFILE",
        clock=Clock(),
        helper_command=lambda: (sys.executable, str(helper), mode),
    )
    controllers.append(owner)
    assert catalog_state(owner) == "never_loaded"
    owner.connect_calendar()
    if mode == "slow":
        assert catalog_state(owner) == "loading"
        assert owner.catalog_diagnostics.total_count == 0
        owner.disconnect_calendar()
    else:
        wait(app, lambda: not owner.busy)
        assert catalog_state(owner) == "error"
        assert owner.catalog == ()
        assert owner.status == "invalid_response"
