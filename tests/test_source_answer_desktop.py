"""Public synthetic in-chat source tasks; no real account, model, browser or child."""

# ruff: noqa: RUF001 - intentional public Russian scenario and literal Unicode.

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from PySide6.QtCore import QEvent, QProcess, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication

from satori.application.assistant.action_contracts import AssistantIntent
from satori.application.assistant.source_answer_contracts import (
    SourceSearchPlan,
    SourceTaskAnswer,
    SourceTaskRequest,
    SourceTaskSnapshot,
)
from satori.application.assistant.source_task_reads import (
    SOURCE_TASK_REFINEMENT,
    source_task_window,
)
from satori.desktop.source_answer_controller import ConversationSourceController
from satori.desktop.source_answer_ui import ConversationSourcePanel
from satori.desktop.workspace_ui import show_plain_history
from satori.infrastructure.gmail import GmailConnector
from tests.test_telegram_chat_handoff import NoChildProcess
from tests.test_telegram_chat_handoff import app as app
from tests.test_telegram_chat_handoff import window as window

NOW = datetime(2026, 11, 6, 12, tzinfo=UTC)
QUERY = "привет, посмотри, написали ли мне по собесу последнему"
BODY = "PUBLIC: приглашаем на заключительный этап в пятницу в 14:00. <b>ЁЖ</b>"


@pytest.fixture(autouse=True)
def forbid_external_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("source_answer_fixture_crossed_account_browser_or_process_boundary")

    monkeypatch.setattr(QProcess, "start", forbidden)
    monkeypatch.setattr(QDesktopServices, "openUrl", forbidden)
    for name in ("begin_login", "search_headers", "task_search", "read_selected"):
        monkeypatch.setattr(GmailConnector, name, forbidden)


class Port:
    def __init__(self, source: str) -> None:
        self.source = source
        self.task_account_binding: tuple[object, ...] | None = (uuid4(),)
        self.calls: list[SourceTaskRequest] = []
        self.cancelled: list[UUID] = []
        self.finished: Callable[[SourceTaskSnapshot], None] | None = None
        self.guard: Callable[[], bool] = lambda: False
        self.immediate = False
        self.available = True

    def start_task_search(
        self,
        request: SourceTaskRequest,
        plan: SourceSearchPlan,
        *,
        guard: Callable[[], bool],
        finished: Callable[[SourceTaskSnapshot], None],
    ) -> bool:
        if not self.available:
            return False
        self.calls.append(request)
        self.guard, self.finished = guard, finished
        if self.immediate:
            finished(batch(request, plan, self.source))
        return True

    def cancel_task_search(self, task_id: UUID) -> None:
        self.cancelled.append(task_id)


def plan(*, clarification: bool = False) -> SourceSearchPlan:
    return SourceSearchPlan(
        status="clarify" if clarification else "search",
        clarification="Какую компанию ты имеешь в виду?" if clarification else None,
        mail_query=None if clarification else "собеседование",
        telegram_query=None if clarification else "собеседование",
        window_start=NOW - timedelta(days=14),
        window_end=NOW,
    )


def batch(
    request: SourceTaskRequest,
    search: SourceSearchPlan,
    source: str,
    *,
    text: str = BODY,
    status: str = "searched",
    detail: str = "",
) -> SourceTaskSnapshot:
    # The literal dictionary deliberately crosses the strict DTO JSON boundary.
    import json

    return SourceTaskSnapshot.model_validate_json(
        json.dumps(
            {
                "request_id": str(request.request_id),
                "scope": request.scope.model_dump(mode="json"),
                "plan_digest": search.digest,
                "observed_at": NOW.isoformat(),
                "items": [
                    {
                        "source": source,
                        "source_ref": source + ":PUBLIC-1",
                        "observed_at": NOW.isoformat(),
                        "event_at": (NOW - timedelta(hours=1)).isoformat(),
                        "title": "PUBLIC Собеседование <b>ЁЖ</b>",
                        "sender": "PUBLIC recruiter",
                        "text": text,
                        "completeness": "complete",
                    }
                ]
                if text
                else [],
                "coverage": [
                    {
                        "source": source,
                        "status": status,
                        "query": search.mail_query if source == "mail" else search.telegram_query,
                        "window_start": search.window_start.isoformat(),
                        "window_end": search.window_end.isoformat(),
                        "detail": detail,
                    }
                ],
            }
        )
    )


