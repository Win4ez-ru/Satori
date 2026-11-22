"""Owned process integration with public synthetic source receipts and local test files."""

import json
import os
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from uuid import uuid4

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from satori.desktop.public_sources_controller import PublicSourcesController, public_source_command
from satori.desktop.ui import DesktopApplication


@pytest.fixture(scope="module")
def app() -> QApplication:
    application = QApplication.instance() or DesktopApplication([])
    assert isinstance(application, QApplication)
    return application


@pytest.fixture
def controller(app: QApplication, tmp_path: Path) -> Iterator[PublicSourcesController]:
    del app
    owner = PublicSourcesController(tmp_path)
    owner.set_visible(True)
    yield owner
    owner.shutdown()
    wait(lambda: owner.shutdown_complete)
    owner.deleteLater()
    QApplication.processEvents()


def wait(predicate: Callable[[], bool], seconds: float = 5) -> None:
    until = time.monotonic() + seconds
    while not predicate() and time.monotonic() < until:
        QApplication.processEvents()
        time.sleep(0.005)
    assert predicate()


def test_fixed_source_and_packaged_entrypoint_have_no_resource_or_secret_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert tuple(public_source_command()) == (
        sys.executable,
        "-m",
        "satori.desktop",
        "--public-source-worker",
    )
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert tuple(public_source_command()) == (sys.executable, "--public-source-worker")


def test_default_actual_python_entrypoint_reads_only_chosen_synthetic_file(
    controller: PublicSourcesController, tmp_path: Path
) -> None:
    path = tmp_path.resolve() / "PUBLIC selected.txt"
    path.write_text("PUBLIC SELECTED FILE BODY\n")
    path.chmod(0o600)
    assert controller.read_file(path)
    assert controller.result is None
    wait(lambda: not controller.busy)
    assert controller.status == "ready"
    result = controller.result
    assert result is not None
    assert result.operation == "file_read"
    assert result.origin == "selected_file"
    assert result.text == "PUBLIC SELECTED FILE BODY\n"
    assert result.source_label == path.name
    assert result.profile_id == controller.profile_id
    assert result.session_id == controller.session_id
    assert result.generation == controller.generation
    assert "PUBLIC SELECTED FILE BODY" not in repr(controller)
    assert "PUBLIC SELECTED FILE BODY" not in repr(result)
    controller.cancel()
    assert controller.result is None


@pytest.mark.parametrize("body", ["", "a" * (129 * 1024)])
def test_actual_helper_preserves_empty_and_partial_file_semantics(
    controller: PublicSourcesController, tmp_path: Path, body: str
) -> None:
    path = tmp_path.resolve() / "PUBLIC.txt"
    path.write_text(body)
    path.chmod(0o600)
    assert controller.read_file(path)
    wait(lambda: not controller.busy)
    result = controller.result
    assert result is not None
    assert result.status == ("partial" if body else "empty")
    assert controller.status == result.status
    assert len((result.text or "").encode()) <= 128 * 1024


def test_actual_helper_unsupported_file_is_negative_not_fabricated_read(
    controller: PublicSourcesController, tmp_path: Path
) -> None:
    path = tmp_path.resolve() / "PUBLIC.pdf"
    path.write_bytes(b"%PDF PUBLIC SYNTHETIC")
    assert controller.read_file(path)
    wait(lambda: not controller.busy)
    assert controller.status == "source_rejected"
    result = controller.result
    assert result is not None
    assert result.reason == "file_unsupported"
    assert result.text is None
    assert result.source_url is None


def test_stderr_is_discarded_and_arbitrary_helper_text_cannot_become_result(
    app: QApplication, tmp_path: Path
) -> None:
    del app
    helper = tmp_path / "synthetic_helper.py"
    helper.write_text(
        "import sys\n"
        "sys.stdin.buffer.read()\n"
        "sys.stderr.write('PRIVATE SYNTHETIC ERROR')\n"
        "sys.stdout.write('PRIVATE SYNTHETIC INVALID FRAME')\n"
    )
    owner = PublicSourcesController(tmp_path, helper_command=lambda: (sys.executable, str(helper)))
    owner.set_visible(True)
    try:
        assert owner.read_url("https://public.example.invalid/")
        wait(lambda: not owner.busy)
        assert owner.status == "response_invalid"
        assert owner.result is None
        assert owner.reason is None
        assert "PRIVATE" not in repr(owner)
    finally:
        owner.shutdown()
        wait(lambda: owner.shutdown_complete)
        owner.deleteLater()


