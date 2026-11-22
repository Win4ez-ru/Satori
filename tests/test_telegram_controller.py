"""Synthetic Telegram presentation intents; no admitted native runtime or account proof."""

import ctypes
import os
import socket
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import NoReturn, cast
from uuid import uuid4

import pytest
from PySide6.QtWidgets import QApplication

from satori.desktop.telegram_contracts import (
    Operation,
    TelegramCatalog,
    TelegramChat,
    TelegramCommand,
    TelegramMessage,
    TelegramPage,
    TelegramPendingToken,
    TelegramRow,
    TelegramRuntimeAdmission,
    TelegramSelection,
    TelegramSession,
    TelegramViewport,
)
from satori.desktop.telegram_controller import Dispatcher, TelegramController

PRIVATE = "SYNTHETIC_SOURCE_MUST_NOT_BECOME_DIAGNOSTIC"


@dataclass
class Clock:
    value: float = 1000.0

    def __call__(self) -> float:
        return self.value


class PrivateFailure(RuntimeError):
    def __str__(self) -> str:
        raise AssertionError("exception text must not be evaluated")

    def __repr__(self) -> str:
        raise AssertionError("exception representation must not be evaluated")


@dataclass
class Recorder:
    commands: list[TelegramCommand] = field(default_factory=list)
    reject: set[Operation] = field(default_factory=set)

    def __call__(self, command: TelegramCommand) -> None:
        self.commands.append(command)
        if command.token.operation in self.reject:
            raise PrivateFailure()


@dataclass
class Harness:
    controller: TelegramController
    clock: Clock
    recorder: Recorder
    chat: TelegramChat = field(
        default_factory=lambda: TelegramChat(handle=uuid4(), title=PRIVATE, eligible=True)
    )

    def token(self, operation: Operation) -> TelegramPendingToken:
        command = self.recorder.commands[-1]
        assert command.token.operation == operation
        return command.token

    def admit(self) -> None:
        # These three fake hashes allow test intents only. They are not verified
        # runtime, policy or live selected-read evidence and are never production input.
        admission = TelegramRuntimeAdmission(
            principal=self.controller.principal,
            runtime_sha256="a" * 64,
            policy_sha256="b" * 64,
            selected_read_receipt_sha256="c" * 64,
        )
        assert self.controller.configure_runtime(admission, self.recorder)

    def connected(self, *, expires: float = 1800.0) -> TelegramPendingToken:
        self.admit()
        assert self.controller.connect_account(consent=True)
        token = self.token("connect")
        assert self.controller.accept_connected(
            token,
            TelegramSession(
                session_id=uuid4(),
                account_epoch=uuid4(),
                account_label=PRIVATE,
                account_confirmed=True,
                expires_mono=expires,
            ),
        )
        return token

    def catalog(self, *, expires: float = 1060.0) -> TelegramCatalog:
        assert self.controller.list_chats()
        catalog = TelegramCatalog(catalog_id=uuid4(), chats=(self.chat,), expires_mono=expires)
        assert self.controller.accept_catalog(self.token("catalog"), catalog)
        return catalog

    def selected(self, *, expires: float = 1040.0) -> TelegramSelection:
        self.connected()
        catalog = self.catalog()
        assert self.controller.select_chat(self.chat.handle)
        selection = TelegramSelection(
            lease_id=uuid4(),
            catalog_id=catalog.catalog_id,
            chat_handle=self.chat.handle,
            expires_mono=expires,
        )
        assert self.controller.accept_selection(self.token("select"), selection)
        return selection

    def make_page(self, selection: TelegramSelection, *, expires: float = 1020.0) -> TelegramPage:
        return TelegramPage(
            page_id=uuid4(),
            lease_id=selection.lease_id,
            chat_handle=selection.chat_handle,
            messages=(TelegramMessage(handle=uuid4(), speaker=PRIVATE, body=PRIVATE),),
            expires_mono=expires,
        )

    def loaded(self) -> TelegramPage:
        selection = self.selected()
        assert self.controller.read_selected()
        page = self.make_page(selection)
        assert self.controller.accept_page(self.token("read"), page)
        return page

    def operations(self) -> list[Operation]:
        return [command.token.operation for command in self.recorder.commands]


