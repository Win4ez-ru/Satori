"""SQLAlchemy engine and session-factory construction."""

from dataclasses import dataclass
from pathlib import Path
from sqlite3 import Connection as SQLiteConnection

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker


def ensure_sqlite_parent(database_url: str) -> None:
    """Create the parent directory for a file-backed SQLite database."""

    url = make_url(database_url)
    if not url.drivername.startswith("sqlite"):
        return
    database = url.database
    if database is None or database == ":memory:":
        return
    Path(database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


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