def test_real_child_is_retained_through_cancel_and_async_shutdown(
    app: QApplication, tmp_path: Path
) -> None:
    del app
    helper = tmp_path / "synthetic_waiting_helper.py"
    helper.write_text("import sys,time\nsys.stdin.buffer.read()\ntime.sleep(20)\n")
    owner = PublicSourcesController(tmp_path, helper_command=lambda: (sys.executable, str(helper)))
    owner.set_visible(True)
    receipts: list[bool] = []
    owner.shutdown_finished.connect(lambda: receipts.append(owner.shutdown_complete))
    assert owner.read_url("https://public.example.invalid/")
    job = owner._job
    assert job is not None
    wait(lambda: job.process.processId() > 0)
    owner.shutdown()
    assert owner.result is None
    assert job.payload == b""
    assert not job.output
    assert not owner.read_url("https://public.example.invalid/another")
    wait(lambda: owner.shutdown_complete)
    assert receipts == [True]
    owner.shutdown()
    assert receipts == [True]
    owner.deleteLater()


def test_profile_and_session_identifiers_are_metadata_not_source_payload(
    controller: PublicSourcesController,
) -> None:
    assert controller.result is None
    assert controller.status == "idle"
    assert not controller.busy
    assert not controller.shutdown_complete
    assert isinstance(str(controller.profile_id), str)
    assert json.dumps({"status": controller.status}) == '{"status": "idle"}'


def test_stale_composition_failure_cannot_cancel_a_newer_owned_request(
    app: QApplication, tmp_path: Path
) -> None:
    del app
    helper = tmp_path / "synthetic_reply.py"
    helper.write_text(
        "import sys,json,datetime\n"
        "request=json.load(sys.stdin)\n"
        "now=datetime.datetime.now(datetime.UTC)\n"
        "keys=['schema_version','request_id','profile_id','session_id','generation','operation']\n"
        "reply={k:request[k] for k in keys}\n"
        "reply.update(untrusted=True,origin='public_https',coverage='selected_url_only',status='ok',"
        "source_label='PUBLIC',source_url=request['url'],media_type='text/plain',"
        "text='PUBLIC NEWER RESULT',"
        "raw_bytes=19,observed_at=now.isoformat(),expires_at=(now+datetime.timedelta(minutes=5)).isoformat())\n"
        "print(json.dumps(reply))\n"
    )
    nested = False
    owner: PublicSourcesController

    def command() -> tuple[str, ...]:
        nonlocal nested
        if not nested:
            nested = True
            assert owner.read_url("https://public.example.invalid/newer")
            raise RuntimeError("SYNTHETIC OLD COMPOSITION FAILURE")
        return sys.executable, str(helper)

    owner = PublicSourcesController(tmp_path, helper_command=command)
    owner.set_visible(True)
    try:
        assert not owner.read_url("https://public.example.invalid/older")
        wait(lambda: not owner.busy)
        result = owner.result
        assert result is not None
        assert result.source_url == "https://public.example.invalid/newer"
        assert result.text == "PUBLIC NEWER RESULT"
    finally:
        owner.shutdown()
        wait(lambda: owner.shutdown_complete)
        owner.deleteLater()


def test_reentrant_session_change_preserves_its_state_without_starting_old_helper(
    app: QApplication, tmp_path: Path
) -> None:
    del app
    owner: PublicSourcesController
    session = uuid4()

    def command() -> None:
        owner.bind_session(session)

    owner = PublicSourcesController(tmp_path, helper_command=command)
    owner.set_visible(True)
    assert not owner.read_url("https://public.example.invalid/older")
    assert owner.session_id == session
    assert owner.status == "session_changed"
    assert not owner.busy
    assert owner.result is None
    owner.shutdown()
    owner.deleteLater()
