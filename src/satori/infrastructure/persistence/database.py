"""SQLAlchemy engine and session-factory construction."""

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from sqlite3 import Connection as SQLiteConnection

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker


def _sqlite_file_path(database_url: str) -> Path | None:
    """Return the local SQLite file path, excluding memory/non-SQLite targets."""

    url = make_url(database_url)
    if not url.drivername.startswith("sqlite"):
        return None
    database = url.database
    if database is None or database == ":memory:":
        return None
    if database.startswith("file::memory:") or (
        database.startswith("file:") and url.query.get("mode") == "memory"
    ):
        return None
    candidate = Path(database).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    return candidate.parent.resolve() / candidate.name


def _ensure_private_parent(parent: Path) -> None:
    """Create missing parents privately without changing existing directory modes."""

    missing: list[Path] = []
    candidate = parent
    while not candidate.exists():
        missing.append(candidate)
        if candidate == candidate.parent:
            break
        candidate = candidate.parent
    for directory in reversed(missing):
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            continue
        os.chmod(directory, 0o700)


def _secure_regular_file(path: Path) -> None:
    """Create or tighten one local artifact without following a final symlink."""

    try:
        current = path.lstat()
    except FileNotFoundError:
        current = None
    if current is not None and not stat.S_ISREG(current.st_mode):
        raise OSError(f"refusing non-regular SQLite database path: {path}")

    no_follow = getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | no_follow,
            0o600,
        )
    except FileExistsError:
        descriptor = os.open(path, os.O_RDONLY | no_follow)
    try:
        opened = os.fstat(descriptor)
        observed = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(opened.st_mode) or (
            opened.st_dev,
            opened.st_ino,
        ) != (observed.st_dev, observed.st_ino):
            raise OSError(f"refusing non-regular SQLite database path: {path}")
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)


def ensure_sqlite_parent(database_url: str) -> None:
    """Prepare a private regular file for a file-backed SQLite database."""

    database_path = _sqlite_file_path(database_url)
    if database_path is None:
        return
    _ensure_private_parent(database_path.parent)
    _secure_regular_file(database_path)


def _enable_sqlite_foreign_keys(engine: Engine) -> None:
    """Enable SQLite foreign-key enforcement for every DB-API connection."""

    if not engine.dialect.name.startswith("sqlite"):
        return

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(
        dbapi_connection: SQLiteConnection,
        _connection_record: object,
    ) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()


@dataclass(frozen=True, slots=True)
class Database:
    """Owned infrastructure resources for one persistence target."""

    engine: Engine
    session_factory: sessionmaker[Session]

    def dispose(self) -> None:
        """Release pooled database resources."""

        self.engine.dispose()


def create_database(database_url: str) -> Database:
    """Build SQLAlchemy resources without creating application tables."""

    ensure_sqlite_parent(database_url)
    engine = create_engine(database_url, pool_pre_ping=True)
    _enable_sqlite_foreign_keys(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return Database(engine=engine, session_factory=factory)
