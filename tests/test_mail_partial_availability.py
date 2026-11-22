"""Synthetic partial metadata state, independent from selected-body completeness."""

# ruff: noqa: RUF001 - intentional Russian user-facing assertions

from dataclasses import asdict
from threading import Event
from typing import Literal

import pytest
from pydantic import ValidationError

from satori.application.assistant.mail import MailAccessCoordinator
from satori.application.assistant.mail_contracts import MailAccessError
from satori.desktop.assistant_home import AssistantHome
from satori.desktop.mail_controller import MailActionReceipt
from satori.desktop.mail_ui import MailPanel
from satori.desktop.source_status import SourceStatus
from satori.infrastructure.gmail.contracts import (
    GmailHeaderOmission,
    GmailInboxPage,
)
from tests.test_gmail_partial_catalog import connected as connected_adapter
from tests.test_gmail_partial_catalog import message as metadata_message
from tests.test_mail_controller import Harness, wait_until
from tests.test_mail_controller import app as app
from tests.test_mail_controller import harness as harness

MARKER = "SYNTHETIC-OMITTED-PRIVATE-VALUE"
PageKind = Literal["partial", "unsupported", "empty"]


def page_variant(page: GmailInboxPage, kind: PageKind) -> GmailInboxPage:
    retained = page.messages[:1] if kind == "partial" else ()
    omissions = (
        (GmailHeaderOmission(reason="metadata_header_value_controls", count=1 if retained else 2),)
        if kind in {"partial", "unsupported"}
        else ()
    )
    catalog = page.catalog.model_copy(
        update={"message_ids": tuple(message.source.message_id for message in retained)}
    )
    return GmailInboxPage(
        catalog=catalog,
        messages=retained,
        completeness=kind if kind != "empty" else "complete",
        omissions=omissions,
        more_available=True,
    )


def load(harness: Harness, kind: PageKind) -> None:
    harness.connect()
    harness.connector.mutate_page = lambda page: page_variant(page, kind)
    harness.controller.list_inbox()
    wait_until(harness.app, lambda: not harness.controller.busy)


@pytest.mark.parametrize(
    ("kind", "expected_receipt", "expected_catalog", "omitted"),
    [
        ("partial", "listed_partial", "partial", 1),
        ("unsupported", "headers_unsupported", "unsupported", 2),
        ("empty", "empty", "empty", 0),
    ],
)
def test_page_outcomes_are_typed_and_do_not_claim_a_body_read(
    harness: Harness, kind: PageKind, expected_receipt: str, expected_catalog: str, omitted: int
) -> None:
    load(harness, kind)
    controller = harness.controller
    assert controller.account is harness.connector.identity
    assert controller.last_receipt is not None
    assert controller.last_receipt.code == expected_receipt
    status = controller.source_status
    assert status.catalog_state == expected_catalog
    assert status.snapshot == "not_read"
    assert status.connection == "connected"
    assert sum(item.count for item in controller.header_omissions) == omitted
    assert controller.snapshot is None
    assert harness.connector.reads == []
    assert controller.more_available
    assert MARKER not in repr(asdict(controller.last_receipt))
    assert MARKER not in repr(list(controller.audit))


def test_partial_page_notice_survives_selection_and_complete_selected_body(
    harness: Harness,
) -> None:
    panel = MailPanel(harness.path, controller=harness.controller)
    try:
        load(harness, "partial")
        expected = harness.controller.header_omissions
        assert "Пропущено писем: 1" in panel.catalog_availability.text()
        harness.controller.selection_changed(("m1",))
        assert harness.controller.header_omissions == expected
        harness.controller.read_selected(("m1",))
        wait_until(harness.app, lambda: not harness.controller.busy)
        snapshot = harness.controller.snapshot
        assert snapshot is not None
        assert all(message.completeness == "complete" for message in snapshot.messages)
        assert harness.controller.last_receipt is not None
        assert harness.controller.last_receipt.code == "ready"
        assert harness.controller.source_status.snapshot == "fresh"
        assert harness.controller.source_status.catalog_state == "partial"
        assert harness.controller.header_omissions == expected
        assert "Пропущено писем: 1" in panel.catalog_availability.text()
    finally:
        panel.deleteLater()


def test_all_omitted_is_not_empty_and_has_no_selectable_rows(harness: Harness) -> None:
    panel = MailPanel(harness.path, controller=harness.controller)
    try:
        load(harness, "unsupported")
        assert panel.messages.count() == 0
        assert not panel.read_button.isEnabled()
        assert "не пустой INBOX" in panel.status.text()
        assert "Пропущено писем: 2" in panel.catalog_availability.text()
        assert "Есть ещё письма" in panel.selection.text()
        assert panel.list_button.isEnabled()
        assert not panel.connect_button.isEnabled()
        assert harness.connector.reads == []
    finally:
        panel.deleteLater()


@pytest.mark.parametrize("action", ["refresh", "cancel", "disconnect", "profile", "expiry"])
def test_metadata_omission_view_clears_on_lifecycle(harness: Harness, action: str) -> None:
    panel = MailPanel(harness.path, controller=harness.controller)
    try:
        load(harness, "partial")
        controller = harness.controller
        assert controller.header_omissions
        if action == "refresh":
            harness.connector.list_gate.clear()
            controller.list_inbox()
        elif action == "cancel":
            controller.cancel()
        elif action == "disconnect":
            controller.disconnect_account()
        elif action == "profile":
            controller.set_profile(harness.path / "other")
        else:
            harness.clock.advance(301)
            controller.poll()
        assert controller.header_omissions == ()
        assert controller.catalog_state is None
        assert panel.catalog_availability.text() == ""
    finally:
        harness.connector.list_gate.set()
        wait_until(harness.app, lambda: not harness.controller.busy)
        panel.deleteLater()