class Harness:
    def __init__(self, root: Path) -> None:
        self.now, self.tick = NOW, 100.0
        self.foreground = True
        self.mail, self.telegram = Port("mail"), Port("telegram")
        self.commands: list[dict[str, Any]] = []
        self.controller = ConversationSourceController(
            self.mail,
            self.telegram,
            root,
            uuid4(),
            foreground=lambda: self.foreground,
            clock=lambda: self.now,
            monotonic=lambda: self.tick,
        )
        self.controller._timer.stop()
        self.controller.bind_context(str(uuid4()), str(uuid4()), "PUBLIC-provider:model")
        self.controller.command_requested.connect(self.commands.append)

    @property
    def request(self) -> SourceTaskRequest:
        result = self.controller._request
        assert result is not None
        return result

    def start(self, *, source: str | None = None) -> UUID:
        intent = AssistantIntent.model_validate(
            {
                "request_id": uuid4(),
                "kind": "source_answer",
                "query": QUERY,
                "timezone": "Europe/Moscow",
                "source": source,
            }
        )
        assert self.controller.try_dispatch(intent)
        return intent.request_id

    def accept_plan(self, search: SourceSearchPlan | None = None) -> SourceSearchPlan:
        search = search or plan()
        self.controller.accept_result(
            str(self.request.request_id),
            "plan",
            {
                "request_id": str(self.request.request_id),
                "status": "planned",
                "plan": search.model_dump(mode="json"),
            },
        )
        return search

    def accept_answer(self, **changes: object) -> None:
        answer: dict[str, object] = {
            "text": "Нашла! Тебя пригласили на следующий этап в пятницу в 14:00. <b>ЁЖ</b>",
            "citations": ["mail:PUBLIC-1"],
            "verdict": "found",
        }
        answer.update(changes)
        self.controller.accept_result(
            str(self.request.request_id),
            "answer",
            {"request_id": str(self.request.request_id), "status": "answered", "answer": answer},
        )


@pytest.fixture
def harness(app: QApplication, tmp_path: Path) -> Iterator[Harness]:
    root = tmp_path / "PUBLIC-profile"
    root.mkdir(mode=0o700)
    result = Harness(root)
    yield result
    result.controller.clear()
    result.controller.deleteLater()
    app.processEvents()


def test_plan_bodies_answer_stay_in_one_transient_conversation(harness: Harness) -> None:
    h = harness
    request_id = h.start()
    search = h.accept_plan()
    assert h.controller.busy
    assert len(h.mail.calls) == len(h.telegram.calls) == 1
    assert h.mail.finished is not None
    h.mail.finished(batch(h.request, search, "mail"))
    assert len(h.commands) == 1
    assert h.telegram.finished is not None
    h.telegram.finished(batch(h.request, search, "telegram", text=""))
    assert [data["source_answer"]["operation"] for data in h.commands] == ["plan", "answer"]
    snapshot = h.controller._snapshot
    assert snapshot is not None
    assert snapshot.items[0].text == BODY
    assert snapshot.request_id == request_id
    h.accept_answer()
    assert h.controller.card is not None
    assert "пятницу в 14:00" in h.controller.card.text
    assert "Gmail: проверено" in "\n".join(h.controller.card.details)
    assert not h.controller.busy


def test_synchronous_sources_and_synthesis_cannot_dispatch_twice(harness: Harness) -> None:
    h = harness
    h.mail.immediate = h.telegram.immediate = True

    def immediate_answer(data: dict[str, Any]) -> None:
        if data["source_answer"]["operation"] == "answer":
            h.accept_answer()

    h.controller.command_requested.connect(immediate_answer)
    h.start()
    h.accept_plan()
    assert [data["source_answer"]["operation"] for data in h.commands] == ["plan", "answer"]
    assert h.controller.card is not None
    assert h.controller.card.status == "answered"


@pytest.mark.parametrize(
    "change",
    ["hide", "session", "profile", "expiry", "wall_rollback", "mono_rollback", "account", "cancel"],
)
def test_retired_task_cannot_publish_late_body_or_answer(harness: Harness, change: str) -> None:
    h = harness
    request_id = h.start()
    search = h.accept_plan()
    request = h.request
    callback = h.mail.finished
    assert callback is not None
    if change == "hide":
        h.foreground = False
    elif change == "session":
        h.controller.bind_context(str(uuid4()), str(uuid4()), "PUBLIC-provider:model")
    elif change == "profile":
        h.controller.set_profile(h.controller._profile / "OTHER", uuid4())
    elif change == "expiry":
        h.now += timedelta(seconds=121)
        h.tick += 121
    elif change == "wall_rollback":
        h.now -= timedelta(seconds=1)
    elif change == "mono_rollback":
        h.tick -= 1
    elif change == "account":
        h.mail.task_account_binding = (uuid4(),)
    else:
        h.controller.clear()
    h.controller.tick()
    callback(batch(request, search, "mail"))
    h.controller.accept_result(str(request_id), "answer", {"status": "answered"})
    assert h.controller.card is None
    assert h.controller._snapshot is None
    assert request_id in h.mail.cancelled
    assert [data["source_answer"]["operation"] for data in h.commands] == ["plan"]


