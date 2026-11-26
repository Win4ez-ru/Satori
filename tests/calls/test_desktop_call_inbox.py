"""Local call delivery and actual Desktop restarts against disposable owner profiles."""

import asyncio
import json
import os
import stat
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest

from satori.application.calls.intake import CallEndReceipt, CallIntakeError
from satori.infrastructure.calls.audiosocket import AudioSocketServer
from satori.infrastructure.calls.busy import ACKNOWLEDGEMENT, GREETING
from satori.infrastructure.calls.inbox import CallInbox
from tests.busy_call_support import FixtureIntakeAuthority, FixtureSpeech
from tests.calls.test_audiosocket import Admission, connect, eof, frame, grant, settled

MESSAGE = "Илья, доставка будет в восемь."
CALL = uuid5(NAMESPACE_URL, "synthetic delivery call")
ACCOUNT = uuid5(NAMESPACE_URL, "synthetic provider account")
ROUTE = uuid5(NAMESPACE_URL, "synthetic busy route")


async def deliver(
    inbox: CallInbox, *, call: UUID = CALL, text: str = MESSAGE, lost: bool = False
) -> None:
    audio = grant(principal=inbox.principal)
    binding = audio.binding.model_copy(
        update={"route_id": ROUTE, "call_id": call, "provider_account_ref": ACCOUNT}
    )
    audio = audio.model_copy(update={"binding": binding})
    speech = FixtureSpeech()
    auth = FixtureIntakeAuthority(audio)
    handler = inbox.busy_handler(auth.grant, authority=auth, speech=speech)
    server = AudioSocketServer(Admission(audio), handler)
    await server.start(enabled=True)
    try:
        reader, writer = await connect(server)

        async def hear() -> str:
            header = await asyncio.wait_for(reader.readexactly(3), 2)
            assert header[0] == 0x10
            return speech.recognize(await reader.readexactly(int.from_bytes(header[1:], "big")))

        try:
            writer.write(frame(1, audio.stream_id.bytes))
            await writer.drain()
            assert await hear() == GREETING
            writer.write(frame(0x10, speech.render(text)))
            await writer.drain()
            assert await hear() == ACKNOWLEDGEMENT
            if lost:
                writer.transport.abort()
            else:
                writer.write(frame(0))
                await writer.drain()
                await eof(reader)
        finally:
            writer.close()
            await writer.wait_closed()
        await settled(server)
    finally:
        await server.close()
    receipt = CallEndReceipt(
        binding=binding, receipt_id=uuid4(), observed_at=datetime.now(UTC), status="confirmed_ended"
    )
    if lost:
        with pytest.raises(CallIntakeError, match="session_stopped"):
            handler.accept_call_end(receipt)
    else:
        handler.accept_call_end(receipt)
        handler.accept_call_end(receipt)


def desktop_visit(profile: Path, phase: str) -> None:
    """Used by separate interpreters, with normal sidebar navigation and no Core start."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from satori.desktop.call_inbox_ui import CallInboxDialog
    from satori.desktop.settings import DesktopPreferences
    from satori.desktop.ui import DesktopApplication, DesktopWindow

    app = DesktopApplication([])
    window = DesktopWindow(DesktopPreferences(data_directory=profile, repository_directory=profile))
    window.poll.stop()
    window.show()
    app.processEvents()
    if phase == "deliver":
        asyncio.run(deliver(window.call_inbox))
    result: dict[str, object] = {"session": str(window.call_inbox.principal.session_id)}

    def inspect() -> None:
        dialog = QApplication.activeModalWidget()
        if isinstance(dialog, CallInboxDialog):
            result["count"] = dialog.calls.count()
            result["text"] = dialog.message.toPlainText()
            dialog.accept()

    if phase != "preview":
        QTimer.singleShot(0, inspect)
    window.workspace.call_inbox_button.click()
    if phase != "preview":
        result["records"] = [r.model_dump(mode="json") for r in window.call_inbox.entries()]
        print(json.dumps(result, ensure_ascii=False))
    window.quitting = True
    window.tray.hide()
    window.close()


def test_desktop_restart_and_replayed_call(tmp_path: Path) -> None:
    def visit(profile: Path, phase: str) -> dict[str, object]:
        process = subprocess.run(
            [sys.executable, "-m", __name__, str(profile), phase],
            capture_output=True,
            text=True,
            timeout=25,
            check=True,
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        )
        return dict(json.loads(process.stdout))

    owner = tmp_path / "owner"
    first = visit(owner, "deliver")
    restarted = visit(owner, "read")
    replay = visit(owner, "deliver")
    assert len({str(p["session"]) for p in (first, restarted, replay)}) == 3
    for result in (first, restarted, replay):
        assert (result["count"], result["text"]) == (1, MESSAGE)
        assert result["records"] == first["records"]
    other = visit(tmp_path / "other", "read")
    assert (other["count"], other["text"], other["records"]) == (0, "", [])


def test_abort_distinct_calls_and_conflicting_replay(tmp_path: Path) -> None:
    inbox = CallInbox(tmp_path)
    asyncio.run(deliver(inbox, lost=True))
    assert inbox.entries() == []
    asyncio.run(deliver(inbox))
    before = inbox.entries()
    with pytest.raises(CallIntakeError, match="inbox_call_conflict"):
        asyncio.run(deliver(inbox, text="Содержание другой сводки."))
    assert inbox.entries() == before
    asyncio.run(deliver(inbox, call=uuid4()))
    assert [entry.text for entry in inbox.entries()] == [MESSAGE, MESSAGE]
    directory = tmp_path / "call-inbox"
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in directory.iterdir())


def test_concurrent_deliveries_share_one_record(tmp_path: Path) -> None:
    def receive() -> None:
        asyncio.run(deliver(CallInbox(tmp_path)))

    with ThreadPoolExecutor(max_workers=2) as workers:
        runs = [workers.submit(receive) for _ in range(2)]
        for run in runs:
            run.result(timeout=10)
    assert [item.text for item in CallInbox(tmp_path).entries()] == [MESSAGE]


def test_inbox_checks_profile_and_private_storage(tmp_path: Path) -> None:
    owner, other = (CallInbox(tmp_path / who) for who in ("owner", "other"))
    auth = FixtureIntakeAuthority(grant(principal=owner.principal))
    with pytest.raises(CallIntakeError, match="review_owner_mismatch"):
        other.busy_handler(auth.grant, authority=auth, speech=FixtureSpeech())
    asyncio.run(deliver(owner))
    folder = tmp_path / "owner" / "call-inbox"
    (item,) = folder.iterdir()
    item.chmod(0o644)
    with pytest.raises(CallIntakeError, match="inbox_file_not_private"):
        owner.entries()
    item.chmod(0o600)
    other_folder = tmp_path / "other" / "call-inbox"
    other_folder.parent.mkdir(mode=0o700)
    other_folder.symlink_to(folder)
    with pytest.raises(CallIntakeError, match="inbox_directory_not_private"):
        other.entries()
    other_folder.unlink()
    other_folder.mkdir(mode=0o700)
    copied = other_folder / item.name
    copied.write_bytes(item.read_bytes())
    copied.chmod(0o600)
    with pytest.raises(CallIntakeError, match="inbox_record_mismatch"):
        other.entries()


if __name__ == "__main__":
    desktop_visit(Path(sys.argv[1]).resolve(), sys.argv[2])
