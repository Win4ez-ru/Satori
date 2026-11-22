"""Metadata-only independent SQLite floor, never implicitly created on read."""

import hashlib
import json
import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, cast
from urllib.parse import quote
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError

from satori.domain.erasure_appraisal import AppraisalBodyAction, AppraisalBodyProposal
from satori.domain.erasure_bodies import (
    BodyAction,
    BodyErasureAction,
    BodyErasureProposal,
    BodyProposal,
    body_actions_digest,
    proposal_identity,
    proposal_lineage,
    source_proposal_root,
)
from satori.domain.erasure_dialogue import DialogueBodyAction, DialogueBodyProposal
from satori.domain.erasure_intent import (
    ERASURE_RETENTION_POLICY_VERSION,
    ErasureIntent,
    ErasureIntentProposal,
)
from satori.domain.erasure_knowledge import KnowledgeBodyAction, KnowledgeBodyProposal
from satori.domain.erasure_personality_reasons import (
    CheckpointReasonAction,
    CheckpointReasonProposal,
)
from satori.domain.erasure_reflection import ReflectionBodyAction, ReflectionBodyProposal
from satori.domain.source_policy import (
    SOURCE_POLICY_VERSION,
    CanonicalRootRef,
    ExclusionPhase,
    SourceEligibilitySnapshot,
    SourceExclusionReceipt,
    SourcePolicyError,
)
from satori.infrastructure.artifacts import PrivateArtifactStore, _bound_parent
from satori.infrastructure.source_policy.body_policy import (
    MAX_BODY_ACTIONS,
    MAX_BODY_REFERENCES,
    body_rows,
    decode_action,
    encode_targets,
    proposal_references,
    validate_body_actions,
    validate_knowledge_origins,
)
from satori.infrastructure.source_policy.migrations import (
    APPRAISAL_POLICY_REVISIONS,
    BODY_POLICY_REVISIONS,
    CHECKPOINT_REASON_POLICY_REVISIONS,
    DIALOGUE_POLICY_REVISIONS,
    ERASURE_POLICY_REVISIONS,
    KNOWLEDGE_POLICY_REVISIONS,
    POLICY_SCHEMA_REVISION,
    REFLECTION_POLICY_REVISIONS,
    SUPPORTED_POLICY_REVISIONS,
    upgrade_policy,
)

MAX_FLOOR_BYTES = 64 * 1024 * 1024
MAX_EXCLUDED_ROOTS = 100_000

if TYPE_CHECKING:
    from satori.application.source_policy.binding_recovery import (
        AdoptCurrentBindingFiles,
        DeviceBindingRecoveryReceipt,
    )


@dataclass(frozen=True, slots=True)
class InstallationBinding:
    installation_id: str
    floor_id: str
    database_inode: tuple[int, int]
    lock_inode: tuple[int, int]


@lru_cache(maxsize=4)
def _expected_schema(
    revision: str = POLICY_SCHEMA_REVISION,
) -> tuple[tuple[str, str, str, str], ...]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with engine.begin() as connection:
            upgrade_policy(connection, revision=revision)
            rows = connection.exec_driver_sql(
                "SELECT type,name,tbl_name,sql FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
            ).all()
            return tuple((str(a), str(b), str(c), str(d)) for a, b, c, d in rows)
    finally:
        engine.dispose()


