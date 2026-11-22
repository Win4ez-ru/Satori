"""Owned bounded QProcess transport. The root supplies a qualified packaged helper."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from .contracts import (
    OUTPUT_CAP,
    CancelCommand,
    CloseCommand,
    Command,
    LineFramer,
    PlacesRejected,
    RouteCommand,
    SearchCommand,
    SearchInput,
    decode_event,
    encode_command,
)


class PlacesPort(QObject):
    """Injectable Qt edge; fake implementations need no process or source access."""

    event_received = Signal(object)
    failed = Signal(str)
    stopped = Signal()

    @property
    def available(self) -> bool:
        return False

    def start_search(self, request: SearchInput) -> bool:
        raise NotImplementedError

    def route(self, command: RouteCommand) -> bool:
        raise NotImplementedError

    def cancel(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class QProcessPort(PlacesPort):
    """No helper discovery or subprocess until start_search. No automatic restart/replay."""

    def __init__(self, helper_path: Path | None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._helper_path = helper_path
        self._process: QProcess | None = None
        self._framer = LineFramer()
        self._queued: bytes | None = None
        self._closing = False

    @property
    def available(self) -> bool:
        # Root qualification owns packaging/parity/signature. No startup stat or fallback.
        return self._helper_path is not None

    def _path(self) -> str | None:
        path = self._helper_path
        if path is None or not path.is_absolute():
            return None
        try:
            if path.is_symlink() or not path.is_file() or not os.access(path, os.X_OK):
                return None
            return str(path.resolve(strict=True))
        except OSError:
            return None

    def start_search(self, request: SearchInput) -> bool:
        try:
            frame = encode_command(SearchCommand(search=request))
        except PlacesRejected:
            return False
        if self._process is not None:
            if self._closing or self._process.state() != QProcess.ProcessState.Running:
                return False
            return self._write(frame)
        path = self._path()
        if path is None:
            return False
        process = QProcess(self)
        self._process = process
        self._queued = frame
        self._closing = False
        self._framer = LineFramer()
        process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        process.setStandardErrorFile(QProcess.nullDevice())
        process.started.connect(self._on_started)
        process.readyReadStandardOutput.connect(self._on_read)
        process.errorOccurred.connect(self._on_error)
        process.finished.connect(self._on_finished)
        process.start(path, ["--stdio"])
        return True

    def _on_started(self) -> None:
        process = self.sender()
        if isinstance(process, QProcess):
            self._started(process)

    def _on_read(self) -> None:
        process = self.sender()
        if isinstance(process, QProcess):
            self._read(process)

    def _on_error(self, _error: QProcess.ProcessError) -> None:
        process = self.sender()
        if isinstance(process, QProcess):
            self._error(process)

    def _on_finished(self, _code: int, _status: QProcess.ExitStatus) -> None:
        process = self.sender()
        if isinstance(process, QProcess):
            self._finished(process)

    def _started(self, process: QProcess) -> None:
        if process is not self._process or self._closing:
            return
        frame, self._queued = self._queued, None
        if frame is not None and not self._write(frame):
            self._fail("helper_write_failed")

    def _write(self, frame: bytes) -> bool:
        process = self._process
        return (
            process is not None
            and not self._closing
            and process.state() == QProcess.ProcessState.Running
            and process.bytesToWrite() == 0
            and process.write(frame) == len(frame)
        )

    def _send(self, command: Command) -> bool:
        try:
            return self._write(encode_command(command))
        except PlacesRejected:
            return False

    def route(self, command: RouteCommand) -> bool:
        return self._send(command)

    def cancel(self) -> None:
        self._queued = None
        process = self._process
        if process is None or self._closing:
            return
        if process.state() == QProcess.ProcessState.Starting:
            self.close()
        elif not self._send(CancelCommand()):
            self._fail("helper_write_failed")

    def close(self) -> None:
        self._queued = None
        process = self._process
        if process is None:
            self.stopped.emit()
            return
        if self._closing:
            return
        self._send(CloseCommand())
        self._closing = True
        process.closeWriteChannel()
        # Never block the main event loop or wait indefinitely for native shutdown.
        QTimer.singleShot(500, lambda p=process: self._terminate(p))
        QTimer.singleShot(1000, lambda p=process: self._kill(p))

    def _terminate(self, process: QProcess) -> None:
        if process is self._process and process.state() != QProcess.ProcessState.NotRunning:
            process.terminate()

    def _kill(self, process: QProcess) -> None:
        if process is self._process and process.state() != QProcess.ProcessState.NotRunning:
            process.kill()

    def _read(self, process: QProcess) -> None:
        if process is not self._process or self._closing:
            return
        try:
            if process.bytesAvailable() > OUTPUT_CAP + 65536:
                raise PlacesRejected("output_size")
            while process.bytesAvailable() > 0:
                chunk = bytes(process.read(min(65536, process.bytesAvailable())).data())
                for frame in self._framer.feed(chunk):
                    event = decode_event(frame)
                    self.event_received.emit(event)
                    if process is not self._process or self._closing:
                        return
        except PlacesRejected:
            self._fail("source_rejected")

    def _error(self, process: QProcess) -> None:
        if process is self._process and not self._closing:
            self._fail("helper_failed")

    def _fail(self, code: str) -> None:
        if self._closing:
            return
        self._queued = None
        self._framer = LineFramer()
        self._closing = True
        process = self._process
        if process is not None:
            if process.state() == QProcess.ProcessState.NotRunning:
                QTimer.singleShot(0, lambda p=process: self._finished(p))
            else:
                process.kill()
        self.failed.emit(code)

    def _finished(self, process: QProcess) -> None:
        if process is not self._process:
            return
        if not self._closing:
            self._read(process)
            if process is not self._process:
                return
        unexpected = not self._closing
        if unexpected:
            try:
                self._framer.eof()
            except PlacesRejected:
                self.failed.emit("source_rejected")
        self._process = None
        self._queued = None
        self._framer = LineFramer()
        self._closing = False
        process.deleteLater()
        if unexpected:
            self.failed.emit("helper_disconnected")
        self.stopped.emit()
