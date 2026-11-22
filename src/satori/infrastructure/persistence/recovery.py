"""Consistent SQLite stored-state capture and exact trusted-schema admission."""

import hashlib
import json
import os
import sqlite3
import stat
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from satori.application.recovery.contracts import RecoveryError, SnapshotInventory
from satori.domain.identity import Identity, SeedProvenance
from satori.domain.initial_self import InitialSelfSnapshot
from satori.domain.personality import Personality, PersonalityTrait
from satori.domain.values import CoreValue, ValueOrigin, Values
from satori.infrastructure.artifacts import PrivateArtifactStore
from satori.infrastructure.persistence.migrations import upgrade_database
from satori.infrastructure.seeds.loader import CANONICAL_V1_TRAITS, CANONICAL_V1_VALUES

MAX_SNAPSHOT_BYTES = 256 * 1024 * 1024
SUPPORTED_SNAPSHOT_REVISIONS = (
    "0013_conversation_failure_reason",
    "0014_memory_scope",
    "0015_memory_lifecycle",
    "0016_source_policy",
    "0017_erasure_intent",
    "0018_erasure_bodies",
    "0019_dialogue_bodies",
    "0020_knowledge_bodies",
    "0021_reflection_bodies",
    "0022_appraisal_bodies",
    "0023_audit_metadata",
    "0024_checkpoint_reasons",
)
_CAPTURE_TIMEOUT_SECONDS = 30.0
type SchemaRows = tuple[tuple[str, str, str, str | None], ...]


def _database_path(database_url: str) -> Path:
    try:
        url = make_url(database_url)
    except (ArgumentError, ValueError):
        raise RecoveryError("unsupported_database_url") from None
    if (
        url.drivername not in {"sqlite", "sqlite+pysqlite"}
        or url.username is not None
        or url.password is not None
        or url.host is not None
        or url.port is not None
        or url.query
        or not url.database
        or url.database == ":memory:"
        or url.database.startswith("file:")
    ):
        raise RecoveryError("unsupported_database_url")
    candidate = Path(url.database).expanduser().absolute()
    return candidate.parent.resolve() / candidate.name


def _regular_file(path: Path) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError:
        raise RecoveryError("snapshot_unavailable") from None
    if not stat.S_ISREG(metadata.st_mode):
        raise RecoveryError("snapshot_not_regular")
    if metadata.st_size <= 0:
        raise RecoveryError("snapshot_empty")
    if metadata.st_size > MAX_SNAPSHOT_BYTES:
        raise RecoveryError("snapshot_too_large")
    return metadata


@contextmanager
def _open_query_only(
    path: Path, *, immutable: bool, read_only: bool = True
) -> Iterator[sqlite3.Connection]:
    """Check regular-file identity and disable every write/extension path."""

    before = _regular_file(path)
    connection: sqlite3.Connection | None = None
    try:
        mode = "ro" if read_only else "rw"
        uri = path.absolute().as_uri() + f"?mode={mode}" + ("&immutable=1" if immutable else "")
        connection = sqlite3.connect(uri, uri=True, timeout=1.0)
        after = _regular_file(path)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise RecoveryError("snapshot_changed")
        connection.enable_load_extension(False)
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        yield connection
    except sqlite3.Error:
        raise RecoveryError("snapshot_invalid") from None
    finally:
        if connection is not None:
            connection.close()


def _schema_rows(connection: sqlite3.Connection) -> SchemaRows:
    return tuple(
        (kind, name, table, _canonical_table_sql(sql) if kind == "table" and sql else sql)
        for kind, name, table, sql in connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema ORDER BY type, name"
        ).fetchall()
    )


