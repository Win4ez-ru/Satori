"""Synthetic existing-account controller ports; no network, browser or real SDK."""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, get_ident
from typing import Any
from uuid import UUID

import pytest
from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication

from satori.application.assistant.source_answer_contracts import SourceTaskSnapshot
from satori.application.assistant.source_task_reads import task_snapshot
from satori.desktop import package
from satori.desktop.telegram_port import WireState, schema_from
from satori.desktop.telegram_reply_wire import reply_qualification_for
from satori.desktop.telegram_runtime import TelegramRuntimeController
from satori.desktop.telegram_task_protocol import TASK_PROTOCOL_PATH, TASK_PROTOCOL_SHA256
from tests.test_desktop_package import repository as repository
from tests.test_desktop_package import telegram_sdk as telegram_sdk
from tests.test_mail_controller import Harness, wait_until
from tests.test_mail_controller import app as app
from tests.test_mail_controller import harness as harness
from tests.test_source_task_adapters import RESOURCES, task
from tests.test_telegram_reply_ui_v1 import NoChildProcess


def test_mail_task_background_result_no_selection_and_source_panel_cancel_is_independent(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = harness
    h.connect()
    request, plan = task(h.controller.principal, h.clock.wall)
    main = get_ident()
    guards: list[int] = []
    calls: list[int] = []
    output: list[SourceTaskSnapshot] = []
    gate, started = Event(), Event()

    def run(*_args: Any, cancel: Event) -> SourceTaskSnapshot:
        del cancel
        calls.append(get_ident())
        started.set()
        assert gate.wait(3)
        return task_snapshot(request, plan, "mail", h.clock.wall, (), status="searched")

    def guard() -> bool:
        guards.append(get_ident())
        return True

    monkeypatch.setattr(h.connector, "task_search", run)
    binding = h.controller.task_account_binding
    try:
        assert h.controller.start_task_search(request, plan, guard=guard, finished=output.append)
        assert started.wait(2)
        h.controller.cancel()  # legacy hidden-panel cleanup is not this task's authority
        h.controller.selection_changed(())
        assert h.controller.task_account_binding == binding
        gate.set()
        wait_until(h.app, lambda: not h.controller.busy)
        assert len(output) == 1
        assert output[0].coverage[0].status == "searched"
        assert calls
        assert all(ident != main for ident in calls)
        assert guards
        assert all(ident == main for ident in guards)
        assert h.controller.headers == ()
        assert h.controller.snapshot is None
        assert h.connector.reads == []
    finally:
        gate.set()


@pytest.mark.parametrize("change", ["guard", "timeout", "cancel", "account"])
def test_mail_task_cancellation_or_timeout_drops_late_delivery_without_other_account_actions(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    h = harness
    h.connect()
    request, plan = task(h.controller.principal, h.clock.wall)
    gate, started = Event(), Event()
    output: list[SourceTaskSnapshot] = []
    allowed = True

    def run(*_args: Any, cancel: Event) -> SourceTaskSnapshot:
        del cancel  # adversarial late delivery must still be fenced by the controller
        started.set()
        assert gate.wait(3)
        return task_snapshot(request, plan, "mail", h.clock.wall, (), status="searched")

    monkeypatch.setattr(h.connector, "task_search", run)
    try:
        assert h.controller.start_task_search(
            request, plan, guard=lambda: allowed, finished=output.append
        )
        assert started.wait(2)
        if change == "guard":
            allowed = False
            h.controller.poll()
        elif change == "timeout":
            h.clock.advance(31)
            h.controller.poll()
        elif change == "account":
            h.connector.fake_account = None
            h.controller.poll()
        else:
            h.controller.cancel_task_search(request.request_id)
        gate.set()
        wait_until(h.app, lambda: not h.controller.busy)
        if change == "timeout":
            assert len(output) == 1
            assert output[0].coverage[0].status == "error"
            assert output[0].coverage[0].detail == "mail_task_timeout"
        else:
            assert output == []
        if change != "account":
            assert h.controller.account is not None
            assert h.connector.disconnects == []
    finally:
        gate.set()


def test_mail_stale_task_cancel_cannot_cancel_a_new_task(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = harness
    h.connect()
    first, plan = task(h.controller.principal, h.clock.wall)
    second, _ = task(h.controller.principal, h.clock.wall)
    output: list[SourceTaskSnapshot] = []

    def run(request: Any, actual_plan: Any, *, cancel: Event) -> SourceTaskSnapshot:
        assert not cancel.is_set()
        return task_snapshot(request, actual_plan, "mail", h.clock.wall, (), status="searched")

    monkeypatch.setattr(h.connector, "task_search", run)
    assert h.controller.start_task_search(first, plan, guard=lambda: True, finished=output.append)
    wait_until(h.app, lambda: not h.controller.busy)
    assert h.controller.start_task_search(second, plan, guard=lambda: True, finished=output.append)
    h.controller.cancel_task_search(first.request_id)
    wait_until(h.app, lambda: not h.controller.busy)
    assert [result.request_id for result in output] == [first.request_id, second.request_id]


@pytest.fixture
def tg(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TelegramRuntimeController, list[dict[str, Any]], list[float]]]:
    import json

    clock = [100.0]
    c = TelegramRuntimeController(tmp_path, clock=lambda: clock[0])
    assert c.configure_reviewed_replies(reply_qualification_for(c.principal))
    c.state = WireState(schema_from((RESOURCES / "telegram/backend/schema.json").read_bytes()))
    c.state.confirmed, c.state.session_deadline = True, 1000
    c.state.session_started = 100
    c.process = NoChildProcess(c)
    c.resources = RESOURCES
    commands: list[dict[str, Any]] = []

    def flush(_process: QProcess, _generation: int) -> None:
        commands.extend(json.loads(raw) for raw in c._pending_writes)
        c._pending_writes.clear()

    monkeypatch.setattr(c, "_flush_writes", flush)
    c._timer.stop()
    yield c, commands, clock
    c._timer.stop()
    c.process = None


def response(
    c: TelegramRuntimeController, request_id: UUID, wire_id: str, snapshot: SourceTaskSnapshot
) -> None:
    assert c.state is not None
    c._receive(
        {
            "v": 3,
            "event_seq": c.state.sequence + 1,
            "type": "task_result",
            "request_id": wire_id,
            "task_id": str(request_id),
            "snapshot": snapshot.model_dump(mode="json"),
        }
    )


def test_telegram_task_no_tab_visibility_and_retained_source_mutation_changes_binding(
    tg: tuple[TelegramRuntimeController, list[dict[str, Any]], list[float]],
) -> None:
    c, commands, _ = tg
    request, plan = task(c.principal, datetime.now(UTC))
    output: list[SourceTaskSnapshot] = []
    assert not c.visible
    binding = c.task_account_binding
    assert c.start_task_search(request, plan, guard=lambda: True, finished=output.append)
    command = commands[-1]
    assert command["v"] == 3
    assert command["command"] == "task_search"
    snapshot = task_snapshot(request, plan, "telegram", datetime.now(UTC), (), status="searched")
    response(c, request.request_id, command["request_id"], snapshot)
    assert len(output) == 1
    assert c.task_account_binding == binding
    assert c.state is not None
    c._receive(
        {
            "v": 3,
            "event_seq": c.state.sequence + 1,
            "type": "task_invalidated",
            "request_id": command["request_id"],
            "task_id": str(request.request_id),
            "reason": "source_changed",
        }
    )
    assert c.task_account_binding != binding
    assert c.live
    assert c.state.confirmed


def test_telegram_timeout_delivers_one_error_and_late_old_result_cannot_replace_new_task(
    tg: tuple[TelegramRuntimeController, list[dict[str, Any]], list[float]],
) -> None:
    c, commands, clock = tg
    old, plan = task(c.principal, datetime.now(UTC))
    new, new_plan = task(c.principal, datetime.now(UTC))
    output: list[SourceTaskSnapshot] = []
    assert c.start_task_search(old, plan, guard=lambda: True, finished=output.append)
    old_wire = commands[-1]["request_id"]
    clock[0] += 31
    c.poll()
    assert len(output) == 1
    assert output[0].coverage[0].detail == "telegram_task_timeout"
    assert c.start_task_search(new, new_plan, guard=lambda: True, finished=output.append)
    new_wire = commands[-1]["request_id"]
    c.cancel_task_search(old.request_id)
    response(
        c,
        old.request_id,
        old_wire,
        task_snapshot(old, plan, "telegram", datetime.now(UTC), (), status="searched"),
    )
    assert len(output) == 1
    assert c._task_flight is not None
    assert c._task_flight.request == new
    response(
        c,
        new.request_id,
        new_wire,
        task_snapshot(new, new_plan, "telegram", datetime.now(UTC), (), status="searched"),
    )
    assert [item.request_id for item in output] == [old.request_id, new.request_id]


def test_reviewed_telegram_bundle_exact_12_resources_and_two_pinned_protocols(
    repository: Path,
    telegram_sdk: Path,
    tmp_path: Path,
) -> None:
    import hashlib

    from satori.desktop.telegram_reply_worker import PROTOCOL_SHA256 as REPLY_PROTOCOL_SHA256

    REPLY_PROTOCOL_PATH = "telegram/reply_v1/protocol.json"

    for relative in (TASK_PROTOCOL_PATH, REPLY_PROTOCOL_PATH):
        target = repository / "src/satori/resources" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((RESOURCES / relative).read_bytes())
    resources = package._prepare_telegram_resources(
        repository, telegram_sdk, tmp_path / "stage", reviewed_replies=True
    )
    assert len(resources.files) == 12
    paths = {relative for relative, _digest, _size in resources.files}
    assert "task_v1/protocol.json" in paths
    assert "reply_v1/protocol.json" in paths
    assert (
        hashlib.sha256((resources.directory / "task_v1/protocol.json").read_bytes()).hexdigest()
        == TASK_PROTOCOL_SHA256
    )
    assert (
        hashlib.sha256((resources.directory / "reply_v1/protocol.json").read_bytes()).hexdigest()
        == REPLY_PROTOCOL_SHA256
    )