@pytest.mark.parametrize("field", ["request_id", "scope", "plan_digest", "coverage", "items"])
def test_correlated_source_batch_rejects_wrong_owner(harness: Harness, field: str) -> None:
    h = harness
    h.start()
    search = h.accept_plan()
    good = batch(h.request, search, "mail")
    changes: dict[str, object] = {
        "request_id": uuid4(),
        "scope": h.request.scope.model_copy(update={"session_id": uuid4()}),
        "plan_digest": "0" * 64,
        "coverage": (good.coverage[0].model_copy(update={"source": "telegram"}),),
        "items": (good.items[0].model_copy(update={"source": "telegram"}),),
    }
    assert h.mail.finished is not None
    h.mail.finished(good.model_copy(update={field: changes[field]}))
    assert h.controller._batches["mail"].items == ()
    assert h.controller._batches["mail"].coverage[0].status == "unavailable"


def test_partial_or_unavailable_source_cannot_claim_global_absence(harness: Harness) -> None:
    h = harness
    h.telegram.available = False
    h.start()
    search = h.accept_plan()
    assert h.mail.finished is not None
    h.mail.finished(batch(h.request, search, "mail", text=""))
    snapshot = h.controller._snapshot
    assert snapshot is not None
    assert [coverage.status for coverage in snapshot.coverage] == ["searched", "unavailable"]
    h.accept_answer(text="Ничего не нашла.", citations=[], verdict="no_match")
    assert h.controller.card is not None
    assert h.controller.card.status == "error"


@pytest.mark.parametrize("budget", ["utf8", "count"])
def test_combined_sources_keep_one_global_budget_and_report_truncation(
    harness: Harness, budget: str
) -> None:
    h = harness
    h.start()
    search = h.accept_plan()
    count, text = (4, "Я" * 4000) if budget == "utf8" else (20, "PUBLIC")
    for source, port in (("mail", h.mail), ("telegram", h.telegram)):
        base = batch(h.request, search, source, text=text)
        items = tuple(
            base.items[0].model_copy(update={"source_ref": f"{source}:PUBLIC-{index}"})
            for index in range(1, count + 1)
        )
        assert port.finished is not None
        port.finished(base.model_copy(update={"items": items}))
    snapshot = h.controller._snapshot
    assert snapshot is not None
    assert len(snapshot.items) <= 20
    assert sum(len(item.text.encode("utf-8")) for item in snapshot.items) <= 32768
    assert len(snapshot.model_dump_json().encode("utf-8")) <= 48 * 1024
    assert snapshot.coverage[1].status == "partial"
    assert snapshot.coverage[1].more
    assert [data["source_answer"]["operation"] for data in h.commands] == ["plan", "answer"]


def test_clarification_is_user_turn_with_same_original_deadline(harness: Harness) -> None:
    h = harness
    first_id = h.start()
    first = h.request
    lease = h.controller._lease
    assert lease is not None
    deadline = lease.monotonic_deadline
    h.accept_plan(plan(clarification=True))
    assert h.controller.pending_clarification
    h.now += timedelta(seconds=20)
    h.tick += 20
    assert h.controller.answer_clarification("PUBLIC Яндекс")
    assert h.request.request_id != first_id
    assert h.request.expires_at == first.expires_at
    assert h.controller._lease is not None
    assert h.controller._lease.monotonic_deadline == deadline
    assert "Уточнение пользователя: PUBLIC Яндекс" in h.request.user_text
    assert first_id in h.mail.cancelled
    assert len(h.commands) == 2
    assert h.mail.calls == h.telegram.calls == []