def _canonical_table_sql(sql: str) -> str:
    """Memoize only a bounded pure DDL transform, never schema admission or body proof.

    Every admission still reads fresh sqlite_schema and recomputes its exact digest.
    The key is the complete raw DDL string; changed SQL cannot reuse the old result.
    This process-local cache contains schema metadata, not protected payload bodies.
    """
    if len(sql) <= 16_384:
        return _cached_canonical_table_sql(sql)
    return _canonical_table_sql_uncached(sql)


@lru_cache(maxsize=128)
def _cached_canonical_table_sql(sql: str) -> str:
    return _canonical_table_sql_uncached(sql)


def _canonical_table_sql_uncached(sql: str) -> str:
    """Ignore only ordering of complete named table constraints, never their content.

    Alembic batch recreation emits equivalent named constraints in set iteration order.
    Declaration edge whitespace is stripped. Column order, declaration content,
    table prefix/suffix and all non-table schema objects remain exact. This parser
    neither executes nor generalizes SQL.
    """

    start = sql.find("(")
    end = sql.rfind(")")
    if start < 0 or end <= start or not sql.startswith("CREATE TABLE "):
        return sql
    body = sql[start + 1 : end]
    declarations: list[str] = []
    depth = 0
    quote: str | None = None
    beginning = 0
    index = 0
    while index < len(body):
        character = body[index]
        if quote is not None:
            if character == quote:
                if index + 1 < len(body) and body[index + 1] == quote:
                    index += 1
                else:
                    quote = None
        elif character in {"'", '"', "`", "["}:
            quote = "]" if character == "[" else character
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        elif character == "," and depth == 0:
            declarations.append(body[beginning:index].strip())
            beginning = index + 1
        index += 1
    if depth != 0 or quote is not None:
        return sql
    declarations.append(body[beginning:].strip())
    columns = [item for item in declarations if not item.startswith("CONSTRAINT ")]
    constraints = sorted(item for item in declarations if item.startswith("CONSTRAINT "))
    return json.dumps([sql[:start], columns, constraints, sql[end + 1 :]], separators=(",", ":"))


