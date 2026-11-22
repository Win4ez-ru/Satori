"""Readable selected-mail view keeps raw headers opt-in and clears both surfaces."""

# ruff: noqa: RUF001 - intentional Russian user-facing text assertions

import base64
import json
import os
from pathlib import Path
from typing import Literal

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from satori.application.assistant.mail_contracts import MailReadResult
from satori.desktop.mail_ui import MailPanel
from tests.test_mail_ui import ACCOUNT, SyntheticController, _clock
from tests.test_mail_ui import app as app
from tests.test_mail_ui import view as view


def _publish(controller: SyntheticController, *, count: int = 1, metadata: bool = True) -> None:
    ids = tuple(f"m{index}" for index in range(count))
    proof = controller.owner.select(ids, confirmed=True)
    controller.owner.issue(proof, confirmed=True)
    request = controller.owner.prepare_read()
    messages = []
    for index, message_id in enumerate(ids):
        body = f'SYNTHETIC BODY {index} <img src="https://untrusted.invalid/no-fetch">'
        headers = [
            {"name": "Received", "value": "SYNTHETIC PRIVATE TRANSPORT"},
            {"name": "DKIM-Signature", "value": "SYNTHETIC SIGNATURE CLAIM"},
        ]
        if metadata:
            headers.extend(
                [
                    {"name": "Subject", "value": f"SYNTHETIC SUBJECT {index}"},
                    {"name": "From", "value": "SYNTHETIC SENDER <sender@example.test>"},
                    {"name": "Date", "value": "Tue, 20 Oct 2026 12:00:00 +0000"},
                ]
            )
        messages.append(
            json.dumps(
                {
                    "id": message_id,
                    "payload": {
                        "mimeType": "text/plain",
                        "headers": headers,
                        "body": {
                            "size": len(body.encode()),
                            "data": base64.urlsafe_b64encode(body.encode()).decode().rstrip("="),
                        },
                    },
                }
            ).encode()
        )
    controller.snapshot = controller.owner.accept_result(
        request,
        MailReadResult(
            request_id=request.request_id,
            principal=controller.principal,
            account=ACCOUNT,
            generation=proof.generation,
            selection_revision=proof.revision,
            observed=_clock(),
            messages=tuple(messages),
        ),
    )
    controller.status = "ready"
    controller.changed.emit()


def test_default_reading_view_shows_metadata_body_and_no_transport_dump(
    view: tuple[MailPanel, SyntheticController, Path],
) -> None:
    panel, controller, _ = view
    controller.catalog_ready()
    _publish(controller)
    text = panel.preview.toPlainText()
    assert "SYNTHETIC SUBJECT 0" in text
    assert "От: SYNTHETIC SENDER <sender@example.test>" in text
    assert "Дата: Tue, 20 Oct 2026 12:00:00 +0000" in text
    assert 'SYNTHETIC BODY 0 <img src="https://untrusted.invalid/no-fetch">' in text
    assert "SYNTHETIC PRIVATE TRANSPORT" not in text
    assert "SYNTHETIC SIGNATURE CLAIM" not in text
    assert "Полнота текстовой проекции" not in text
    assert panel.details_view.isHidden()
    assert not panel.details_toggle.isChecked()
    assert panel.details_view.isReadOnly()
    assert panel.preview.isReadOnly()
    panel.details_toggle.click()
    assert not panel.details_view.isHidden()
    details = panel.details_view.toPlainText()
    assert "received: SYNTHETIC PRIVATE TRANSPORT" in details
    assert "dkim-signature: SYNTHETIC SIGNATURE CLAIM" in details
    assert "SYNTHETIC BODY" not in details
    assert controller.connects == []
    assert controller.lists == 0
    assert controller.reads == []


def test_details_stay_open_for_same_snapshot_but_close_for_fresh_read(
    view: tuple[MailPanel, SyntheticController, Path],
) -> None:
    panel, controller, _ = view
    controller.catalog_ready()
    _publish(controller)
    panel.details_toggle.click()
    controller.changed.emit()
    assert panel.details_toggle.isChecked()
    assert not panel.details_view.isHidden()
    before = controller.snapshot
    assert before is not None
    _publish(controller)
    assert controller.snapshot is not None
    assert controller.snapshot.snapshot_id != before.snapshot_id
    assert not panel.details_toggle.isChecked()
    assert panel.details_view.isHidden()
    assert "Показать" in panel.details_toggle.text()


ClearEvent = Literal["selection", "cancel", "disconnect", "profile", "expiry", "login", "shutdown"]


@pytest.mark.parametrize(
    "event", ["selection", "cancel", "disconnect", "profile", "expiry", "login", "shutdown"]
)
def test_scope_loss_clears_reading_and_expanded_service_data(
    view: tuple[MailPanel, SyntheticController, Path], event: ClearEvent, tmp_path: Path
) -> None:
    panel, controller, _ = view
    controller.catalog_ready()
    _publish(controller)
    panel.details_toggle.click()
    assert panel.details_view.toPlainText()
    if event == "selection":
        controller.selection_changed(())
    elif event == "cancel":
        panel.cancel()
    elif event == "disconnect":
        controller.disconnect_account()
    elif event == "profile":
        panel.set_profile(tmp_path / "other-synthetic-profile")
    elif event == "expiry":
        controller.snapshot = None
        controller.status = "snapshot_expired"
        controller.changed.emit()
    elif event == "login":
        controller.connection_override = "expired"
        controller.changed.emit()
    else:
        panel.shutdown()
    assert panel.preview.toPlainText() == ""
    assert panel.details_view.toPlainText() == ""
    assert panel.details_view.isHidden()
    assert panel.details_toggle.isHidden()
    assert not panel.details_toggle.isChecked()
    assert not panel.details_toggle.isEnabled()
    assert controller.reads == []
    assert controller.lists == 0


def test_missing_display_headers_use_neutral_fallbacks(
    view: tuple[MailPanel, SyntheticController, Path],
) -> None:
    panel, controller, _ = view
    controller.catalog_ready()
    _publish(controller, metadata=False)
    text = panel.preview.toPlainText()
    assert "(Без темы)" in text
    assert "От: (не указан)" in text
    assert "Дата: (не указана)" in text
    assert "SYNTHETIC BODY" in text
    assert "SYNTHETIC PRIVATE TRANSPORT" not in text


def test_multiple_messages_keep_order_and_legacy_diagnostic_helper(
    view: tuple[MailPanel, SyntheticController, Path],
) -> None:
    panel, controller, _ = view
    controller.catalog_ready()
    _publish(controller, count=2)
    text = panel.preview.toPlainText()
    assert text.index("SYNTHETIC SUBJECT 0") < text.index("SYNTHETIC SUBJECT 1")
    assert text.count("Выбранное письмо") == 2
    assert panel.details_view.toPlainText().count("received: SYNTHETIC PRIVATE TRANSPORT") == 2
    assert controller.snapshot is not None
    legacy = panel._snapshot_text(controller.snapshot)
    assert "Источник: Gmail / выбранное письмо" in legacy
    assert "received: SYNTHETIC PRIVATE TRANSPORT" in legacy
    assert "\n\nТекст:\nSYNTHETIC BODY" in legacy
    assert controller.reads == []