@pytest.mark.parametrize("action", ["cancel", "disconnect", "profile"])
def test_late_partial_page_cannot_repopulate_omissions(harness: Harness, action: str) -> None:
    harness.connect()
    connector = harness.connector
    connector.mutate_page = lambda page: page_variant(page, "partial")
    connector.list_gate.clear()
    connector.started.clear()
    harness.controller.list_inbox()
    wait_until(harness.app, connector.started.is_set)
    if action == "cancel":
        harness.controller.cancel()
    elif action == "disconnect":
        harness.controller.disconnect_account()
    else:
        harness.controller.set_profile(harness.path / "other")
    connector.list_gate.set()
    wait_until(harness.app, lambda: not harness.controller.busy)
    assert harness.controller.header_omissions == ()
    assert harness.controller.catalog_state is None
    assert harness.controller.headers == ()


def test_forged_page_coverage_is_revalidated_before_owner_admission(harness: Harness) -> None:
    harness.connect()
    harness.connector.mutate_page = lambda page: page.model_copy(
        update={"completeness": "unsupported"}
    )
    harness.controller.list_inbox()
    wait_until(harness.app, lambda: not harness.controller.busy)
    assert harness.controller.status == "response_invalid"
    assert harness.controller.headers == ()
    assert harness.controller.header_omissions == ()
    assert harness.controller._owner.catalog is None


@pytest.mark.parametrize("count", [True, False, "1", 1.0, 0, 21])
def test_nested_copy_forged_omission_count_is_revalidated(harness: Harness, count: object) -> None:
    harness.connect()
    page = harness.connector.list_inbox(cancel=Event())
    base = page_variant(page, "unsupported")
    omission = GmailHeaderOmission(reason="metadata_header_value_controls", count=1)
    forged = omission.model_copy(update={"count": count})
    with pytest.raises(ValidationError):
        GmailInboxPage(
            catalog=base.catalog,
            messages=(),
            more_available=False,
            completeness="unsupported",
            omissions=(forged,),
        )


def test_forged_summary_is_silently_rejected_at_delivery(
    harness: Harness, capsys: pytest.CaptureFixture[str]
) -> None:
    harness.connect()

    def forged(page: GmailInboxPage) -> GmailInboxPage:
        result = page_variant(page, "partial")
        bad = GmailHeaderOmission(reason="metadata_header_value_controls", count=1).model_copy(
            update={"count": MARKER}
        )
        return result.model_copy(update={"omissions": (bad,)})

    harness.connector.mutate_page = forged
    harness.controller.list_inbox()
    wait_until(harness.app, lambda: not harness.controller.busy)
    assert harness.controller.status == "response_invalid"
    assert harness.controller.header_omissions == ()
    assert harness.controller._owner.catalog is None
    captured = capsys.readouterr()
    assert MARKER not in captured.out
    assert MARKER not in captured.err


@pytest.mark.parametrize("code", ["listed_partial", "headers_unsupported"])
def test_partial_receipt_code_is_only_valid_for_list(code: str) -> None:
    assert MailActionReceipt("list", code, None).code == code
    with pytest.raises(ValueError, match="mail_receipt_invalid"):
        MailActionReceipt("read", code, None)


def test_later_healthy_row_is_not_permanently_blacklisted() -> None:
    connector, http = connected_adapter({"m1": metadata_message("m1", "blocked\x00")})
    omitted = connector.list_headers(cancel=Event())
    owner = MailAccessCoordinator(omitted.catalog.principal, clock=lambda: omitted.catalog.observed)
    owner.admit_catalog(omitted.catalog)
    with pytest.raises(MailAccessError, match="selection_invalid"):
        owner.select(("m1",), confirmed=True)
    http.rows["m1"] = metadata_message("m1", "fresh safe header")
    current = connector.list_headers(cancel=Event())
    assert current.completeness == "complete"
    assert current.omitted_count == 0
    assert current.catalog.message_ids == ("m1",)
    assert current.catalog.catalog_id != omitted.catalog.catalog_id
    owner.admit_catalog(current.catalog)
    selection = owner.select(("m1",), confirmed=True)
    owner.issue(selection, confirmed=True)
    assert owner.prepare_read().grant.selection.resources[0].message_id == "m1"
    assert len(http.calls) == 4


def test_home_distinguishes_empty_partial_and_unavailable_without_source_data(
    harness: Harness,
) -> None:
    value = SourceStatus(source="mail", connection="connected", catalog_state="empty")
    reads: list[str] = []

    def status() -> SourceStatus:
        reads.append("metadata")
        return value

    home = AssistantHome({"mail": status}, lambda _source: None, lambda: None)
    home._timer.stop()
    try:
        assert "страница входящих пуста" in home.status_labels["mail"].text()
        value = value.model_copy(update={"catalog_state": "partial"})
        home.refresh()
        assert "Список писем частичный" in home.status_labels["mail"].text()
        value = value.model_copy(update={"catalog_state": "unsupported"})
        home.refresh()
        assert "нет доступных заголовков" in home.status_labels["mail"].text()
        assert reads == ["metadata", "metadata", "metadata"]
        assert harness.instances == []
        value = value.model_copy(update={"connection": "disconnected"})
        home.refresh()
        assert home.status_labels["mail"].text() == "Не подключён"
        with pytest.raises(ValidationError):
            SourceStatus.model_validate(
                {"source": "mail", "connection": "connected", "catalog_state": MARKER}
            )
        value = value.model_copy(update={"catalog_state": MARKER})
        home.refresh()
        assert "недоступно" in home.status_labels["mail"].text()
        assert MARKER not in home.status_labels["mail"].text()
    finally:
        home.deleteLater()
