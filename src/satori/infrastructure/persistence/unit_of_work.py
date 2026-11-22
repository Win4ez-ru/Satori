"""SQLAlchemy implementation of the application transaction boundary."""

from contextlib import AbstractContextManager
from types import TracebackType
from typing import Self, cast

from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

from satori.domain.source_policy import SourceEligibilitySnapshot, SourcePolicyError
from satori.infrastructure.persistence.models.source_policy import SourcePolicyBindingRow
from satori.infrastructure.persistence.source_policy import SessionSourceAdmission, verify_mirror
from satori.infrastructure.source_policy.runtime import SourcePolicyRuntime


class SQLAlchemyUnitOfWork:
    """Keep one SQLAlchemy session scoped to an explicit unit of work."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None
        self._committed = False
        self._lease: AbstractContextManager[SourceEligibilitySnapshot] | None = None

    @property
    def session(self) -> Session:
        """Expose the session to infrastructure repositories inside the boundary."""

        if self._session is None:
            raise RuntimeError("unit of work is not active")
        return self._session

    def __enter__(self) -> Self:
        """Open a fresh session for this unit of work."""

        if self._session is not None:
            raise RuntimeError("unit of work is already active")
        info = self._session_factory.kw.get("info", {})
        runtime = cast(SourcePolicyRuntime | None, info.get("source_runtime"))
        snapshot = None
        if runtime is not None:
            self._lease = runtime.shared()
            snapshot = self._lease.__enter__()
        try:
            self._session = self._session_factory()
            if snapshot is not None:
                self._session.info["source_snapshot"] = snapshot
                verify_mirror(self._session, snapshot)
            if (
                snapshot is None
                and self._policy_schema_present()
                and self._session.scalar(select(SourcePolicyBindingRow.identity_id).limit(1))
                is not None
            ):
                raise SourcePolicyError("source_policy_binding_required")
            self._committed = False
            return self
        except BaseException:
            if self._session is not None:
                self._session.close()
                self._session = None
            if self._lease is not None:
                self._lease.__exit__(None, None, None)
                self._lease = None
            raise

    def _policy_schema_present(self) -> bool:
        """Pure pre-policy core fixtures stay usable; damaged policy schemas never downgrade."""
        inspection = inspect(self.session.connection())
        if inspection.has_table("alembic_version"):
            revisions = set(self.session.scalars(text("SELECT version_num FROM alembic_version")))
            if revisions & {
                "0023_audit_metadata",
                "0024_checkpoint_reasons",
            } and not inspection.has_table("audit_metadata_bindings"):
                raise SourcePolicyError("audit_metadata_schema_required")
        if inspection.has_table("source_policy_bindings"):
            return True
        policy_tables = (
            "source_excluded_roots",
            "source_inputs",
            "source_payload_receipts",
            "owner_source_withdrawals",
            "source_owner_repairs",
            "erasure_policy_bindings",
            "erasure_preparations",
            "erasure_body_bindings",
            "erasure_body_applications",
            "audit_metadata_bindings",
        )
        if any(inspection.has_table(name) for name in policy_tables):
            raise SourcePolicyError("source_policy_schema_incomplete")
        if inspection.has_table("alembic_version"):
            persisted_revisions = self.session.scalars(
                text("SELECT version_num FROM alembic_version")
            )
            if any(
                revision
                in {
                    "0016_source_policy",
                    "0017_erasure_intent",
                    "0018_erasure_bodies",
                    "0019_dialogue_bodies",
                    "0020_knowledge_bodies",
                    "0021_reflection_bodies",
                    "0022_appraisal_bodies",
                    "0023_audit_metadata",
                    "0024_checkpoint_reasons",
                }
                for revision in persisted_revisions
            ):
                raise SourcePolicyError("source_policy_schema_incomplete")
        # Operational composition binds/upgrades before providers; this branch is only the
        # explicit unmanaged core path and does not enroll or infer an allow floor.
        return False

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Roll back any uncommitted work, then close the session."""

        if self._session is None:
            return
        try:
            if exc_type is not None or not self._committed:
                self._session.rollback()
        finally:
            self._session.close()
            self._session = None
            self._committed = False
            if self._lease is not None:
                self._lease.__exit__(exc_type, exc_value, traceback)
                self._lease = None

    @property
    def source_admission(self) -> SessionSourceAdmission | None:
        return (
            SessionSourceAdmission(self.session) if "source_snapshot" in self.session.info else None
        )

    def commit(self) -> None:
        """Commit the active transaction."""

        self.session.commit()
        self._committed = True

    def rollback(self) -> None:
        """Roll back the active transaction."""

        self.session.rollback()
        self._committed = False
