"""Private metadata-only reply attempts; never replay an uncertain send after restart."""

import json
import os
import sqlite3
import stat
from datetime import datetime
from pathlib import Path
from uuid import UUID

from satori.application.assistant.contracts import require_utc
from satori.application.assistant.telegram_reply_contracts import (
    ReplyApproval,
    ReplyDenied,
    ReplyDraft,
    ReplyReceipt,
    scope_digest,
)


class SQLiteReplyJournal:
    """One application owner, atomic cross-connection claims, bounded fail-closed capacity."""

    def __init__(self, path: Path, *, create: bool = False, capacity: int = 512) -> None:
        if not path.is_absolute() or not 1 <= capacity <= 4096:
            raise ReplyDenied("reply_journal_configuration")
        for parent in (path.parent, *path.parent.parents):
            if parent.is_symlink():
                raise ReplyDenied("reply_journal_path")
        meta = path.parent.stat()
        if meta.st_uid != os.getuid() or stat.S_IMODE(meta.st_mode) != 0o700:
            raise ReplyDenied("reply_journal_directory")
        if create:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            os.close(descriptor)
        meta = path.lstat()
        if (
            not stat.S_ISREG(meta.st_mode)
            or path.is_symlink()
            or meta.st_nlink != 1
            or meta.st_uid != os.getuid()
            or stat.S_IMODE(meta.st_mode) != 0o600
        ):
            raise ReplyDenied("reply_journal_file")
        self._capacity = capacity
        self._connection = sqlite3.connect(path, timeout=2, isolation_level=None)
        self._connection.execute("PRAGMA journal_mode=DELETE")
        self._connection.execute("PRAGMA synchronous=FULL")
        if create:
            self._connection.execute(
                "CREATE TABLE reply_attempts (request_id TEXT PRIMARY KEY, receipt TEXT NOT NULL, "
                "admission TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            self._connection.execute("PRAGMA user_version=1")
        if self._connection.execute("PRAGMA user_version").fetchone() != (1,):
            self.close()
            raise ReplyDenied("reply_journal_schema")

    def close(self) -> None:
        self._connection.close()

    def get(self, request_id: UUID) -> ReplyReceipt | None:
        row = self._connection.execute(
            "SELECT receipt FROM reply_attempts WHERE request_id=?", (str(request_id),)
        ).fetchone()
        if row is None:
            return None
        try:
            receipt = ReplyReceipt.model_validate_json(row[0])
            if receipt.request_id != request_id:
                raise ReplyDenied("reply_journal_invalid")
            return receipt
        except ValueError:
            raise ReplyDenied("reply_journal_invalid") from None

    def claim(
        self, draft: ReplyDraft, approval: ReplyApproval, sending_id: int, at: datetime
    ) -> ReplyReceipt:
        at = require_utc(at)
        if (
            approval.request_id != draft.request_id
            or approval.payload_digest != draft.payload_digest
        ):
            raise ReplyDenied("reply_approval_binding")
        receipt = ReplyReceipt(
            request_id=draft.request_id,
            payload_digest=draft.payload_digest,
            state="unknown",
            sending_id=sending_id,
            code="dispatch_claimed",
        )
        connection = self._connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            if self.get(draft.request_id) is not None:
                raise ReplyDenied("reply_already_attempted")
            if (
                connection.execute("SELECT count(*) FROM reply_attempts").fetchone()[0]
                >= self._capacity
            ):
                raise ReplyDenied("reply_journal_capacity")
            connection.execute(
                "INSERT INTO reply_attempts VALUES (?, ?, ?, ?)",
                (
                    str(draft.request_id),
                    receipt.model_dump_json(),
                    json.dumps(
                        {
                            "operation": "telegram.reply_text",
                            "authority": "explicit_review",
                            "approval": approval.model_dump(mode="json"),
                            "principal": draft.scope.principal.model_dump(mode="json"),
                            "scope_sha256": scope_digest(draft.scope),
                            "claimed_at": at.isoformat(),
                        },
                        sort_keys=True,
                    ),
                    at.isoformat(),
                ),
            )
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        return receipt

    def record(self, receipt: ReplyReceipt, at: datetime) -> ReplyReceipt:
        at = require_utc(at)
        connection = self._connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            previous = self.get(receipt.request_id)
            if (
                previous is None
                or previous.payload_digest != receipt.payload_digest
                or previous.sending_id != receipt.sending_id
                or previous.state != "unknown"
            ):
                raise ReplyDenied("reply_receipt_conflict")
            connection.execute(
                "UPDATE reply_attempts SET receipt=?, updated_at=? WHERE request_id=?",
                (receipt.model_dump_json(), at.isoformat(), str(receipt.request_id)),
            )
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        return receipt