class InstallationPolicyLedger:
    """Sole floor writer used only while SourcePolicyRuntime holds its OS barrier."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory.expanduser().absolute()
        self.database_path = self.directory / "policy.sqlite"
        self.binding_path = self.directory / "installation.json"
        self.lock_path = self.directory / "policy.lock"
        self._snapshots: dict[str, SourceEligibilitySnapshot] = {}
        self._validated_stamp: tuple[int, int, int, int] | None = None

    @property
    def reserved_paths(self) -> tuple[Path, ...]:
        return (
            self.binding_path,
            self.lock_path,
            self.database_path,
            Path(f"{self.database_path}-journal"),
            Path(f"{self.database_path}-wal"),
            Path(f"{self.database_path}-shm"),
        )

    def binding_exists(self) -> bool:
        # lexists deliberately distinguishes absent metadata from a broken symlink.
        return os.path.lexists(self.binding_path)

    def read_binding(self) -> InstallationBinding:
        try:
            data = PrivateArtifactStore().read(self.binding_path, max_bytes=4096)
            value = json.loads(data)
            if not isinstance(value, dict) or set(value) != {
                "version",
                "installation_id",
                "floor_id",
                "database_inode",
                "lock_inode",
            }:
                raise ValueError
            if type(value["version"]) is not int or value["version"] != 1:
                raise ValueError
            for name in ("installation_id", "floor_id"):
                if not isinstance(value[name], str) or not value[name] or len(value[name]) > 128:
                    raise ValueError
            inodes: list[tuple[int, int]] = []
            for name in ("database_inode", "lock_inode"):
                pair = value[name]
                if (
                    not isinstance(pair, list)
                    or len(pair) != 2
                    or any(type(part) is not int or part < 0 for part in pair)
                ):
                    raise ValueError
                inodes.append((pair[0], pair[1]))
            self._file_stamp(self.binding_path)
            return InstallationBinding(value["installation_id"], value["floor_id"], *inodes)
        except (OSError, ValueError, TypeError, KeyError):
            raise SourcePolicyError("policy_installation_binding_unavailable") from None

    @staticmethod
    def _file_stamp(path: Path) -> tuple[int, int, int, int]:
        try:
            with _bound_parent(path, create=False) as (parent, name):
                parent_metadata = os.fstat(parent)
                if (
                    stat.S_IMODE(parent_metadata.st_mode) != 0o700
                    or parent_metadata.st_uid != os.getuid()
                ):
                    raise SourcePolicyError("policy_directory_not_private")
                observed = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if (
                    not stat.S_ISREG(observed.st_mode)
                    or observed.st_nlink != 1
                    or stat.S_IMODE(observed.st_mode) != 0o600
                    or observed.st_uid != os.getuid()
                    or observed.st_size > MAX_FLOOR_BYTES
                ):
                    raise SourcePolicyError("policy_file_binding_invalid")
                return observed.st_dev, observed.st_ino, observed.st_size, observed.st_mtime_ns
        except SourcePolicyError:
            raise
        except OSError:
            raise SourcePolicyError("policy_file_unavailable") from None

    def validate_binding(self) -> InstallationBinding:
        binding = self.read_binding()
        if self._file_stamp(self.database_path)[:2] != binding.database_inode:
            raise SourcePolicyError("policy_floor_binding_invalid")
        if self._file_stamp(self.lock_path)[:2] != binding.lock_inode:
            raise SourcePolicyError("policy_lock_binding_invalid")
        return binding

    @staticmethod
    def _binding_descriptor_bytes(binding: InstallationBinding) -> bytes:
        return json.dumps(
            {
                "version": 1,
                "installation_id": binding.installation_id,
                "floor_id": binding.floor_id,
                "database_inode": binding.database_inode,
                "lock_inode": binding.lock_inode,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()

    def _publish_binding_descriptor(
        self,
        binding: InstallationBinding,
        *,
        overwrite: bool = False,
        expected_sha256: str | None = None,
    ) -> None:
        """The sole descriptor publisher, shared by initialization and reviewed adoption."""
        if overwrite:
            current = PrivateArtifactStore().read(self.binding_path, max_bytes=4096)
            self._file_stamp(self.binding_path)
            if expected_sha256 is None or hashlib.sha256(current).hexdigest() != expected_sha256:
                raise SourcePolicyError("binding_recovery_review_stale")
        PrivateArtifactStore().write(
            self.binding_path, self._binding_descriptor_bytes(binding), overwrite=overwrite
        )
        self._validated_stamp = None
        self._snapshots.clear()

    def adopt_reviewed_device_binding(
        self, canonical_database: Path, command: "AdoptCurrentBindingFiles"
    ) -> "DeviceBindingRecoveryReceipt":
        """Explicit owner command; normal validation never calls or falls back to it."""
        from satori.infrastructure.source_policy.binding_recovery import adopt_under_ledger_owner

        return adopt_under_ledger_owner(self, canonical_database, command)

    def initialize(self, lock_inode: tuple[int, int]) -> InstallationBinding:
        if self.binding_exists() or os.path.lexists(self.database_path):
            raise SourcePolicyError("policy_recovery_required")
        installation_id, floor_id = str(uuid4()), str(uuid4())
        try:
            with _bound_parent(self.database_path, create=True) as (parent, name):
                descriptor = os.open(
                    name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent
                )
                try:
                    os.fchmod(descriptor, 0o600)
                    metadata = os.fstat(descriptor)
                    database_inode = metadata.st_dev, metadata.st_ino
                    os.fsync(descriptor)
                    os.fsync(parent)
                finally:
                    os.close(descriptor)
            engine = create_engine(f"sqlite+pysqlite:///{self.database_path}")
            try:
                with engine.begin() as connection:
                    connection.exec_driver_sql("PRAGMA synchronous=EXTRA")
                    upgrade_policy(connection)
                    connection.exec_driver_sql(
                        "INSERT INTO policy_installation VALUES (1,?,?,?)",
                        (installation_id, floor_id, SOURCE_POLICY_VERSION),
                    )
            finally:
                engine.dispose()
            with _bound_parent(self.database_path, create=False) as (parent, name):
                descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                os.fsync(parent)
            self._publish_binding_descriptor(
                InstallationBinding(installation_id, floor_id, database_inode, lock_inode)
            )
            return self.validate_binding()
        except SourcePolicyError:
            raise
        except Exception:
            # Partial initialization is intentionally retained for explicit policy recovery.
            raise SourcePolicyError("policy_initialization_failed") from None

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        binding = self.validate_binding()
        before = self._file_stamp(self.database_path)
        connection: sqlite3.Connection | None = None
        try:
            uri = f"file:{quote(str(self.database_path), safe='/')}?mode=rw"
            connection = sqlite3.connect(uri, uri=True, timeout=1.0, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute("PRAGMA synchronous=EXTRA")
            if not write:
                connection.execute("PRAGMA query_only=ON")
            if self._file_stamp(self.database_path)[:2] != before[:2]:
                raise SourcePolicyError("policy_floor_binding_invalid")
            self._validate_database(connection, binding, before)
            if write:
                page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
                connection.execute(f"PRAGMA max_page_count={MAX_FLOOR_BYTES // page_size}")
                connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                if write:
                    connection.commit()
            except BaseException:
                if write:
                    connection.rollback()
                raise
            if self._file_stamp(self.database_path)[:2] != binding.database_inode:
                raise SourcePolicyError("policy_floor_binding_invalid")
        except SourcePolicyError:
            raise
        except (sqlite3.Error, OSError, ValueError, KeyError, TypeError):
            raise SourcePolicyError("policy_storage_unavailable") from None
        finally:
            if connection is not None:
                connection.close()

    def _validate_database(
        self,
        connection: sqlite3.Connection,
        binding: InstallationBinding,
        stamp: tuple[int, int, int, int],
    ) -> None:
        revision = self._revision(connection)
        if stamp != self._validated_stamp:
            schema = tuple(
                tuple(str(part) for part in row)
                for row in connection.execute(
                    "SELECT type,name,tbl_name,sql FROM sqlite_master "
                    "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
                )
            )
            if schema != _expected_schema(revision):
                raise SourcePolicyError("policy_schema_invalid")
        installation = connection.execute("SELECT * FROM policy_installation").fetchall()
        if [tuple(row) for row in installation] != [
            (1, binding.installation_id, binding.floor_id, SOURCE_POLICY_VERSION)
        ]:
            raise SourcePolicyError("policy_floor_binding_invalid")
        if stamp == self._validated_stamp:
            return
        self._snapshots.clear()
        if [row[0] for row in connection.execute("PRAGMA integrity_check")] != ["ok"]:
            raise SourcePolicyError("policy_integrity_invalid")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise SourcePolicyError("policy_integrity_invalid")
        invalid = connection.execute(
            "SELECT 1 FROM policy_exclusions e JOIN policy_lineages l USING(identity_id) "
            "WHERE e.generation>l.generation OR e.generation<1 OR l.generation<0 LIMIT 1"
        ).fetchone()
        if invalid is not None:
            raise SourcePolicyError("policy_integrity_invalid")
        invalid_receipt = connection.execute(
            "SELECT 1 FROM policy_receipts r JOIN policy_lineages l USING(identity_id) "
            "JOIN policy_exclusions e ON e.identity_id=r.identity_id AND e.message_id=r.message_id "
            "WHERE r.lineage_id!=l.lineage_id OR r.counterparty_id!=e.counterparty_id "
            "OR r.session_id!=e.session_id OR r.interaction_id!=e.interaction_id "
            "OR r.result_generation>l.generation OR r.result_generation<e.generation "
            "OR r.expected_generation<0 OR r.result_generation<r.expected_generation "
            "OR r.result_generation>r.expected_generation+1 LIMIT 1"
        ).fetchone()
        if invalid_receipt is not None:
            raise SourcePolicyError("policy_integrity_invalid")
        inconsistent_generation = connection.execute(
            "SELECT 1 FROM policy_lineages l WHERE l.generation!=(SELECT count(*) "
            "FROM policy_exclusions e WHERE e.identity_id=l.identity_id) LIMIT 1"
        ).fetchone()
        if inconsistent_generation is not None:
            raise SourcePolicyError("policy_integrity_invalid")
        duplicate_generation = connection.execute(
            "SELECT 1 FROM policy_exclusions GROUP BY identity_id,generation "
            "HAVING count(*)!=1 LIMIT 1"
        ).fetchone()
        missing_original_receipt = connection.execute(
            "SELECT 1 FROM policy_exclusions e LEFT JOIN policy_receipts r "
            "ON r.request_id=e.request_id WHERE r.request_id IS NULL "
            "OR r.identity_id!=e.identity_id OR r.message_id!=e.message_id "
            "OR r.result_generation!=e.generation OR r.expected_generation!=e.generation-1 "
            "LIMIT 1"
        ).fetchone()
        if duplicate_generation is not None or missing_original_receipt is not None:
            raise SourcePolicyError("policy_integrity_invalid")
        if revision in ERASURE_POLICY_REVISIONS:
            self._validate_erasure_intents(connection, binding)
        if revision in BODY_POLICY_REVISIONS:
            validate_body_actions(
                connection, floor_id=binding.floor_id, installation_id=binding.installation_id
            )
        self._validated_stamp = stamp

    @staticmethod
    def _revision(connection: sqlite3.Connection) -> str:
        revisions = connection.execute("SELECT version_num FROM alembic_version").fetchall()
        if len(revisions) != 1 or revisions[0][0] not in SUPPORTED_POLICY_REVISIONS:
            raise SourcePolicyError("policy_schema_invalid")
        return str(revisions[0][0])

    def _validate_erasure_intents(
        self, connection: sqlite3.Connection, binding: InstallationBinding
    ) -> None:
        invalid = connection.execute(
            "SELECT 1 FROM policy_erasure_intents i JOIN policy_lineages l USING(identity_id) "
            "JOIN policy_exclusions e ON e.identity_id=i.identity_id AND e.message_id=i.message_id "
            "WHERE i.lineage_id!=l.lineage_id OR i.counterparty_id!=e.counterparty_id "
            "OR i.session_id!=e.session_id OR i.interaction_id!=e.interaction_id "
            "OR i.expected_source_generation<e.generation "
            "OR i.expected_source_generation>l.generation "
            "OR i.result_erasure_revision>l.erasure_revision "
            "OR i.result_erasure_revision!=i.expected_erasure_revision+1 "
            "OR i.retention_policy!=? LIMIT 1",
            (ERASURE_RETENTION_POLICY_VERSION,),
        ).fetchone()
        inconsistent = connection.execute(
            "SELECT 1 FROM policy_lineages l WHERE l.erasure_revision!=(SELECT count(*) "
            "FROM policy_erasure_intents i WHERE i.identity_id=l.identity_id) LIMIT 1"
        ).fetchone()
        conflicting_request = connection.execute(
            "SELECT 1 FROM policy_erasure_intents i "
            "JOIN policy_receipts r USING(request_id) LIMIT 1"
        ).fetchone()
        if invalid is not None or inconsistent is not None or conflicting_request is not None:
            raise SourcePolicyError("policy_integrity_invalid")
        # CHECK constraints cover storage shape; domain validation also admits exact digest,
        # timestamp and authority meanings before publishing any floor snapshot.
        rows = connection.execute(
            "SELECT * FROM policy_erasure_intents LIMIT ?", (MAX_EXCLUDED_ROOTS + 1,)
        ).fetchall()
        if len(rows) > MAX_EXCLUDED_ROOTS:
            raise SourcePolicyError("policy_floor_capacity_exceeded")
        try:
            for row in rows:
                self._erasure_intent(row, binding=binding)
        except (ValueError, TypeError, KeyError):
            raise SourcePolicyError("policy_integrity_invalid") from None

    def upgrade(self, *, revision: str = POLICY_SCHEMA_REVISION) -> None:
        """Explicit exact-old-schema admission; fixed floor inode/binding survive migration."""
        binding = self.validate_binding()
        before = self._file_stamp(self.database_path)
        engine = create_engine(
            f"sqlite+pysqlite:///file:{quote(str(self.database_path), safe='/')}?mode=rw&uri=true"
        )
        try:
            with engine.connect() as connection:
                raw = cast(sqlite3.Connection, connection.connection.driver_connection)
                raw.row_factory = sqlite3.Row
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                connection.exec_driver_sql("PRAGMA trusted_schema=OFF")
                connection.exec_driver_sql("PRAGMA synchronous=EXTRA")
                page_size = int(connection.exec_driver_sql("PRAGMA page_size").scalar_one())
                connection.exec_driver_sql(f"PRAGMA max_page_count={MAX_FLOOR_BYTES // page_size}")
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                self._validate_database(raw, binding, before)
                if revision not in SUPPORTED_POLICY_REVISIONS:
                    raise SourcePolicyError("policy_schema_invalid")
                if SUPPORTED_POLICY_REVISIONS.index(
                    self._revision(raw)
                ) >= SUPPORTED_POLICY_REVISIONS.index(revision):
                    connection.rollback()
                    return
                upgrade_policy(connection, revision=revision)
                self._validated_stamp = None
                self._snapshots.clear()
                self._validate_database(raw, binding, before)
                connection.commit()
            if self._file_stamp(self.database_path)[:2] != binding.database_inode:
                raise SourcePolicyError("policy_floor_binding_invalid")
            self._validated_stamp = None
            self._snapshots.clear()
        except SourcePolicyError:
            raise
        except (SQLAlchemyError, sqlite3.Error, OSError, ValueError, KeyError, TypeError):
            raise SourcePolicyError("policy_migration_failed") from None
        finally:
            self._validated_stamp = None
            self._snapshots.clear()
            engine.dispose()

    def enroll(
        self, identity_id: str, *, lineage_id: str | None = None
    ) -> SourceEligibilitySnapshot:
        with self._connection(write=True) as connection:
            existing = connection.execute(
                "SELECT lineage_id FROM policy_lineages WHERE identity_id=?", (identity_id,)
            ).fetchone()
            if existing is not None:
                if lineage_id is not None and lineage_id != existing[0]:
                    raise SourcePolicyError("policy_lineage_binding_mismatch")
            else:
                connection.execute(
                    "INSERT INTO policy_lineages(identity_id,lineage_id,generation,enrolled_at) "
                    "VALUES (?,?,0,?)",
                    (identity_id, lineage_id or str(uuid4()), datetime.now(UTC).isoformat()),
                )
        return self.snapshot(identity_id)

    def snapshot(self, identity_id: str) -> SourceEligibilitySnapshot:
        binding = self.read_binding()
        with self._connection() as connection:
            erasure_column = (
                "erasure_revision"
                if self._revision(connection) in ERASURE_POLICY_REVISIONS
                else "0"
            )
            body_column = (
                "body_revision" if self._revision(connection) in BODY_POLICY_REVISIONS else "0"
            )
            lineage = connection.execute(
                f"SELECT lineage_id,generation,{erasure_column},{body_column} FROM policy_lineages "
                "WHERE identity_id=?",
                (identity_id,),
            ).fetchone()
            if lineage is None:
                raise SourcePolicyError("policy_lineage_not_enrolled")
            cached = self._snapshots.get(identity_id)
            if cached is not None and (
                cached.generation,
                cached.erasure_revision,
                cached.body_revision,
            ) == (
                lineage[1],
                lineage[2],
                lineage[3],
            ):
                return cached
            rows = connection.execute(
                "SELECT message_id FROM policy_exclusions WHERE identity_id=? LIMIT ?",
                (identity_id, MAX_EXCLUDED_ROOTS + 1),
            ).fetchall()
            if len(rows) > MAX_EXCLUDED_ROOTS:
                raise SourcePolicyError("policy_floor_capacity_exceeded")
            snapshot = SourceEligibilitySnapshot(
                binding.floor_id,
                binding.installation_id,
                lineage[0],
                identity_id,
                lineage[1],
                frozenset(row[0] for row in rows),
                erasure_revision=lineage[2],
                body_revision=lineage[3],
                body_head_digest=(
                    body_actions_digest(
                        tuple(
                            decode_action(
                                row,
                                floor_id=binding.floor_id,
                                installation_id=binding.installation_id,
                            )
                            for row in connection.execute(
                                "SELECT * FROM policy_body_actions WHERE identity_id=? "
                                "ORDER BY result_body_revision",
                                (identity_id,),
                            )
                        )
                    )
                    if lineage[3]
                    else ""
                ),
            )
            self._snapshots[identity_id] = snapshot
            return snapshot

    @staticmethod
    def _receipt(row: sqlite3.Row, floor_id: str) -> SourceExclusionReceipt:
        return SourceExclusionReceipt(
            request_id=row["request_id"],
            decision_id=row["decision_id"],
            root=CanonicalRootRef(
                row["lineage_id"],
                row["identity_id"],
                row["counterparty_id"],
                row["session_id"],
                row["interaction_id"],
                row["message_id"],
            ),
            floor_id=floor_id,
            expected_generation=row["expected_generation"],
            result_generation=row["result_generation"],
            preview_digest=row["preview_digest"],
            decided_at=datetime.fromisoformat(row["decided_at"]),
            trace_id=row["trace_id"],
            phase=ExclusionPhase(row["phase"]),
        )

    def receipt(self, request_id: str) -> SourceExclusionReceipt | None:
        with self._connection() as connection:
            if self._body_request_exists(connection, request_id):
                raise SourcePolicyError("source_request_conflict")
            row = connection.execute(
                "SELECT * FROM policy_receipts WHERE request_id=?", (request_id,)
            ).fetchone()
            return None if row is None else self._receipt(row, self.read_binding().floor_id)

    def _erasure_intent(
        self, row: sqlite3.Row, *, binding: InstallationBinding | None = None
    ) -> ErasureIntent:
        binding = binding or self.read_binding()
        return ErasureIntent(
            root=CanonicalRootRef(
                row["lineage_id"],
                row["identity_id"],
                row["counterparty_id"],
                row["session_id"],
                row["interaction_id"],
                row["message_id"],
            ),
            request_id=row["request_id"],
            decision_id=row["decision_id"],
            floor_id=binding.floor_id,
            installation_id=binding.installation_id,
            expected_source_generation=row["expected_source_generation"],
            expected_erasure_revision=row["expected_erasure_revision"],
            result_erasure_revision=row["result_erasure_revision"],
            reviewed_preview_digest=row["reviewed_preview_digest"],
            approval_digest=row["approval_digest"],
            schema_revision=row["schema_revision"],
            schema_digest=row["schema_digest"],
            store_digest=row["store_digest"],
            routing_digest=row["routing_digest"],
            assessment_status=row["assessment_status"],
            retention_policy=row["retention_policy"],
            decided_at=datetime.fromisoformat(row["decided_at"]),
            trace_id=row["trace_id"],
        )

    def erasure_intent(self, request_id: str) -> ErasureIntent | None:
        with self._connection() as connection:
            if self._body_request_exists(connection, request_id):
                raise SourcePolicyError("erasure_request_conflict")
            if (
                connection.execute(
                    "SELECT 1 FROM policy_receipts WHERE request_id=?", (request_id,)
                ).fetchone()
                is not None
            ):
                raise SourcePolicyError("erasure_request_conflict")
            if self._revision(connection) not in ERASURE_POLICY_REVISIONS:
                return None
            row = connection.execute(
                "SELECT * FROM policy_erasure_intents WHERE request_id=?", (request_id,)
            ).fetchone()
            return None if row is None else self._erasure_intent(row)

    def erasure_intents(self, identity_id: str) -> tuple[ErasureIntent, ...]:
        with self._connection() as connection:
            if self._revision(connection) not in ERASURE_POLICY_REVISIONS:
                return ()
            rows = connection.execute(
                "SELECT * FROM policy_erasure_intents WHERE identity_id=? "
                "ORDER BY result_erasure_revision LIMIT ?",
                (identity_id, MAX_EXCLUDED_ROOTS + 1),
            ).fetchall()
            if len(rows) > MAX_EXCLUDED_ROOTS:
                raise SourcePolicyError("policy_floor_capacity_exceeded")
            binding = self.read_binding()
            return tuple(self._erasure_intent(row, binding=binding) for row in rows)

    def commit_erasure_intent(
        self, proposal: ErasureIntentProposal, *, trace_id: str
    ) -> ErasureIntent:
        try:
            with self._connection(write=True) as connection:
                if self._body_request_exists(connection, proposal.request_id):
                    raise SourcePolicyError("erasure_request_conflict")
                if self._revision(connection) not in ERASURE_POLICY_REVISIONS:
                    raise SourcePolicyError("policy_schema_upgrade_required")
                if (
                    connection.execute(
                        "SELECT 1 FROM policy_receipts WHERE request_id=?", (proposal.request_id,)
                    ).fetchone()
                    is not None
                ):
                    raise SourcePolicyError("erasure_request_conflict")
                existing = connection.execute(
                    "SELECT * FROM policy_erasure_intents WHERE request_id=?",
                    (proposal.request_id,),
                ).fetchone()
                if existing is not None:
                    result = self._erasure_intent(existing)
                    if not result.matches(proposal):
                        raise SourcePolicyError("erasure_request_conflict")
                    return result
                root = proposal.root
                lineage = connection.execute(
                    "SELECT lineage_id,generation,erasure_revision FROM policy_lineages "
                    "WHERE identity_id=?",
                    (root.identity_id,),
                ).fetchone()
                if lineage is None or lineage[0] != root.lineage_id:
                    raise SourcePolicyError("policy_lineage_binding_mismatch")
                if lineage[1] != proposal.expected_source_generation:
                    raise SourcePolicyError("source_generation_conflict")
                if lineage[2] != proposal.expected_erasure_revision:
                    raise SourcePolicyError("erasure_revision_conflict")
                excluded = connection.execute(
                    "SELECT counterparty_id,session_id,interaction_id FROM policy_exclusions "
                    "WHERE identity_id=? AND message_id=?",
                    (root.identity_id, root.message_id),
                ).fetchone()
                if excluded is None:
                    raise SourcePolicyError("erasure_requires_source_exclusion")
                if tuple(excluded) != (root.counterparty_id, root.session_id, root.interaction_id):
                    raise SourcePolicyError("source_root_binding_mismatch")
                if (
                    connection.execute(
                        "SELECT 1 FROM policy_erasure_intents WHERE identity_id=? AND message_id=?",
                        (root.identity_id, root.message_id),
                    ).fetchone()
                    is not None
                ):
                    raise SourcePolicyError("erasure_root_already_intended")
                if proposal.expected_erasure_revision >= MAX_EXCLUDED_ROOTS:
                    raise SourcePolicyError("policy_floor_capacity_exceeded")
                binding = self.read_binding()
                result = ErasureIntent(
                    root,
                    proposal.request_id,
                    str(uuid4()),
                    binding.floor_id,
                    binding.installation_id,
                    proposal.expected_source_generation,
                    proposal.expected_erasure_revision,
                    proposal.expected_erasure_revision + 1,
                    proposal.reviewed_preview_digest,
                    proposal.approval_digest,
                    proposal.schema_revision,
                    proposal.schema_digest,
                    proposal.store_digest,
                    proposal.routing_digest,
                    proposal.assessment_status,
                    proposal.retention_policy,
                    datetime.now(UTC),
                    trace_id,
                )
                changed = connection.execute(
                    "UPDATE policy_lineages SET erasure_revision=? "
                    "WHERE identity_id=? AND erasure_revision=?",
                    (
                        result.result_erasure_revision,
                        root.identity_id,
                        proposal.expected_erasure_revision,
                    ),
                ).rowcount
                if changed != 1:
                    raise SourcePolicyError("erasure_revision_conflict")
                connection.execute(
                    "INSERT INTO policy_erasure_intents "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        result.request_id,
                        result.decision_id,
                        root.identity_id,
                        root.lineage_id,
                        root.counterparty_id,
                        root.session_id,
                        root.interaction_id,
                        root.message_id,
                        result.expected_source_generation,
                        result.expected_erasure_revision,
                        result.result_erasure_revision,
                        result.reviewed_preview_digest,
                        result.approval_digest,
                        result.schema_revision,
                        result.schema_digest,
                        result.store_digest,
                        result.routing_digest,
                        result.assessment_status,
                        result.retention_policy,
                        result.decided_at.isoformat(),
                        result.trace_id,
                    ),
                )
        except SourcePolicyError as failure:
            if failure.reason_code == "policy_storage_unavailable":
                recovered = self.erasure_intent(proposal.request_id)
                if recovered is not None and recovered.matches(proposal):
                    return recovered
            raise
        committed = self.erasure_intent(proposal.request_id)
        if committed is None:
            raise SourcePolicyError("policy_write_outcome_unknown")
        return committed

    def _body_request_exists(self, connection: sqlite3.Connection, request_id: str) -> bool:
        return (
            self._revision(connection) in BODY_POLICY_REVISIONS
            and connection.execute(
                "SELECT 1 FROM policy_body_actions WHERE request_id=?", (request_id,)
            ).fetchone()
            is not None
        )

    def _body_action(self, row: sqlite3.Row) -> BodyAction:
        binding = self.read_binding()
        return decode_action(
            row, floor_id=binding.floor_id, installation_id=binding.installation_id
        )

    def body_action(self, request_id: str) -> BodyAction | None:
        with self._connection() as connection:
            if connection.execute(
                "SELECT 1 FROM policy_receipts WHERE request_id=?",
                (request_id,),
            ).fetchone() is not None or (
                self._revision(connection) in ERASURE_POLICY_REVISIONS
                and connection.execute(
                    "SELECT 1 FROM policy_erasure_intents WHERE request_id=?", (request_id,)
                ).fetchone()
                is not None
            ):
                raise SourcePolicyError("body_erasure_request_conflict")
            if self._revision(connection) not in BODY_POLICY_REVISIONS:
                return None
            row = connection.execute(
                "SELECT * FROM policy_body_actions WHERE request_id=?", (request_id,)
            ).fetchone()
            return None if row is None else self._body_action(row)

    def body_actions(self, identity_id: str) -> tuple[BodyAction, ...]:
        with self._connection() as connection:
            if self._revision(connection) not in BODY_POLICY_REVISIONS:
                return ()
            rows = connection.execute(
                "SELECT * FROM policy_body_actions WHERE identity_id=? "
                "ORDER BY result_body_revision LIMIT ?",
                (identity_id, MAX_BODY_ACTIONS + 1),
            ).fetchall()
            if len(rows) > MAX_BODY_ACTIONS:
                raise SourcePolicyError("policy_body_capacity_exceeded")
            return tuple(self._body_action(row) for row in rows)

    def body_policy_ready(self) -> bool:
        with self._connection() as connection:
            return self._revision(connection) in BODY_POLICY_REVISIONS

    def dialogue_policy_ready(self) -> bool:
        with self._connection() as connection:
            return self._revision(connection) in DIALOGUE_POLICY_REVISIONS

    def knowledge_policy_ready(self) -> bool:
        with self._connection() as connection:
            return self._revision(connection) in KNOWLEDGE_POLICY_REVISIONS

    def reflection_policy_ready(self) -> bool:
        with self._connection() as connection:
            return self._revision(connection) in REFLECTION_POLICY_REVISIONS

    def appraisal_policy_ready(self) -> bool:
        with self._connection() as connection:
            return self._revision(connection) in APPRAISAL_POLICY_REVISIONS

    def checkpoint_reason_policy_ready(self) -> bool:
        with self._connection() as connection:
            return self._revision(connection) in CHECKPOINT_REASON_POLICY_REVISIONS

    def commit_body_action(
        self, proposal: BodyErasureProposal, *, trace_id: str
    ) -> BodyErasureAction:
        if not isinstance(proposal, BodyErasureProposal):
            raise SourcePolicyError("invalid_body_proposal")
        result = self._commit_body_action(proposal, trace_id=trace_id)
        if not isinstance(result, BodyErasureAction):
            raise SourcePolicyError("body_erasure_request_conflict")
        return result

    def commit_dialogue_action(
        self, proposal: DialogueBodyProposal, *, trace_id: str
    ) -> DialogueBodyAction:
        if not isinstance(proposal, DialogueBodyProposal):
            raise SourcePolicyError("invalid_dialogue_proposal")
        result = self._commit_body_action(proposal, trace_id=trace_id)
        if not isinstance(result, DialogueBodyAction):
            raise SourcePolicyError("body_erasure_request_conflict")
        return result

    def commit_knowledge_action(
        self, proposal: KnowledgeBodyProposal, *, trace_id: str
    ) -> KnowledgeBodyAction:
        if not isinstance(proposal, KnowledgeBodyProposal):
            raise SourcePolicyError("invalid_knowledge_proposal")
        result = self._commit_body_action(proposal, trace_id=trace_id)
        if not isinstance(result, KnowledgeBodyAction):
            raise SourcePolicyError("body_erasure_request_conflict")
        return result

    def commit_reflection_action(
        self, proposal: ReflectionBodyProposal, *, trace_id: str
    ) -> ReflectionBodyAction:
        if not isinstance(proposal, ReflectionBodyProposal):
            raise SourcePolicyError("invalid_reflection_proposal")
        result = self._commit_body_action(proposal, trace_id=trace_id)
        if not isinstance(result, ReflectionBodyAction):
            raise SourcePolicyError("body_erasure_request_conflict")
        return result

    def commit_appraisal_action(
        self, proposal: AppraisalBodyProposal, *, trace_id: str
    ) -> AppraisalBodyAction:
        if not isinstance(proposal, AppraisalBodyProposal):
            raise SourcePolicyError("invalid_appraisal_proposal")
        result = self._commit_body_action(proposal, trace_id=trace_id)
        if not isinstance(result, AppraisalBodyAction):
            raise SourcePolicyError("body_erasure_request_conflict")
        return result

    def commit_checkpoint_reason_action(
        self, proposal: CheckpointReasonProposal, *, trace_id: str
    ) -> CheckpointReasonAction:
        if not isinstance(proposal, CheckpointReasonProposal):
            raise SourcePolicyError("invalid_checkpoint_reason_proposal")
        result = self._commit_body_action(proposal, trace_id=trace_id)
        if not isinstance(result, CheckpointReasonAction):
            raise SourcePolicyError("body_erasure_request_conflict")
        return result

    def _commit_body_action(self, proposal: BodyProposal, *, trace_id: str) -> BodyAction:
        try:
            with self._connection(write=True) as connection:
                if self._revision(connection) not in BODY_POLICY_REVISIONS:
                    raise SourcePolicyError("policy_body_schema_upgrade_required")
                if (
                    isinstance(proposal, DialogueBodyProposal)
                    and self._revision(connection) not in DIALOGUE_POLICY_REVISIONS
                ):
                    raise SourcePolicyError("policy_dialogue_schema_upgrade_required")
                if (
                    isinstance(proposal, KnowledgeBodyProposal)
                    and self._revision(connection) not in KNOWLEDGE_POLICY_REVISIONS
                ):
                    raise SourcePolicyError("policy_knowledge_schema_upgrade_required")
                if (
                    isinstance(proposal, ReflectionBodyProposal)
                    and self._revision(connection) not in REFLECTION_POLICY_REVISIONS
                ):
                    raise SourcePolicyError("policy_reflection_schema_upgrade_required")
                if (
                    isinstance(proposal, AppraisalBodyProposal)
                    and self._revision(connection) not in APPRAISAL_POLICY_REVISIONS
                ):
                    raise SourcePolicyError("policy_appraisal_schema_upgrade_required")
                if (
                    isinstance(proposal, CheckpointReasonProposal)
                    and self._revision(connection) not in CHECKPOINT_REASON_POLICY_REVISIONS
                ):
                    raise SourcePolicyError("policy_checkpoint_reason_schema_upgrade_required")
                if (
                    connection.execute(
                        "SELECT 1 FROM policy_receipts WHERE request_id=?", (proposal.request_id,)
                    ).fetchone()
                    is not None
                    or connection.execute(
                        "SELECT 1 FROM policy_erasure_intents WHERE request_id=?",
                        (proposal.request_id,),
                    ).fetchone()
                    is not None
                ):
                    raise SourcePolicyError("body_erasure_request_conflict")
                existing = connection.execute(
                    "SELECT * FROM policy_body_actions WHERE request_id=?", (proposal.request_id,)
                ).fetchone()
                if existing is not None:
                    action = self._body_action(existing)
                    if action.proposal != proposal:
                        raise SourcePolicyError("body_erasure_request_conflict")
                    return action
                root = source_proposal_root(proposal)
                identity_id = proposal_identity(proposal)
                lineage_id = proposal_lineage(proposal)
                lineage = connection.execute(
                    "SELECT lineage_id,generation,erasure_revision,body_revision "
                    "FROM policy_lineages WHERE identity_id=?",
                    (identity_id,),
                ).fetchone()
                if lineage is None or lineage[0] != lineage_id:
                    raise SourcePolicyError("policy_lineage_binding_mismatch")
                for actual, expected, reason in (
                    (lineage[1], proposal.expected_source_generation, "source_generation_conflict"),
                    (lineage[2], proposal.expected_erasure_revision, "erasure_revision_conflict"),
                    (lineage[3], proposal.expected_body_revision, "body_revision_conflict"),
                ):
                    if actual != expected:
                        raise SourcePolicyError(reason)
                if not isinstance(proposal, CheckpointReasonProposal):
                    assert root is not None
                    intent_row = connection.execute(
                        "SELECT * FROM policy_erasure_intents WHERE request_id=?",
                        (proposal.intent_request_id,),
                    ).fetchone()
                    if intent_row is None:
                        raise SourcePolicyError("body_erasure_intent_required")
                    if self._erasure_intent(intent_row).root != root:
                        raise SourcePolicyError("body_erasure_intent_scope_mismatch")
                    excluded = connection.execute(
                        "SELECT counterparty_id,session_id,interaction_id FROM policy_exclusions "
                        "WHERE identity_id=? AND message_id=?",
                        (root.identity_id, root.message_id),
                    ).fetchone()
                    if excluded is None or tuple(excluded) != (
                        root.counterparty_id,
                        root.session_id,
                        root.interaction_id,
                    ):
                        raise SourcePolicyError("source_root_binding_mismatch")
                all_rows = body_rows(connection)
                if (
                    len(all_rows) >= MAX_BODY_ACTIONS
                    or sum(proposal_references(self._body_action(row).proposal) for row in all_rows)
                    + proposal_references(proposal)
                    > MAX_BODY_REFERENCES
                ):
                    raise SourcePolicyError("policy_body_capacity_exceeded")
                encoded = encode_targets(proposal.targets)
                binding = self.read_binding()
                if isinstance(proposal, CheckpointReasonProposal):
                    action = CheckpointReasonAction(
                        proposal,
                        str(uuid4()),
                        binding.floor_id,
                        binding.installation_id,
                        proposal.expected_body_revision + 1,
                        datetime.now(UTC),
                        trace_id,
                    )
                elif isinstance(proposal, AppraisalBodyProposal):
                    action = AppraisalBodyAction(
                        proposal,
                        str(uuid4()),
                        binding.floor_id,
                        binding.installation_id,
                        proposal.expected_body_revision + 1,
                        datetime.now(UTC),
                        trace_id,
                    )
                elif isinstance(proposal, ReflectionBodyProposal):
                    action = ReflectionBodyAction(
                        proposal,
                        str(uuid4()),
                        binding.floor_id,
                        binding.installation_id,
                        proposal.expected_body_revision + 1,
                        datetime.now(UTC),
                        trace_id,
                    )
                elif isinstance(proposal, KnowledgeBodyProposal):
                    action = KnowledgeBodyAction(
                        proposal,
                        str(uuid4()),
                        binding.floor_id,
                        binding.installation_id,
                        proposal.expected_body_revision + 1,
                        datetime.now(UTC),
                        trace_id,
                    )
                elif isinstance(proposal, DialogueBodyProposal):
                    action = DialogueBodyAction(
                        proposal,
                        str(uuid4()),
                        binding.floor_id,
                        binding.installation_id,
                        proposal.expected_body_revision + 1,
                        datetime.now(UTC),
                        trace_id,
                    )
                else:
                    action = BodyErasureAction(
                        proposal,
                        str(uuid4()),
                        binding.floor_id,
                        binding.installation_id,
                        proposal.expected_body_revision + 1,
                        datetime.now(UTC),
                        trace_id,
                    )
                try:
                    validate_knowledge_origins(
                        (*(self._body_action(row) for row in all_rows), action)
                    )
                except (ValueError, TypeError, KeyError):
                    raise SourcePolicyError("knowledge_witness_predecessor_conflict") from None
                changed = connection.execute(
                    "UPDATE policy_lineages SET body_revision=? "
                    "WHERE identity_id=? AND body_revision=? "
                    "AND generation=? AND erasure_revision=?",
                    (
                        action.result_body_revision,
                        identity_id,
                        proposal.expected_body_revision,
                        proposal.expected_source_generation,
                        proposal.expected_erasure_revision,
                    ),
                ).rowcount
                if changed != 1:
                    raise SourcePolicyError("body_revision_conflict")
                connection.execute(
                    "INSERT INTO policy_body_actions "
                    "(request_id,action_id,identity_id,lineage_id,counterparty_id,session_id,"
                    "interaction_id,message_id,intent_request_id,expected_source_generation,"
                    "expected_erasure_revision,expected_body_revision,result_body_revision,"
                    "approval_digest,store_digest,schema_digest,targets,policy_version,"
                    "decided_at,trace_id) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        proposal.request_id,
                        action.action_id,
                        identity_id,
                        lineage_id,
                        root.counterparty_id if root is not None else None,
                        root.session_id if root is not None else None,
                        root.interaction_id if root is not None else None,
                        root.message_id if root is not None else None,
                        None
                        if isinstance(proposal, CheckpointReasonProposal)
                        else proposal.intent_request_id,
                        proposal.expected_source_generation,
                        proposal.expected_erasure_revision,
                        proposal.expected_body_revision,
                        action.result_body_revision,
                        proposal.approval_digest,
                        proposal.store_digest,
                        proposal.schema_digest,
                        encoded,
                        proposal.policy_version,
                        action.decided_at.isoformat(),
                        trace_id,
                    ),
                )
        except SourcePolicyError as failure:
            if failure.reason_code == "policy_storage_unavailable":
                recovered = self.body_action(proposal.request_id)
                if recovered is not None and recovered.proposal == proposal:
                    return recovered
            raise
        committed = self.body_action(proposal.request_id)
        if committed is None or committed.proposal != proposal:
            raise SourcePolicyError("policy_write_outcome_unknown")
        return committed

    def commit_exclusion(
        self,
        root: CanonicalRootRef,
        *,
        expected_generation: int,
        request_id: str,
        preview_digest: str,
        trace_id: str,
    ) -> SourceExclusionReceipt:
        try:
            with self._connection(write=True) as connection:
                if self._body_request_exists(connection, request_id):
                    raise SourcePolicyError("source_request_conflict")
                if (
                    self._revision(connection) in ERASURE_POLICY_REVISIONS
                    and connection.execute(
                        "SELECT 1 FROM policy_erasure_intents WHERE request_id=?", (request_id,)
                    ).fetchone()
                    is not None
                ):
                    raise SourcePolicyError("source_request_conflict")
                existing = connection.execute(
                    "SELECT * FROM policy_receipts WHERE request_id=?", (request_id,)
                ).fetchone()
                if existing is not None:
                    receipt = self._receipt(existing, self.read_binding().floor_id)
                    if (
                        receipt.root != root
                        or receipt.expected_generation != expected_generation
                        or receipt.preview_digest != preview_digest
                    ):
                        raise SourcePolicyError("source_request_conflict")
                    return receipt
                lineage = connection.execute(
                    "SELECT lineage_id,generation FROM policy_lineages WHERE identity_id=?",
                    (root.identity_id,),
                ).fetchone()
                if lineage is None or lineage[0] != root.lineage_id:
                    raise SourcePolicyError("policy_lineage_binding_mismatch")
                if lineage[1] != expected_generation:
                    raise SourcePolicyError("source_generation_conflict")
                excluded = connection.execute(
                    "SELECT * FROM policy_exclusions WHERE identity_id=? AND message_id=?",
                    (root.identity_id, root.message_id),
                ).fetchone()
                generation = expected_generation
                if excluded is None:
                    if expected_generation >= MAX_EXCLUDED_ROOTS:
                        raise SourcePolicyError("policy_floor_capacity_exceeded")
                    generation += 1
                    connection.execute(
                        "UPDATE policy_lineages SET generation=? WHERE identity_id=?",
                        (generation, root.identity_id),
                    )
                    connection.execute(
                        "INSERT INTO policy_exclusions VALUES (?,?,?,?,?,?,?)",
                        (
                            root.message_id,
                            root.identity_id,
                            root.counterparty_id,
                            root.session_id,
                            root.interaction_id,
                            generation,
                            request_id,
                        ),
                    )
                elif (
                    excluded["counterparty_id"],
                    excluded["session_id"],
                    excluded["interaction_id"],
                ) != (root.counterparty_id, root.session_id, root.interaction_id):
                    raise SourcePolicyError("source_root_binding_mismatch")
                connection.execute(
                    "INSERT INTO policy_receipts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        request_id,
                        str(uuid4()),
                        root.identity_id,
                        root.lineage_id,
                        root.counterparty_id,
                        root.session_id,
                        root.interaction_id,
                        root.message_id,
                        expected_generation,
                        generation,
                        preview_digest,
                        datetime.now(UTC).isoformat(),
                        trace_id,
                        ExclusionPhase.REPAIR_PENDING.value,
                    ),
                )
        except SourcePolicyError as failure:
            # A failed COMMIT can have an unknown outcome. Resolve the same durable binding;
            # absence/unreadable storage stays fail-closed rather than issuing a fresh operation.
            if failure.reason_code == "policy_storage_unavailable":
                recovered = self.receipt(request_id)
                if recovered is not None and (
                    recovered.root == root
                    and recovered.expected_generation == expected_generation
                    and recovered.preview_digest == preview_digest
                ):
                    return recovered
            raise
        committed_receipt = self.receipt(request_id)
        if committed_receipt is None:
            raise SourcePolicyError("policy_write_outcome_unknown")
        return committed_receipt

    def set_phase(self, request_id: str, phase: ExclusionPhase) -> SourceExclusionReceipt:
        with self._connection(write=True) as connection:
            changed = connection.execute(
                "UPDATE policy_receipts SET phase=? WHERE request_id=?", (phase.value, request_id)
            ).rowcount
            if changed != 1:
                raise SourcePolicyError("source_receipt_missing")
        receipt = self.receipt(request_id)
        if receipt is None:
            raise SourcePolicyError("policy_write_outcome_unknown")
        return receipt
