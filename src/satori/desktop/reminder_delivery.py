"""Bounded native helper process; only an exact readback can confirm OS scheduling."""

import json
import plistlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from satori.application.assistant.reminder_contracts import (
    ReminderDeliveryReceipt,
    ReminderDeliveryRequest,
)

_BUNDLE_ID = "ai.satori.desktop.reminder-notifications"
_BINARY = "SatoriReminderNotifications"
_LIMIT = 16384
_AUTH_STATES = {"authorized", "denied", "not_determined", "unavailable", "unknown"}


@dataclass
class _Attempt:
    process: QProcess
    timer: QTimer
    payload: bytes
    request: ReminderDeliveryRequest | None
    operation: str
    complete: Callable[[ReminderDeliveryReceipt], None] | None
    output: bytearray = field(default_factory=bytearray)
    overflow: bool = False


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("notification_reply_invalid")
        result[key] = value
    return result


class NativeReminderDeliveryPort(QObject):
    """No subprocess or authorization prompt on construction. Root owns this adapter."""

    authorization_changed = Signal(str)
    shutdown_finished = Signal()

    def __init__(self, helper_app: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.helper_app = helper_app
        self.status = "unknown"
        self._attempts: dict[UUID, _Attempt] = {}
        self._retiring: set[QProcess] = set()
        self._closed = False
        self._shutdown_emitted = False

    @property
    def shutdown_complete(self) -> bool:
        return self._closed and not self._attempts and not self._retiring

    def _program(self) -> str:
        if not self.helper_app.is_absolute() or self.helper_app.suffix != ".app":
            raise ValueError("notification_helper_invalid")
        if self.helper_app.is_symlink():
            raise ValueError("notification_helper_invalid")
        contents = self.helper_app / "Contents"
        info_path = contents / "Info.plist"
        program = contents / "MacOS" / _BINARY
        if info_path.is_symlink() or program.is_symlink():
            raise ValueError("notification_helper_invalid")
        if info_path.stat().st_size > _LIMIT:
            raise ValueError("notification_helper_invalid")
        with info_path.open("rb") as source:
            info = plistlib.load(source)
        if (
            info.get("CFBundleIdentifier") != _BUNDLE_ID
            or info.get("CFBundleExecutable") != _BINARY
        ):
            raise ValueError("notification_helper_invalid")
        if not program.is_file():
            raise ValueError("notification_helper_invalid")
        return str(program)

    def authorize(self) -> None:
        """Only an explicit settings click calls this OS permission request."""
        self._start("authorize", None, None)

    def refresh_authorization(self) -> None:
        self._start("status", None, None)

    def submit(
        self, request: ReminderDeliveryRequest, complete: Callable[[ReminderDeliveryReceipt], None]
    ) -> None:
        request = ReminderDeliveryRequest.model_validate_json(request.model_dump_json())
        self._start(request.operation, request, complete)

    def _start(
        self,
        operation: str,
        request: ReminderDeliveryRequest | None,
        complete: Callable[[ReminderDeliveryReceipt], None] | None,
    ) -> None:
        if self._closed or len(self._attempts) >= 8:
            self._failed(request, complete)
            return
        try:
            program = self._program()
        except (OSError, ValueError, plistlib.InvalidFileException):
            self._failed(request, complete)
            return
        payload = (
            request.model_dump_json().encode()
            if request is not None
            else json.dumps({"operation": operation}).encode()
        ) + b"\n"
        process, timer = QProcess(self), QTimer(self)
        timer.setSingleShot(True)
        key = uuid4()
        attempt = _Attempt(process, timer, payload, request, operation, complete)
        self._attempts[key] = attempt
        process.setProgram(program)
        process.setArguments([])
        process.started.connect(lambda: self._write(key))
        process.readyReadStandardOutput.connect(lambda: self._read(key))
        process.readyReadStandardError.connect(lambda: self._discard_error(key))
        process.finished.connect(lambda code, status: self._finish(key, code, status))
        process.errorOccurred.connect(lambda _error: self._process_error(key, process))
        timer.timeout.connect(lambda: self._finish(key, -1, None))
        timer.start(125000 if operation == "authorize" else 12000)
        process.start()

    def _write(self, key: UUID) -> None:
        attempt = self._attempts.get(key)
        if attempt is not None:
            if attempt.process.write(attempt.payload) != len(attempt.payload):
                self._finish(key, -1, None)
            else:
                attempt.process.closeWriteChannel()

    def _read(self, key: UUID) -> None:
        attempt = self._attempts.get(key)
        if attempt is None:
            return
        chunk = attempt.process.readAllStandardOutput().data()
        if len(attempt.output) + len(chunk) > _LIMIT:
            attempt.overflow = True
            self._finish(key, -1, None)
        else:
            attempt.output.extend(chunk)

    def _discard_error(self, key: UUID) -> None:
        attempt = self._attempts.get(key)
        if attempt is not None:
            attempt.process.readAllStandardError()

    def _process_error(self, key: UUID, process: QProcess) -> None:
        self._finish(key, -1, None)
        if process in self._retiring and process.state() == QProcess.ProcessState.NotRunning:
            self._retire(process)

    def _failed(
        self,
        request: ReminderDeliveryRequest | None,
        complete: Callable[[ReminderDeliveryReceipt], None] | None,
    ) -> None:
        if request is not None and complete is not None:
            complete(
                ReminderDeliveryReceipt(
                    attempt_id=request.attempt_id,
                    reminder_id=request.reminder_id,
                    operation=request.operation,
                    notification_identifier=request.notification_identifier,
                    payload_digest=request.payload_digest,
                    status="unknown",
                    observed_at=datetime.now(UTC),
                    platform="local_watchdog",
                )
            )
        else:
            self.status = "unavailable"
            self.authorization_changed.emit(self.status)

    def _finish(self, key: UUID, code: int, status: object) -> None:
        attempt = self._attempts.pop(key, None)
        if attempt is None:
            return
        attempt.timer.stop()
        remainder = attempt.process.readAllStandardOutput().data()
        if len(attempt.output) + len(remainder) > _LIMIT:
            attempt.overflow = True
        else:
            attempt.output.extend(remainder)
        if attempt.process.state() != QProcess.ProcessState.NotRunning:
            self._retiring.add(attempt.process)
            attempt.process.finished.connect(lambda _code, _status: self._retire(attempt.process))
            attempt.process.kill()
        else:
            attempt.process.deleteLater()
        attempt.timer.deleteLater()
        try:
            if code != 0 or status != QProcess.ExitStatus.NormalExit or attempt.overflow:
                raise ValueError("notification_reply_unconfirmed")
            value = json.loads(bytes(attempt.output), object_pairs_hook=_unique_object)
            if not isinstance(value, dict):
                raise ValueError("notification_reply_invalid")
            if attempt.request is None:
                if set(value) != {"operation", "status", "observed_at"}:
                    raise ValueError("notification_reply_invalid")
                if value["operation"] != attempt.operation or value["status"] not in _AUTH_STATES:
                    raise ValueError("notification_reply_invalid")
                self.status = value["status"]
                self.authorization_changed.emit(self.status)
            else:
                receipt = ReminderDeliveryReceipt.model_validate_json(json.dumps(value))
                expected = attempt.request
                if (
                    receipt.attempt_id != expected.attempt_id
                    or receipt.reminder_id != expected.reminder_id
                    or receipt.operation != expected.operation
                    or receipt.notification_identifier != expected.notification_identifier
                    or receipt.payload_digest != expected.payload_digest
                    or receipt.platform != "native_os"
                ):
                    raise ValueError("notification_reply_stale")
                if attempt.complete is not None:
                    attempt.complete(receipt)
        except (ValueError, TypeError, KeyError):
            self._failed(attempt.request, attempt.complete)
        self._maybe_finished()

    def _retire(self, process: QProcess) -> None:
        self._retiring.discard(process)
        process.deleteLater()
        self._maybe_finished()

    def _maybe_finished(self) -> None:
        if self.shutdown_complete and not self._shutdown_emitted:
            self._shutdown_emitted = True
            self.shutdown_finished.emit()

    def shutdown(self) -> None:
        self._closed = True
        for key in tuple(self._attempts):
            self._finish(key, -1, None)
        self._maybe_finished()
