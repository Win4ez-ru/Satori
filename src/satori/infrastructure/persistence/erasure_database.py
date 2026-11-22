"""Open existing rollback-mode SQLite without creating database or journal files."""

import os
import stat
from pathlib import Path
from sqlite3 import Connection as SQLiteConnection
from sqlite3 import connect
from urllib.parse import quote

from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.persistence.database import Database


def open_existing_erasure_database(database_url: str, *, writable: bool = False) -> Database:
    url = make_url(database_url)
    if (
        url.drivername not in {"sqlite", "sqlite+pysqlite"}
        or not url.database
        or url.database == ":memory:"
        or url.database.startswith("file:")
        or url.query
    ):
        raise SourcePolicyError("erasure_requires_file_sqlite")
    candidate = Path(url.database).expanduser().absolute()
    path = candidate.parent.resolve() / candidate.name
    try:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            raise SourcePolicyError("erasure_database_not_regular")
    except OSError:
        raise SourcePolicyError("erasure_database_unavailable") from None
    inode = (metadata.st_dev, metadata.st_ino)
    mode = "rw" if writable else "ro"
    sqlite_uri = f"file:{quote(str(path), safe='/')}?mode={mode}"

    def guarded_connection() -> SQLiteConnection:
        # mode=ro still creates missing SHM for some WAL databases. Check before
        # sqlite3.connect, rather than an SQLAlchemy connect event that is too late.
        # immutable=1 is unsuitable: it silently ignores committed WAL content.
        try:
            current = path.lstat()
            if not stat.S_ISREG(current.st_mode) or (current.st_dev, current.st_ino) != inode:
                raise SourcePolicyError("erasure_database_replaced")
            with path.open("rb") as database_file:
                header = database_file.read(20)
            sidecars = (Path(f"{path}{suffix}") for suffix in ("-wal", "-shm", "-journal"))
            if header[18:20] != b"\x01\x01" or any(
                os.path.lexists(sidecar) for sidecar in sidecars
            ):
                raise SourcePolicyError("erasure_database_journal_unsupported")
        except OSError:
            raise SourcePolicyError("erasure_database_unavailable") from None
        return connect(sqlite_uri, uri=True)

    engine = create_engine(
        url.set(drivername="sqlite+pysqlite", database=str(path)), creator=guarded_connection
    )

    @event.listens_for(engine, "connect")
    def read_only_connection(connection: SQLiteConnection, _record: object) -> None:
        if not writable:
            connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA foreign_keys=ON")

    return Database(
        engine=engine,
        session_factory=sessionmaker(bind=engine, class_=Session, autoflush=False),
    )