@pytest.fixture(scope="module")
def app() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    existing = QApplication.instance()
    return existing if isinstance(existing, QApplication) else QApplication([])


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    attempts: list[str] = []

    def forbidden(*args: object, **kwargs: object) -> NoReturn:
        attempts.append("external_io")
        raise AssertionError("native, process and socket factories are forbidden")

    monkeypatch.setattr(ctypes, "CDLL", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    for name in ("Popen", "run", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, forbidden)
    yield
    assert attempts == []


@pytest.fixture
def harness(app: QApplication, tmp_path: Path) -> Iterator[Harness]:
    clock, recorder = Clock(), Recorder()
    # This non-existent path only identifies the local principal; no profile is read.
    controller = TelegramController(tmp_path / "synthetic-profile", clock=clock)
    value = Harness(controller, clock, recorder)
    yield value
    controller.shutdown()
    for command in reversed(recorder.commands):
        if command.token.operation == "close":
            controller.accept_closed(command.token)
            break
    controller.deleteLater()
    app.processEvents()


def test_default_construction_poll_and_direct_consent_do_not_connect(harness: Harness) -> None:
    controller = harness.controller
    for consent in (False, True):
        assert not controller.connect_account(consent=consent)
    harness.clock.value = 1100.0
    controller.poll()
    assert not controller.connect_enabled
    assert not controller.list_enabled
    assert not controller.read_enabled
    assert controller.status == "runtime_unavailable"
    assert controller.page is None
    assert len(controller.chats) == 0
    assert controller.account_label == ""
    assert harness.recorder.commands == []


def test_unsolicited_dtos_and_partial_admission_cannot_create_runtime(harness: Harness) -> None:
    controller = harness.controller
    token = TelegramPendingToken(controller.principal, 0, uuid4(), "connect")
    session = TelegramSession(
        session_id=uuid4(),
        account_epoch=uuid4(),
        account_label=PRIVATE,
        account_confirmed=True,
        expires_mono=1100.0,
    )
    assert not controller.accept_connected(token, session)
    assert not controller.accept_catalog(
        replace(token, operation="catalog"),
        TelegramCatalog(catalog_id=uuid4(), chats=(harness.chat,), expires_mono=1060.0),
    )
    admission = TelegramRuntimeAdmission(
        principal=controller.principal,
        runtime_sha256="a" * 64,
        policy_sha256="b" * 64,
        selected_read_receipt_sha256="c" * 64,
    )
    assert not controller.configure_runtime(admission, cast(Dispatcher, None))
    wrong = admission.model_copy(
        update={"principal": controller.principal.model_copy(update={"session_id": uuid4()})}
    )
    assert not controller.configure_runtime(wrong, harness.recorder)
    assert not controller.connect_enabled
    assert not controller.connect_account(consent=True)
    assert harness.recorder.commands == []


def test_synthetic_admission_requires_exact_consent_before_dispatch(harness: Harness) -> None:
    harness.admit()
    assert harness.controller.connect_enabled
    assert harness.recorder.commands == []
    assert not harness.controller.connect_account()
    assert not harness.controller.connect_account(consent=cast(bool, 1))
    assert harness.recorder.commands == []
    assert harness.controller.connect_account(consent=True)
    assert harness.operations() == ["connect"]
    assert not harness.controller.connect_enabled


def test_human_connect_wait_has_five_minutes_but_never_retries_on_timeout(harness: Harness) -> None:
    harness.admit()
    assert harness.controller.connect_account(consent=True)
    token = harness.token("connect")
    harness.clock.value = 1031.0
    harness.controller.poll()
    assert harness.controller.busy
    assert harness.operations() == ["connect"]
    harness.clock.value = 1299.0
    harness.controller.poll()
    assert harness.operations() == ["connect"]
    harness.clock.value = 1300.0
    harness.controller.poll()
    assert harness.controller.status == "timeout"
    assert harness.operations() == ["connect", "close"]
    assert not harness.controller.accept_connected(
        token,
        TelegramSession(
            session_id=uuid4(),
            account_epoch=uuid4(),
            account_label=PRIVATE,
            account_confirmed=True,
            expires_mono=1800.0,
        ),
    )
    assert not harness.controller.connect_enabled


def test_explicit_connected_catalog_selection_and_read_are_separate(harness: Harness) -> None:
    harness.connected()
    assert harness.operations() == ["connect"]
    assert harness.controller.account_label == PRIVATE
    initial_chats = harness.controller.chats
    assert initial_chats == ()
    assert harness.controller.page is None
    assert not harness.controller.read_enabled
    catalog = harness.catalog()
    assert harness.operations() == ["connect", "catalog"]
    catalog_chats = harness.controller.chats
    assert catalog_chats == (harness.chat,)
    assert harness.controller.page is None
    assert harness.controller.select_chat(harness.chat.handle)
    selection = TelegramSelection(
        lease_id=uuid4(),
        catalog_id=catalog.catalog_id,
        chat_handle=harness.chat.handle,
        expires_mono=1040.0,
    )
    assert harness.controller.accept_selection(harness.token("select"), selection)
    assert harness.controller.page is None
    assert harness.operations() == ["connect", "catalog", "select"]
    assert harness.controller.read_selected()
    page = harness.make_page(selection)
    assert harness.controller.accept_page(harness.token("read"), page)
    assert harness.controller.page == page
    assert harness.operations() == ["connect", "catalog", "select", "read"]


@pytest.mark.parametrize("replacement", ["copy", "session", "generation", "principal"])
def test_reconstructed_or_foreign_connect_tokens_cannot_admit(
    harness: Harness, replacement: str
) -> None:
    harness.admit()
    assert harness.controller.connect_account(consent=True)
    token = harness.token("connect")
    if replacement == "copy":
        forged = replace(token)
    elif replacement == "session":
        forged = replace(token, session_id=uuid4())
    elif replacement == "generation":
        forged = replace(token, generation=token.generation + 1)
    else:
        forged = replace(
            token, principal=token.principal.model_copy(update={"profile_id": uuid4()})
        )
    session = TelegramSession(
        session_id=uuid4(),
        account_epoch=uuid4(),
        account_label=PRIVATE,
        account_confirmed=True,
        expires_mono=1100.0,
    )
    assert not harness.controller.accept_connected(forged, session)
    assert harness.controller.busy
    assert harness.controller.account_label == ""
    assert harness.controller.accept_connected(token, session)
    assert not harness.controller.accept_connected(token, session)
    assert harness.operations() == ["connect"]


def test_profile_change_rejects_old_result_and_waits_for_its_owned_close(
    harness: Harness, tmp_path: Path
) -> None:
    harness.admit()
    assert harness.controller.connect_account(consent=True)
    old = harness.token("connect")
    old_principal = harness.controller.principal
    harness.controller.set_profile(tmp_path / "other-synthetic-profile")
    close = harness.token("close")
    assert harness.controller.principal != old_principal
    assert not harness.controller.connect_enabled
    assert not harness.controller.shutdown_complete
    assert not harness.controller.accept_connected(
        old,
        TelegramSession(
            session_id=uuid4(),
            account_epoch=uuid4(),
            account_label=PRIVATE,
            account_confirmed=True,
            expires_mono=1100.0,
        ),
    )
    assert harness.controller.accept_closed(close)
    assert harness.controller.shutdown_complete
    assert not harness.controller.connect_enabled
    assert len(harness.controller.chats) == 0
    assert harness.controller.page is None


def test_page_reply_requires_original_read_token_and_one_admission(harness: Harness) -> None:
    selection = harness.selected()
    old_select = harness.token("select")
    assert harness.controller.read_selected()
    read = harness.token("read")
    page = harness.make_page(selection)
    assert not harness.controller.accept_page(old_select, page)
    assert not harness.controller.accept_page(replace(read), page)
    assert harness.controller.page is None
    assert harness.controller.accept_page(read, page)
    harness.clock.value = 1005.0
    renewed = page.model_copy(update={"expires_mono": 1030.0})
    assert not harness.controller.accept_page(read, renewed)
    assert harness.controller.page == page
    harness.clock.value = page.expires_mono
    harness.controller.poll()
    assert harness.controller.page is None
    assert harness.controller.status == "page_expired"
    assert harness.operations().count("read") == 1


@pytest.mark.parametrize("phase", ["session", "catalog", "selection"])
def test_current_reply_outside_its_scope_or_lifetime_retires_the_owned_connection(
    harness: Harness, phase: str
) -> None:
    if phase == "session":
        harness.admit()
        assert harness.controller.connect_account(consent=True)
        accepted = harness.controller.accept_connected(
            harness.token("connect"),
            TelegramSession(
                session_id=uuid4(),
                account_epoch=uuid4(),
                account_label=PRIVATE,
                account_confirmed=True,
                expires_mono=1900.1,
            ),
        )
    elif phase == "catalog":
        harness.connected()
        assert harness.controller.list_chats()
        accepted = harness.controller.accept_catalog(
            harness.token("catalog"),
            TelegramCatalog(catalog_id=uuid4(), chats=(harness.chat,), expires_mono=1060.1),
        )
    else:
        harness.connected()
        harness.catalog()
        assert harness.controller.select_chat(harness.chat.handle)
        accepted = harness.controller.accept_selection(
            harness.token("select"),
            TelegramSelection(
                lease_id=uuid4(),
                catalog_id=uuid4(),
                chat_handle=harness.chat.handle,
                expires_mono=1040.0,
            ),
        )
    assert not accepted
    assert harness.controller.status == "source_rejected"
    assert harness.controller.page is None
    assert harness.controller.account_label == ""
    assert not harness.controller.connect_enabled
    assert not harness.controller.shutdown_complete
    assert harness.operations().count("close") == 1
    assert harness.controller.accept_closed(harness.token("close"))


def test_previous_page_id_under_a_new_read_token_cannot_renew_its_lifetime(
    harness: Harness,
) -> None:
    page = harness.loaded()
    harness.clock.value = 1005.0
    assert harness.controller.read_selected()
    fresh_token = harness.token("read")
    renewed = page.model_copy(update={"expires_mono": 1030.0})
    assert not harness.controller.accept_page(fresh_token, renewed)
    assert harness.controller.status == "source_rejected"
    assert harness.controller.page is None
    assert harness.controller.chats == ()
    assert not harness.controller.read_enabled
    assert harness.operations().count("read") == 2
    assert harness.operations().count("close") == 1


@pytest.mark.parametrize("resource", ["lease", "chat", "deadline"])
def test_current_read_with_wrong_scope_fails_closed_and_closes_owned_port(
    harness: Harness, resource: str
) -> None:
    selection = harness.selected()
    assert harness.controller.read_selected()
    token = harness.token("read")
    page = harness.make_page(selection)
    changes_by_resource: dict[str, dict[str, object]] = {
        "lease": {"lease_id": uuid4()},
        "chat": {"chat_handle": uuid4()},
        "deadline": {"expires_mono": 1400.0},
    }
    changes = changes_by_resource[resource]
    assert not harness.controller.accept_page(token, page.model_copy(update=changes))
    assert harness.controller.status == "source_rejected"
    assert harness.controller.page is None
    assert harness.controller.chats == ()
    assert harness.controller.account_label == ""
    assert harness.operations().count("close") == 1
    assert not harness.controller.shutdown_complete
    assert harness.controller.accept_closed(harness.token("close"))
    assert harness.controller.shutdown_complete
    assert not harness.controller.connect_enabled


def test_duplicate_catalog_and_selection_callbacks_do_not_renew_expiry(harness: Harness) -> None:
    harness.connected()
    catalog = harness.catalog(expires=1050.0)
    catalog_token = harness.token("catalog")
    harness.clock.value = 1005.0
    assert not harness.controller.accept_catalog(
        catalog_token, catalog.model_copy(update={"expires_mono": 1060.0})
    )
    assert harness.controller.select_chat(harness.chat.handle)
    select_token = harness.token("select")
    selection = TelegramSelection(
        lease_id=uuid4(),
        catalog_id=catalog.catalog_id,
        chat_handle=harness.chat.handle,
        expires_mono=1020.0,
    )
    assert harness.controller.accept_selection(select_token, selection)
    assert not harness.controller.accept_selection(
        select_token, selection.model_copy(update={"expires_mono": 1040.0})
    )
    harness.clock.value = 1020.0
    harness.controller.poll()
    assert harness.controller.selected_handle is None
    assert harness.controller.chats == (harness.chat,)
    assert not harness.controller.read_enabled
    harness.clock.value = 1050.0
    harness.controller.poll()
    assert len(harness.controller.chats) == 0
    assert harness.controller.status == "catalog_expired"
    assert harness.operations().count("catalog") == 1
    assert harness.operations().count("select") == 1
    assert "read" not in harness.operations()


def test_selecting_another_chat_clears_page_without_implicit_read(harness: Harness) -> None:
    harness.loaded()
    before = harness.operations().count("read")
    assert harness.controller.select_chat(harness.chat.handle)
    assert harness.controller.page is None
    assert not harness.controller.read_enabled
    assert harness.operations().count("read") == before


def test_catalog_refresh_clears_selection_and_body_before_response(harness: Harness) -> None:
    harness.loaded()
    assert harness.controller.list_chats()
    assert harness.controller.page is None
    assert harness.controller.selected_handle is None
    assert harness.controller.chats == ()
    assert harness.operations()[-1] == "catalog"
    assert harness.operations().count("read") == 1


def test_only_current_connection_can_invalidate_source_without_automatic_read(
    harness: Harness,
) -> None:
    harness.loaded()
    connection = harness.recorder.commands[0].token
    assert not harness.controller.invalidate_source(replace(connection))
    assert not harness.controller.fail_connection(replace(connection), PRIVATE)
    assert harness.controller.page is not None
    before = list(harness.recorder.commands)
    assert harness.controller.invalidate_source(connection)
    assert harness.controller.status == "source_changed"
    assert harness.controller.account_label == PRIVATE
    assert harness.controller.page is None
    assert harness.controller.chats == ()
    assert harness.controller.selected_handle is None
    assert all(
        command.token.operation == "conceal" for command in harness.recorder.commands[len(before) :]
    )
    assert not harness.controller.read_enabled


def test_catalog_expiry_discards_an_inflight_read_without_regrant_or_retry(
    harness: Harness,
) -> None:
    selection = harness.selected(expires=1100.0)
    harness.clock.value = 1050.0
    assert harness.controller.read_selected()
    token = harness.token("read")
    harness.clock.value = 1060.0
    harness.controller.poll()
    assert not harness.controller.accept_page(token, harness.make_page(selection, expires=1080.0))
    assert harness.controller.page is None
    assert harness.controller.selected_handle is None
    assert harness.controller.chats == ()
    assert harness.controller.status == "catalog_expired"
    assert harness.operations().count("read") == 1


def test_request_timeout_closes_once_and_rejects_late_success(harness: Harness) -> None:
    selection = harness.selected()
    assert harness.controller.read_selected()
    token = harness.token("read")
    harness.clock.value = 1030.0
    harness.controller.poll()
    assert harness.controller.status == "timeout"
    assert not harness.controller.accept_page(token, harness.make_page(selection, expires=1035.0))
    harness.clock.value = 1100.0
    harness.controller.poll()
    assert harness.operations().count("read") == 1
    assert harness.operations().count("close") == 1
    assert not harness.controller.connect_enabled


def test_session_expiry_cannot_be_extended_by_a_replayed_connected_reply(harness: Harness) -> None:
    token = harness.connected(expires=1040.0)
    harness.clock.value = 1010.0
    assert not harness.controller.accept_connected(
        token,
        TelegramSession(
            session_id=uuid4(),
            account_epoch=uuid4(),
            account_label=PRIVATE,
            account_confirmed=True,
            expires_mono=1800.0,
        ),
    )
    harness.clock.value = 1040.0
    harness.controller.poll()
    assert harness.controller.status == "expired"
    assert harness.controller.account_label == ""
    assert harness.operations() == ["connect", "close"]


def test_clock_rollback_fails_closed_instead_of_extending_visible_source(harness: Harness) -> None:
    harness.loaded()
    harness.clock.value = 999.0
    harness.controller.poll()
    assert harness.controller.status == "runtime_failed"
    assert harness.controller.page is None
    assert harness.controller.account_label == ""
    assert harness.operations().count("close") == 1
    assert harness.operations().count("read") == 1


def test_unknown_failure_is_closed_and_never_projects_source_or_exception_text(
    harness: Harness,
) -> None:
    selection = harness.selected()
    assert harness.controller.read_selected()
    token = harness.token("read")
    assert harness.controller.accept_failure(token, PRIVATE)
    assert harness.controller.status == "runtime_failed"
    assert harness.controller.page is None
    assert harness.controller.chats == ()
    assert harness.controller.account_label == ""
    assert not harness.controller.accept_page(token, harness.make_page(selection))
    assert PRIVATE not in repr(harness.recorder.commands)
    assert PRIVATE not in repr(harness.controller)
    harness.controller.cancel()
    harness.controller.poll()
    assert harness.controller.status == "runtime_failed"
    assert harness.operations().count("close") == 1


def test_dispatch_exception_does_not_invoke_str_or_retry_uncertain_delivery(
    harness: Harness,
) -> None:
    harness.selected()
    harness.recorder.reject.add("read")
    assert not harness.controller.read_selected()
    assert harness.controller.status == "runtime_failed"
    assert harness.controller.page is None
    harness.controller.poll()
    assert harness.operations().count("read") == 1
    assert harness.operations().count("close") == 1
    assert not harness.controller.shutdown_complete


def test_shutdown_waits_for_exact_owned_close_and_emits_completion_once(harness: Harness) -> None:
    harness.loaded()
    finished: list[bool] = []
    harness.controller.shutdown_finished.connect(lambda: finished.append(True))
    harness.controller.shutdown()
    close = harness.token("close")
    assert harness.controller.page is None
    assert not harness.controller.shutdown_complete
    assert finished == []
    assert not harness.controller.accept_closed(cast(TelegramPendingToken, None))
    assert not harness.controller.accept_closed(replace(close))
    assert not harness.controller.accept_closed(replace(close, request_id=uuid4()))
    assert not harness.controller.shutdown_complete
    assert harness.controller.accept_closed(close)
    assert harness.controller.shutdown_complete
    assert finished == [True]
    assert not harness.controller.accept_closed(close)
    harness.controller.shutdown()
    assert finished == [True]
    assert harness.operations().count("close") == 1


def test_failed_close_dispatch_cannot_claim_shutdown_complete(harness: Harness) -> None:
    harness.connected()
    harness.recorder.reject.add("close")
    harness.controller.shutdown()
    assert not harness.controller.shutdown_complete
    assert harness.operations().count("close") == 1
    harness.controller.shutdown()
    assert harness.operations().count("close") == 1
    assert not harness.controller.accept_closed(cast(TelegramPendingToken, None))
    assert harness.controller.accept_closed(harness.token("close"))
    assert harness.controller.shutdown_complete


def test_conceal_discards_page_and_inflight_read_without_auto_read_on_resume(
    harness: Harness,
) -> None:
    page = harness.loaded()
    assert harness.controller.read_selected()
    read = harness.token("read")
    harness.controller.conceal()
    assert harness.controller.page is None
    assert not harness.controller.read_enabled
    assert not harness.controller.accept_page(read, page)
    assert harness.operations()[-1] == "conceal"
    before = list(harness.recorder.commands)
    harness.controller.set_foreground(True)
    harness.controller.poll()
    assert harness.controller.read_enabled
    assert harness.controller.page is None
    assert harness.recorder.commands == before
    assert harness.operations().count("read") == 2


def test_conceal_carries_the_visible_page_scope_before_dropping_it(harness: Harness) -> None:
    page = harness.loaded()
    harness.controller.conceal()
    command = harness.recorder.commands[-1]
    assert command.token.operation == "conceal"
    assert command.token.page_id == page.page_id
    assert command.token.lease_id == page.lease_id
    assert command.token.chat_handle == page.chat_handle
    assert command.viewport is None
    assert harness.controller.page is None
    harness.controller.set_foreground(True)
    assert harness.controller.page is None
    assert harness.operations().count("read") == 1


def test_viewport_is_a_bound_intent_with_no_direct_ack_or_freshness_renewal(
    harness: Harness,
) -> None:
    page = harness.loaded()
    viewport = TelegramViewport(
        page_id=page.page_id,
        foreground=True,
        first=0,
        after=400,
        rows=(TelegramRow(handle=page.messages[0].handle, first=80, after=180),),
    )
    assert harness.controller.report_viewport(viewport)
    command = harness.recorder.commands[-1]
    assert command.token.operation == "viewport"
    assert command.viewport == viewport
    assert not command.consent
    assert command.token.lease_id == page.lease_id
    assert command.token.chat_handle == page.chat_handle
    assert command.token.page_id == page.page_id
    assert not harness.controller.report_viewport(viewport)
    harness.clock.value = 1000.25
    assert harness.controller.report_viewport(viewport)
    assert not harness.controller.report_viewport(viewport.model_copy(update={"page_id": uuid4()}))
    foreign = TelegramRow(handle=uuid4(), first=80, after=180)
    assert not harness.controller.report_viewport(viewport.model_copy(update={"rows": (foreign,)}))
    current = harness.controller.page
    assert current is not None
    assert current.expires_mono == page.expires_mono
    assert harness.operations().count("read") == 1
    assert set(harness.operations()) <= {"connect", "catalog", "select", "read", "viewport"}
    harness.clock.value = page.expires_mono
    harness.controller.poll()
    assert harness.controller.page is None
    assert not harness.controller.report_viewport(viewport)
    assert harness.operations().count("read") == 1


def test_invalid_nonintersecting_viewport_retires_source_without_dispatching_it(
    harness: Harness,
) -> None:
    page = harness.loaded()
    viewport = TelegramViewport(
        page_id=page.page_id,
        foreground=True,
        first=0,
        after=400,
        rows=(TelegramRow(handle=page.messages[0].handle, first=80, after=180),),
    )
    outside = TelegramRow(handle=page.messages[0].handle, first=400, after=500)
    invalid = viewport.model_copy(update={"rows": (outside,)})
    assert not harness.controller.report_viewport(invalid)
    assert harness.controller.status == "source_rejected"
    assert harness.controller.page is None
    assert len(harness.controller.chats) == 0
    assert not harness.controller.read_enabled
    assert not harness.controller.shutdown_complete
    assert "viewport" not in harness.operations()
    assert harness.operations().count("read") == 1
    assert harness.operations().count("close") == 1


def test_viewport_cannot_replace_a_pending_read_or_its_timeout(harness: Harness) -> None:
    page = harness.loaded()
    assert harness.controller.read_selected()
    read = harness.token("read")
    viewport = TelegramViewport(page_id=page.page_id, foreground=True, first=0, after=400, rows=())
    harness.clock.value = 1010.0
    assert not harness.controller.report_viewport(viewport)
    assert harness.token("read") is read
    harness.clock.value = 1030.0
    harness.controller.poll()
    assert harness.controller.status == "timeout"
    assert harness.operations().count("read") == 2
    assert "viewport" not in harness.operations()