def test_source_choice_retires_active_reads_without_widening_or_renewing(harness: Harness) -> None:
    h = harness
    old_id = h.start()
    before = h.request
    previous_plan = h.accept_plan()
    mail_result = h.mail.finished
    assert mail_result is not None
    assert h.controller.busy
    assert h.controller._lease is not None
    deadline = h.controller._lease.monotonic_deadline
    h.now += timedelta(seconds=8)
    h.tick += 8
    assert h.controller.answer_source_choice("В тг посмотри")
    assert h.request.request_id != old_id
    assert h.request.sources == ("telegram",)
    assert h.request.user_text == before.user_text + SOURCE_TASK_REFINEMENT + "В тг посмотри"
    assert h.request.expires_at == before.expires_at
    assert h.controller._lease is not None
    assert h.controller._lease.monotonic_deadline == deadline
    assert old_id in h.mail.cancelled
    assert old_id in h.telegram.cancelled
    assert not h.controller.answer_source_choice("В почте посмотри")
    mail_result(batch(before, previous_plan, "mail"))
    assert h.controller.card is not None
    assert h.controller.card.status == "planning"
    assert [data["source_answer"]["operation"] for data in h.commands] == ["plan", "plan"]


def test_local_day_cannot_widen_and_is_displayed_without_utc_date_rollover(
    harness: Harness,
) -> None:
    h = harness
    intent = AssistantIntent(
        request_id=uuid4(),
        kind="source_answer",
        query="А Алексей мне сегодня писал?",
        timezone="Europe/Moscow",
    )
    assert h.controller.try_dispatch(intent)
    h.accept_plan()
    assert h.controller.card is not None
    assert h.controller.card.status == "error"
    assert h.mail.calls == h.telegram.calls == []

    h.mail.immediate = h.telegram.immediate = True
    assert h.controller.try_dispatch(intent.model_copy(update={"request_id": uuid4()}))
    start, end = source_task_window(h.request)
    h.accept_plan(
        SourceSearchPlan(
            status="search",
            mail_query="Алексей",
            telegram_query="Алексей",
            telegram_match="peer",
            window_start=start,
            window_end=end,
        )
    )
    h.accept_answer()
    assert h.controller.card is not None
    assert h.controller.card.status == "answered"
    assert "Период: 06.11.2026." in h.controller.card.details[1]
    assert "05.11.2026" not in h.controller.card.details[1]


def test_ambiguous_telegram_contact_accepts_owner_surname_after_the_answer(
    harness: Harness,
) -> None:
    h = harness
    intent = AssistantIntent(
        request_id=uuid4(),
        kind="source_answer",
        query="В тг Алексей мне сегодня писал?",
        timezone="Europe/Moscow",
    )
    assert h.controller.try_dispatch(intent)
    first = h.request
    start, end = source_task_window(first)
    search = h.accept_plan(
        SourceSearchPlan(
            status="search",
            telegram_query="Алексей",
            telegram_match="peer",
            window_start=start,
            window_end=end,
        )
    )
    assert h.telegram.finished is not None
    h.telegram.finished(
        batch(first, search, "telegram", text="", status="partial", detail="peer_ambiguous")
    )
    h.accept_answer(
        text="Нашла несколько Алексеев. Какая у него фамилия?", citations=[], verdict="partial"
    )
    assert h.controller.pending_clarification
    assert h.controller.answer_clarification("Алексей Иванов")
    assert h.request.user_text == first.user_text + SOURCE_TASK_REFINEMENT + "Алексей Иванов"
    assert h.request.expires_at == first.expires_at
    assert h.request.sources == ("telegram",)


@pytest.mark.parametrize("source", [None, "telegram"])
def test_company_name_cannot_reclassify_original_sources(
    harness: Harness, source: str | None
) -> None:
    h = harness
    h.start(source=source)
    original_sources = h.request.sources
    h.accept_plan(plan(clarification=True))
    assert h.controller.answer_clarification("Почта Банк")
    assert h.request.sources == original_sources


def test_calls_only_followup_keeps_calls_only_scope(harness: Harness) -> None:
    h = harness
    # Synthetic call owner; this regression checks scope, not live phone readiness.
    h.controller._ports["calls"] = Port("calls")
    assert h.controller.try_dispatch(
        AssistantIntent(
            request_id=uuid4(),
            kind="source_answer",
            query="Кто мне звонил?",
            timezone="Europe/Moscow",
        )
    )
    h.accept_plan(
        SourceSearchPlan(status="search", window_start=NOW - timedelta(days=14), window_end=NOW)
    )
    h.accept_answer(
        text="Пока не могу посмотреть звонки: этот канал ещё не подключён.",
        citations=[],
        verdict="unknown",
    )
    assert h.controller.answer_followup("а во сколько?")
    assert h.request.sources == ("calls",)
    assert h.mail.calls == h.telegram.calls == []


