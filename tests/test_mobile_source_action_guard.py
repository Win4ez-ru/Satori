"""Unsupported private-source actions terminate before Core or another capability owner."""

# ruff: noqa: RUF001 - intentional Russian source-action command literals

from typing import cast
from uuid import uuid4

import pytest
from PySide6.QtWidgets import QApplication

from satori.application.assistant.intents import parse_assistant_intent
from satori.desktop.mobile_gateway.contracts import DEFAULT_CAPABILITIES
from satori.desktop.reminder_controller import ReminderController
from tests.test_mobile_bridge import NOW, ForbiddenReminders, Harness, obj
from tests.test_mobile_bridge import app as app
from tests.test_mobile_bridge import h as h


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Найди письмо от PUBLIC@example.test", "mailbox_search"),
        ("Найди письмо с темой PUBLIC", "mailbox_search"),
        ("Найди в почте: in:anywhere has:attachment after:2026/10/01 PUBLIC", "mailbox_search"),
        ("Найди в выбранной почте: PUBLIC", "selected_lookup"),
        (
            "Ответь в Telegram на выбранное сообщение:PUBLIC own reply <b>literal</b>",
            "telegram_reply",
        ),
    ],
)
@pytest.mark.parametrize(
    "capabilities",
    [
        DEFAULT_CAPABILITIES,
        frozenset({"chat"}),
        DEFAULT_CAPABILITIES | {"mail", "gmail", "telegram", "mailbox_search", "*"},
    ],
    ids=["default", "chat-only", "forged-source-capabilities"],
)
@pytest.mark.parametrize("worker_running", [True, False], ids=["core-present", "core-absent"])
def test_private_source_actions_never_fall_through_to_reminders_or_worker(
    h: Harness,
    app: QApplication,
    text: str,
    kind: str,
    capabilities: frozenset[str],
    worker_running: bool,
) -> None:
    intent = parse_assistant_intent(text, now=NOW, timezone="Europe/Moscow", request_id=uuid4())
    assert intent is not None
    assert intent.kind == kind
    h.bridge._reminders = cast(ReminderController, ForbiddenReminders())
    h.process.running = worker_running
    h.submit(
        h.call(payload={"sessionID": "phone-session", "text": text}, capabilities=capabilities)
    )
    app.processEvents()
    assert h.process.frames == []
    assert len(h.results) == 1
    assert h.results[0].error_code is None
    data = h.results[0].data
    assert data is not None
    assert data["kind"] == "assistant"
    assert data["state"] == "completed"
    assert data["text"] == ""
    card = obj(data["card"])
    assert card["type"] == "connection_required"
    assert card["status"] == "permission_required"
    assert text not in str(card)
    assert h.guard_calls == 1
    assert h.preflight_calls == 0
    assert h.reminders.refreshes == 0
    assert h.reminders.creates == []
    assert h.reminders.cancels == []


def test_source_capability_names_do_not_replace_chat_authority(
    h: Harness, app: QApplication
) -> None:
    h.bridge._reminders = cast(ReminderController, ForbiddenReminders())
    h.submit(
        h.call(
            payload={"sessionID": "phone-session", "text": "Найди в почте: PUBLIC"},
            capabilities=frozenset({"mail", "gmail", "telegram", "reminders"}),
        )
    )
    app.processEvents()
    assert h.process.frames == []
    assert len(h.results) == 1
    assert h.results[0].data is None
    assert h.results[0].error_code == "source_grant_required"
    assert h.guard_calls == 0


def test_payload_cannot_inject_source_permissions(h: Harness, app: QApplication) -> None:
    h.bridge._reminders = cast(ReminderController, ForbiddenReminders())
    h.submit(
        h.call(
            payload={
                "sessionID": "phone-session",
                "text": "Найди в почте: PUBLIC",
                "mailbox_search": True,
                "capabilities": ["gmail"],
            }
        )
    )
    app.processEvents()
    assert h.process.frames == []
    assert len(h.results) == 1
    assert h.results[0].error_code is None
    data = h.results[0].data
    assert data is not None
    assert data["state"] == "failed"
    assert data["kind"] == "conversation"
    assert data["text"] == ""
    assert data["card"] is None
    assert h.guard_calls == 1
    assert h.preflight_calls == 0
    assert h.reminders.refreshes == 0
    assert h.reminders.creates == []


def test_source_denial_still_checks_current_device_guard(h: Harness, app: QApplication) -> None:
    h.bridge._reminders = cast(ReminderController, ForbiddenReminders())
    h.revoked = True
    h.submit(h.call(payload={"sessionID": "phone-session", "text": "Найди в почте: PUBLIC"}))
    app.processEvents()
    assert h.process.frames == []
    assert len(h.results) == 1
    assert h.results[0].data is None
    assert h.results[0].error_code == "device_revoked"
    assert h.guard_calls == 1