def _schema_digest(rows: SchemaRows) -> str:
    return hashlib.sha256(
        json.dumps(rows, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _quote_identifier(value: str) -> str:
    """Only called for identifiers from the admitted trusted schema."""

    return '"' + value.replace('"', '""') + '"'


def _check_page_size(connection: sqlite3.Connection) -> int:
    page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
    page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
    if page_size * page_count > MAX_SNAPSHOT_BYTES:
        raise RecoveryError("snapshot_too_large")
    return page_size


def _private_parent(path: Path) -> None:
    missing: list[Path] = []
    parent = path.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
        os.chmod(directory, 0o700)


def _identity_shape(connection: sqlite3.Connection, counts: dict[str, int]) -> str | None:
    identities = connection.execute(
        "SELECT identity_id, installation_slot FROM satori_identities"
    ).fetchall()
    if not identities:
        if any(count for table, count in counts.items() if table != "alembic_version"):
            raise RecoveryError("identity_state_invalid")
        return None
    if len(identities) != 1:
        raise RecoveryError("identity_state_invalid")
    identity_id, slot = identities[0]
    if not isinstance(identity_id, str) or not identity_id.strip() or len(identity_id) > 128:
        raise RecoveryError("identity_state_invalid")
    if slot != 1:
        raise RecoveryError("identity_state_invalid")
    for table in ("satori_personality_states", "satori_value_sets"):
        records = connection.execute(
            f"SELECT identity_id, schema_version FROM {_quote_identifier(table)}"
        ).fetchall()
        if records != [(identity_id, 1)]:
            raise RecoveryError("identity_state_invalid")
    for table, key, expected in (
        ("satori_personality_traits", "trait_key", CANONICAL_V1_TRAITS),
        ("satori_values", "value_key", CANONICAL_V1_VALUES),
    ):
        records = connection.execute(
            f"SELECT identity_id, {_quote_identifier(key)} FROM {_quote_identifier(table)}"
        ).fetchall()
        if len(records) != len(expected) or {item[1] for item in records} != expected:
            raise RecoveryError("identity_state_invalid")
        if any(item[0] != identity_id for item in records):
            raise RecoveryError("identity_state_invalid")
    _validate_initial_domain(connection, identity_id)
    return identity_id


def _validate_initial_domain(connection: sqlite3.Connection, identity_id: str) -> None:
    """Apply pure DTO/domain validation, without a repository/status write path."""

    identity = connection.execute(
        "SELECT name, activation_time, identity_version, seed_id, seed_schema_version, "
        "seed_content_hash FROM satori_identities WHERE identity_id=?",
        (identity_id,),
    ).fetchone()
    personality = connection.execute(
        "SELECT schema_version, aggregate_version FROM satori_personality_states "
        "WHERE identity_id=?",
        (identity_id,),
    ).fetchone()
    values = connection.execute(
        "SELECT schema_version, aggregate_version FROM satori_value_sets WHERE identity_id=?",
        (identity_id,),
    ).fetchone()
    traits = connection.execute(
        "SELECT trait_key, value, baseline_value FROM satori_personality_traits "
        "WHERE identity_id=?",
        (identity_id,),
    ).fetchall()
    items = connection.execute(
        "SELECT value_key, strength, description, origin FROM satori_values WHERE identity_id=?",
        (identity_id,),
    ).fetchall()
    try:
        # SQLite's UTCDateTime storage drops the offset; mirror its pure read codec.
        activated_at = datetime.fromisoformat(identity[1])
        if activated_at.tzinfo is None or activated_at.utcoffset() is None:
            activated_at = activated_at.replace(tzinfo=UTC)
        InitialSelfSnapshot(
            schema_version=1,
            identity=Identity(
                identity_id=identity_id,
                name=identity[0],
                activation_time=activated_at,
                identity_version=identity[2],
                seed_provenance=SeedProvenance(identity[3], identity[4], identity[5]),
            ),
            personality=Personality(
                schema_version=personality[0],
                aggregate_version=personality[1],
                traits=tuple(PersonalityTrait(*trait) for trait in traits),
            ),
            values=Values(
                schema_version=values[0],
                aggregate_version=values[1],
                items=tuple(
                    CoreValue(item[0], item[1], item[2], ValueOrigin(item[3])) for item in items
                ),
            ),
        )
    except (AttributeError, TypeError, ValueError):
        raise RecoveryError("identity_state_invalid") from None


class SQLiteStoredSnapshot:
    """Capture existing storage without initializing or evaluating domain state."""

    def __init__(self, database_url: str, *, alembic_config: Path) -> None:
        self._source_path = _database_path(database_url)
        self._alembic_config = alembic_config
        self._references: dict[str, SchemaRows] = {}

    @property
    def source_path(self) -> Path:
        return self._source_path

    def _trusted_reference(self, revision: str) -> SchemaRows:
        if revision not in self._references:
            try:
                with TemporaryDirectory(prefix="satori-recovery-schema-") as temporary:
                    path = Path(temporary) / "reference.db"
                    upgrade_database(
                        f"sqlite+pysqlite:///{path}",
                        config_path=self._alembic_config,
                        revision=revision,
                    )
                    with _open_query_only(path, immutable=True) as connection:
                        rows = _schema_rows(connection)
                        versions = connection.execute(
                            "SELECT version_num FROM alembic_version"
                        ).fetchall()
                        if versions != [(revision,)]:
                            raise RecoveryError("trusted_schema_unavailable")
                        self._references[revision] = rows
            except Exception:
                raise RecoveryError("trusted_schema_unavailable") from None
        return self._references[revision]

    def _admit_schema(self, connection: sqlite3.Connection) -> tuple[SchemaRows, str]:
        rows = _schema_rows(connection)
        for revision in SUPPORTED_SNAPSHOT_REVISIONS:
            if rows == self._trusted_reference(revision):
                versions = connection.execute("SELECT version_num FROM alembic_version").fetchall()
                if versions != [(revision,)]:
                    raise RecoveryError("unsupported_snapshot_revision")
                _check_page_size(connection)
                return rows, revision
        raise RecoveryError("unsupported_snapshot_schema")

    def inspect(self, snapshot: Path) -> SnapshotInventory:
        """Validate only a private replica of an offline snapshot, never its input.

        Some SQLite builds skip CHECK validation when the connection uses mode=ro.
        A private replica uses mode=rw plus query_only, after exact schema admission,
        so integrity_check covers every trusted owner's CHECK constraints.
        """

        _regular_file(snapshot)
        try:
            with TemporaryDirectory(prefix="satori-recovery-inspection-") as temporary:
                replica = Path(temporary) / "validation.db"
                artifacts = PrivateArtifactStore()
                artifacts.write(replica, artifacts.read(snapshot, max_bytes=MAX_SNAPSHOT_BYTES))
                with _open_query_only(replica, immutable=False, read_only=False) as connection:
                    return self._inspect_admitted(connection)
        except OSError:
            raise RecoveryError("snapshot_inspection_failed") from None

    def _inspect_admitted(self, connection: sqlite3.Connection) -> SnapshotInventory:
        rows, revision = self._admit_schema(connection)
        if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise RecoveryError("snapshot_integrity_failed")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RecoveryError("snapshot_foreign_keys_failed")
        counts = {
            name: int(
                connection.execute(f"SELECT count(*) FROM {_quote_identifier(name)}").fetchone()[0]
            )
            for kind, name, _table, _sql in rows
            if kind == "table"
        }
        return SnapshotInventory(
            identity_id=_identity_shape(connection, counts),
            alembic_revision=revision,
            schema_sha256=_schema_digest(rows),
            table_counts=counts,
        )

    def capture(self, destination: Path) -> SnapshotInventory:
        """Backup one committed read snapshot, including data still in source WAL."""

        source = self.source_path
        total_size = _regular_file(source).st_size
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = Path(str(source) + suffix)
            try:
                metadata = sidecar.lstat()
            except FileNotFoundError:
                continue
            except OSError:
                raise RecoveryError("snapshot_unavailable") from None
            if not stat.S_ISREG(metadata.st_mode):
                raise RecoveryError("source_sidecar_not_regular")
            total_size += metadata.st_size
        if total_size > MAX_SNAPSHOT_BYTES:
            raise RecoveryError("snapshot_too_large")
        created = False
        completed = False
        try:
            with _open_query_only(source, immutable=False) as connection:
                self._admit_schema(connection)
                page_size = _check_page_size(connection)
                _private_parent(destination)
                try:
                    descriptor = os.open(
                        destination,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                        0o600,
                    )
                except FileExistsError:
                    raise RecoveryError("snapshot_destination_exists") from None
                created = True
                try:
                    os.fchmod(descriptor, 0o600)
                finally:
                    os.close(descriptor)
                started = time.monotonic()

                def progress(_status: int, _remaining: int, total: int) -> None:
                    if total * page_size > MAX_SNAPSHOT_BYTES:
                        raise RecoveryError("snapshot_too_large")
                    if time.monotonic() - started > _CAPTURE_TIMEOUT_SECONDS:
                        raise RecoveryError("snapshot_capture_timeout")

                with closing(sqlite3.connect(destination)) as captured:
                    connection.backup(captured, pages=128, progress=progress, sleep=0.01)
                inventory = self.inspect(destination)
                with destination.open("rb") as stored:
                    os.fsync(stored.fileno())
                completed = True
                return inventory
        except (OSError, sqlite3.Error):
            raise RecoveryError("snapshot_capture_failed") from None
        finally:
            if created and not completed:
                try:
                    destination.unlink(missing_ok=True)
                except OSError:
                    raise RecoveryError("snapshot_cleanup_failed") from None