def test_new_task_cancels_only_old_exact_task(harness: Harness) -> None:
    h = harness
    old_id = h.start()
    search = h.accept_plan()
    old_request, old_callback = h.request, h.mail.finished
    new_id = h.start()
    assert old_id in h.mail.cancelled
    assert new_id not in h.mail.cancelled
    assert old_callback is not None
    old_callback(batch(old_request, search, "mail"))
    assert h.request.request_id == new_id
    assert h.controller.card is not None
    assert h.controller.card.status == "planning"


def test_account_change_during_plan_cannot_search_new_account(harness: Harness) -> None:
    h = harness
    h.start()
    h.mail.task_account_binding = (uuid4(),)
    h.accept_plan()
    assert h.controller.card is None
    assert h.mail.calls == h.telegram.calls == []
    assert len(h.commands) == 1


def test_answer_bubble_plaintext_and_collapsed_coverage(
    harness: Harness, app: QApplication
) -> None:
    h = harness
    h.mail.immediate = h.telegram.immediate = True
    h.start()
    h.accept_plan()
    h.accept_answer()
    panel = ConversationSourcePanel(h.controller)
    panel.resize(740, 280)
    panel.show()
    app.processEvents()
    assert "<b>ЁЖ</b>" in panel.answer.toPlainText()
    assert panel.query.textFormat() == Qt.TextFormat.PlainText
    assert not panel.details_button.isChecked()
    assert not panel.details.isVisible()
    panel.details_button.setChecked(True)
    assert panel.details.isVisible()
    assert "PUBLIC recruiter" in panel.details.toPlainText()
    h.controller.clear()
    assert panel.answer.toPlainText() == ""
    assert panel.details.toPlainText() == ""
    panel.close()
    panel.deleteLater()


def test_provider_and_task_purpose_are_visible_before_planning_dispatch(
    harness: Harness, app: QApplication
) -> None:
    h = harness
    panel = ConversationSourcePanel(h.controller)
    observed: list[str] = []

    def inspect(data: dict[str, Any]) -> None:
        assert data["source_answer"]["operation"] == "plan"
        assert panel.provider.textFormat() == Qt.TextFormat.PlainText
        assert "PUBLIC-provider" in panel.provider.text()
        assert "model" in panel.provider.text()
        assert QUERY in panel.query.text()
        observed.append(panel.provider.text())

    h.controller.command_requested.connect(inspect)
    h.start()
    assert len(observed) == 1
    h.controller.clear()
    assert panel.provider.text() == ""
    panel.close()
    panel.deleteLater()
    app.processEvents()


def test_production_window_routes_natural_question_in_chat_before_calendar(
    window: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    w, requests = window
    w._calendar_identity, w._calendar_provider = str(uuid4()), "PUBLIC-provider:model"
    w.supports_source_answers = True  # Explicit synthetic admitted worker capability.
    w.process = NoChildProcess(w)
    monkeypatch.setattr(w.process, "write", lambda raw: len(raw))

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("source_question_entered_canonical_calendar_or_source_tab")

    monkeypatch.setattr(w.calendar_chat, "submit", forbidden)
    monkeypatch.setattr(w.calendar_chat, "answer", forbidden)
    monkeypatch.setattr(w, "_open_settings_section", forbidden)
    w.source_answer._ports = {"mail": Port("mail"), "telegram": Port("telegram")}
    before = w.transcript.toPlainText()
    w.editor.setPlainText(QUERY)
    w.send()
    assert w.tabs.currentWidget() is w.conversation_page
    assert w.transcript.toPlainText() == before
    assert w.source_answer.card is not None
    assert w.source_answer_panel.isVisible()
    assert w.editor.toPlainText() == ""
    assert len(requests) == 1
    assert requests[0][0] == "source_answer"
    assert requests[0][1]["source_answer"]["operation"] == "plan"
    app_instance = QApplication.instance()
    assert app_instance is not None
    app_instance.sendEvent(w, QEvent(QEvent.Type.WindowDeactivate))
    assert w.source_answer.card is None
    assert w.transcript.toPlainText() == before


@pytest.mark.parametrize(
    ("first_text", "refinement", "expected_sources"),
    [
        ("А Алексей мне сегодня писал?", "В тг посмотри", ("telegram",)),
        (
            "Написали ли мне по последнему собеседованию?",
            "Да, Север это компания, они должны были написать",
            ("mail", "telegram"),
        ),
    ],
)
def test_production_composer_keeps_owner_question_when_user_refines_source_task(
    window: Any,
    monkeypatch: pytest.MonkeyPatch,
    first_text: str,
    refinement: str,
    expected_sources: tuple[str, ...],
) -> None:
    w, requests = window
    w._calendar_identity, w._calendar_provider = str(uuid4()), "PUBLIC-provider:model"
    w.supports_source_answers = True
    w.process = NoChildProcess(w)
    monkeypatch.setattr(w.process, "write", lambda raw: len(raw))
    w.source_answer._ports = {"mail": Port("mail"), "telegram": Port("telegram")}
    history = w.transcript.toPlainText()
    w.editor.setPlainText(first_text)
    w.send()
    first = w.source_answer._request
    assert first is not None
    assert first.user_text == first_text
    start, end = source_task_window(first)
    clarification = SourceSearchPlan(
        status="clarify",
        clarification="Уточни, пожалуйста, кого ты имеешь в виду.",
        window_start=start,
        window_end=end,
    )
    w.source_answer.accept_result(
        str(first.request_id),
        "plan",
        {
            "request_id": str(first.request_id),
            "status": "planned",
            "plan": clarification.model_dump(mode="json"),
        },
    )
    assert w.source_answer.pending_clarification
    w.editor.setPlainText(refinement)
    w.send()
    current = w.source_answer._request
    assert current is not None
    assert current.request_id != first.request_id
    assert current.user_text == first_text + SOURCE_TASK_REFINEMENT + refinement
    assert current.sources == expected_sources
    assert current.expires_at == first.expires_at
    assert w.transcript.toPlainText() == history
    assert w.editor.toPlainText() == ""
    assert [name for name, _ in requests] == ["source_answer", "source_answer"]


@pytest.mark.parametrize(
    ("text", "expected_query"),
    [
        ("Найди в почте: newer_than:30d", "newer_than:30d"),
        ("Найди письмо от info@example.com", "from:(info@example.com)"),
        ("Найди письмо о собеседовании", None),
    ],
)
def test_production_composer_keeps_exact_mail_and_contextual_mail_routes(
    window: Any, monkeypatch: pytest.MonkeyPatch, text: str, expected_query: str | None
) -> None:
    w, requests = window
    w._calendar_identity, w._calendar_provider = str(uuid4()), "PUBLIC-provider:model"
    w.supports_source_answers = True
    w.process = NoChildProcess(w)
    monkeypatch.setattr(w.process, "write", lambda raw: len(raw))
    mail, telegram = Port("mail"), Port("telegram")
    w.source_answer._ports = {"mail": mail, "telegram": telegram}
    dispatched: list[AssistantIntent] = []
    monkeypatch.setattr(w.assistant, "dispatch", dispatched.append)
    w.editor.setPlainText(text)
    w.send()
    if text.startswith("Найди письмо от"):
        assert dispatched == []
        assert requests == []
        assert len(mail.calls) == 1
        assert telegram.calls == []
        assert w.source_answer._plan is not None
        assert w.source_answer._plan.mail_query == 'from:("info@example.com")'
        assert w.source_answer._plan.mail_headers_only
    elif expected_query is None:
        assert dispatched == []
        assert [name for name, _ in requests] == ["source_answer"]
    else:
        assert len(dispatched) == 1
        assert dispatched[0].kind == "mailbox_search"
        assert dispatched[0].query == expected_query
        assert requests == []
    assert w.editor.toPlainText() == ""


def test_production_composer_explicit_telegram_letter_never_queries_gmail(
    window: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    w, requests = window
    w._calendar_identity, w._calendar_provider = str(uuid4()), "PUBLIC-provider:model"
    w.supports_source_answers = True
    w.process = NoChildProcess(w)
    monkeypatch.setattr(w.process, "write", lambda raw: len(raw))
    mail, telegram = Port("mail"), Port("telegram")
    w.source_answer._ports = {"mail": mail, "telegram": telegram}
    dispatched: list[AssistantIntent] = []
    monkeypatch.setattr(w.assistant, "dispatch", dispatched.append)

    w.editor.setPlainText("Найди письмо от Acme в Telegram?")
    w.send()
    request = w.source_answer._request
    assert request is not None
    assert request.sources == ("telegram",)
    assert [name for name, _ in requests] == ["source_answer"]
    assert dispatched == []
    start, end = source_task_window(request)
    search = SourceSearchPlan(
        status="search", telegram_query="Acme", window_start=start, window_end=end
    )
    w.source_answer.accept_result(
        str(request.request_id),
        "plan",
        {
            "request_id": str(request.request_id),
            "status": "planned",
            "plan": search.model_dump(mode="json"),
        },
    )
    assert telegram.calls == [request]
    assert mail.calls == []
    assert w.tabs.currentWidget() is w.conversation_page


def test_production_wire_result_answers_from_bodies_without_history_or_voice(
    window: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    w, _ = window
    w._calendar_identity, w._calendar_provider = str(uuid4()), "PUBLIC-provider:model"
    w.supports_source_answers = True  # Explicit synthetic admitted worker capability.
    w.process = NoChildProcess(w)
    monkeypatch.setattr(w.process, "write", lambda raw: len(raw))
    mail, telegram = Port("mail"), Port("telegram")
    w.source_answer._ports = {"mail": mail, "telegram": telegram}
    calls: list[tuple[str, dict[str, Any]]] = []

    def request(command: str, data: dict[str, Any] | None = None) -> None:
        assert command == "source_answer"
        assert data is not None
        operation_id = str(uuid4())
        operation = data["source_answer"]["operation"]
        w.pending[operation_id] = command
        w.source_answer_requests[operation_id] = (data["request_id"], operation)
        calls.append((operation_id, data))

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("source_answer_entered_canonical_history_or_tts")

    monkeypatch.setattr(w, "request", request)
    monkeypatch.setattr(w.voice_panel, "committed_reply", forbidden)
    before = w.transcript.toPlainText()
    w.editor.setPlainText(QUERY)
    w.send()
    task = w.source_answer._request
    assert task is not None
    search = plan().model_copy(
        update={
            "window_start": task.created_at - timedelta(days=14),
            "window_end": task.created_at,
        }
    )
    w._receive(
        {
            "id": calls[0][0],
            "event": "result",
            "data": {
                "request_id": str(task.request_id),
                "status": "planned",
                "plan": search.model_dump(mode="json"),
            },
        }
    )
    assert mail.finished is not None
    assert telegram.finished is not None
    # Real Window uses the actual wall clock; keep the synthetic receipt within its task.
    raw_message = batch(task, search, "mail")
    message = raw_message.model_copy(
        update={
            "observed_at": task.created_at,
            "items": tuple(
                item.model_copy(
                    update={
                        "observed_at": task.created_at,
                        "event_at": task.created_at - timedelta(hours=1),
                    }
                )
                for item in raw_message.items
            ),
        }
    )
    empty = batch(task, search, "telegram", text="").model_copy(
        update={"observed_at": task.created_at}
    )
    mail.finished(message)
    telegram.finished(empty)
    assert len(calls) == 2
    assert calls[1][1]["source_answer"]["snapshot"]["items"][0]["text"] == BODY
    answer = SourceTaskAnswer(
        text="Нашла: в пятницу в 14:00 следующий этап. Кажется, твой гений всё-таки заметили.",
        citations=("mail:PUBLIC-1",),
        verdict="found",
    )
    w._receive(
        {
            "id": calls[1][0],
            "event": "result",
            "data": {
                "request_id": str(task.request_id),
                "status": "answered",
                "answer": answer.model_dump(mode="json"),
            },
        }
    )
    assert w.tabs.currentWidget() is w.conversation_page
    assert w.source_answer_panel.answer.toPlainText() == answer.text
    assert not w.source_answer_panel.details_button.isChecked()
    assert w.transcript.toPlainText() == before
    assert len(calls) == 2
    old_deadline = w.source_answer._lease
    assert old_deadline is not None
    w.editor.setPlainText("а во сколько?")
    w.send()
    assert len(calls) == 3
    refined = w.source_answer._request
    assert refined is not None
    assert refined.request_id != task.request_id
    assert refined.expires_at == task.expires_at
    assert w.source_answer._lease is not None
    assert w.source_answer._lease.monotonic_deadline == old_deadline.monotonic_deadline
    assert "Уточнение пользователя: а во сколько?" in refined.user_text
    assert answer.text not in refined.user_text
    assert BODY not in refined.user_text
    assert w.transcript.toPlainText() == before


def test_unrelated_greeting_does_not_become_source_followup(harness: Harness) -> None:
    h = harness
    h.mail.immediate = h.telegram.immediate = True
    h.start()
    h.accept_plan()
    h.accept_answer()
    assert not h.controller.answer_followup("привет, как дела?")
    assert len(h.commands) == 2


def test_exact_worker_cancel_receipt_cannot_clear_newer_busy_request(
    window: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    w, _ = window
    w.process = NoChildProcess(w)
    written: list[bytes] = []

    def write(raw: bytes) -> int:
        written.append(raw)
        return len(raw)

    monkeypatch.setattr(w.process, "write", write)
    w.busy = True
    w._source_answer_cancel_requested({"request_id": "PUBLIC-old-task"})
    assert len(written) == 1
    operation_id = next(key for key, value in w.pending.items() if value == "source_answer_cancel")
    w._receive({"id": operation_id, "event": "result", "data": {"status": "cancelled"}})
    assert w.busy
    assert operation_id not in w.pending


@pytest.mark.parametrize("source", ["mail", "telegram"])
def test_source_change_signal_revokes_core_lease_without_waiting_for_timer(
    window: Any, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    w, _ = window
    w._calendar_identity, w._calendar_provider = str(uuid4()), "PUBLIC-provider:model"
    w.supports_source_answers = True  # Explicit synthetic admitted worker capability.
    w.process = NoChildProcess(w)
    monkeypatch.setattr(w.process, "write", lambda raw: len(raw))
    ports = {"mail": Port("mail"), "telegram": Port("telegram")}
    w.source_answer._ports = ports
    w.editor.setPlainText(QUERY)
    w.send()
    lease = w.source_answer._lease
    assert lease is not None
    lease_path = (
        w.preferences.data_directory / "source-task-leases" / (lease.lease_id.hex + ".json")
    )
    assert lease_path.is_file()
    ports[source].task_account_binding = (uuid4(),)
    source_controller = w.mail_panel.controller if source == "mail" else w.telegram_panel.controller
    source_controller.changed.emit()
    # No processEvents/tick/timer wait: revocation is synchronous with the source event.
    assert not lease_path.exists()
    assert w.source_answer.card is None


@pytest.mark.parametrize("phase", ["planning", "answered"])
def test_production_window_source_bubble_never_overlaps_its_controls(
    window: Any, monkeypatch: pytest.MonkeyPatch, app: QApplication, phase: str
) -> None:
    w, _ = window
    w._calendar_identity, w._calendar_provider = str(uuid4()), "PUBLIC-provider:model"
    w.supports_source_answers = True  # Explicit synthetic admitted worker capability.
    w.process = NoChildProcess(w)
    monkeypatch.setattr(w.process, "write", lambda raw: len(raw))
    ports = {"mail": Port("mail"), "telegram": Port("telegram")}
    w.source_answer._ports = ports
    w.resize(1000, 720)
    show_plain_history(
        w.transcript,
        ["Ты:\nВчера был собес, теперь жду ответа.", "Сатори:\nЕсли что — посмотрим вместе."],
    )
    w.editor.setPlainText(QUERY)
    w.send()
    request = w.source_answer._request
    assert request is not None
    if phase == "answered":
        search = plan().model_copy(
            update={
                "window_start": request.created_at - timedelta(days=14),
                "window_end": request.created_at,
            }
        )
        w.source_answer.accept_result(
            str(request.request_id),
            "plan",
            {
                "request_id": str(request.request_id),
                "status": "planned",
                "plan": search.model_dump(mode="json"),
            },
        )
        for source, port in ports.items():
            raw = batch(request, search, source, text=BODY if source == "mail" else "")
            result = raw.model_copy(
                update={
                    "observed_at": request.created_at,
                    "items": tuple(
                        item.model_copy(
                            update={
                                "observed_at": request.created_at,
                                "event_at": request.created_at - timedelta(hours=1),
                            }
                        )
                        for item in raw.items
                    ),
                }
            )
            assert port.finished is not None
            port.finished(result)
        answer = SourceTaskAnswer(
            text="Нашла: тебя приглашают на следующий этап в пятницу в 14:00. "
            "Письмо пришло час назад. Похоже, твой гений прошёл первичный отбор.\n\n"
            "В Telegram новостей по этому собеседованию не нашла.",
            citations=("mail:PUBLIC-1",),
            verdict="found",
        )
        w.source_answer.accept_result(
            str(request.request_id),
            "answer",
            {
                "request_id": str(request.request_id),
                "status": "answered",
                "answer": answer.model_dump(mode="json"),
            },
        )
    # Fixed text heights post a new layout request. Inspect settled geometry,
    # not a synchronous grab between child resize and its parent reflow.
    for _ in range(6):
        app.processEvents()
    panel = w.source_answer_panel
    answer_bottom = panel.answer.mapTo(w, panel.answer.rect().bottomLeft()).y()
    button = panel.details_button if phase == "answered" else panel.cancel_button
    controls_top = button.mapTo(w, button.rect().topLeft()).y()
    assert answer_bottom + 6 <= controls_top
    assert w.rect().contains(panel.answer.mapTo(w, panel.answer.rect().bottomRight()))
    assert w.rect().contains(w.send_button.mapTo(w, w.send_button.rect().bottomRight()))
    assert not panel.details_button.isChecked()
    if phase == "planning":
        assert w.transcript.viewport().height() >= w.transcript.document().size().height()
