"""Prospective audit certification sidecars; historical audit rows remain unchanged."""

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from satori.domain.audit_metadata import AUDIT_METADATA_FIELDS_BY_FAMILY
from satori.infrastructure.persistence.base import Base
from satori.infrastructure.persistence.models.initial_self import AuditEventRow, SatoriIdentityRow
from satori.infrastructure.persistence.models.memory import (
    EpisodeFormationDecisionRow,
    MemoryLifecycleDecisionRow,
)
from satori.infrastructure.persistence.models.models import ModelFormationDecisionRow
from satori.infrastructure.persistence.models.personality import (
    PersonalityCheckpointApprovalRow,
    PersonalityRestoreEventRow,
)
from satori.infrastructure.persistence.models.positions import PositionFormationDecisionRow
from satori.infrastructure.persistence.models.reflection import ReflectionOutcomeRow
from satori.infrastructure.persistence.models.relationship import RelationshipDecisionRow
from satori.infrastructure.persistence.models.semantic import SemanticFormationDecisionRow
from satori.infrastructure.persistence.models.source_policy import (
    OwnerSourceWithdrawalRow,
    SourceInputRow,
)

_SLOTS = tuple(sorted({slot for slot in AUDIT_METADATA_FIELDS_BY_FAMILY.values() if slot}))
_FAMILIES = tuple(family.value for family in AUDIT_METADATA_FIELDS_BY_FAMILY)
_SLOT_SHAPE = " OR ".join(
    "(family='"
    + family.value
    + "' AND "
    + " AND ".join(
        f"{candidate} IS {'NOT NULL' if candidate == slot else 'NULL'}" for candidate in _SLOTS
    )
    + ")"
    for family, slot in AUDIT_METADATA_FIELDS_BY_FAMILY.items()
)


class AuditMetadataBindingRow(Base):
    __tablename__ = "audit_metadata_bindings"
    __table_args__ = (
        CheckConstraint("contract_version=1", name="contract_version_valid"),
        CheckConstraint(
            "family IN (" + ",".join(repr(item) for item in _FAMILIES) + ")", name="family_valid"
        ),
        CheckConstraint(_SLOT_SHAPE, name="owner_slot_consistent"),
        CheckConstraint(
            "length(event_id) BETWEEN 1 AND 128 AND length(identity_id) BETWEEN 1 AND 128",
            name="headers_bounded",
        ),
        CheckConstraint(
            "length(event_digest)=64 AND event_digest NOT GLOB '*[^0-9a-f]*'", name="digest_valid"
        ),
        *(
            UniqueConstraint("family", slot, name=f"uq_audit_metadata_bindings_family_{slot}")
            for slot in _SLOTS
        ),
        Index(
            "uq_audit_metadata_bindings_activation_identity",
            "identity_id",
            unique=True,
            sqlite_where=text("family='activation'"),
        ),
    )

    event_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("audit_events.event_id", ondelete="RESTRICT"), primary_key=True
    )
    identity_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("satori_identities.identity_id", ondelete="RESTRICT"),
        nullable=False,
    )
    contract_version: Mapped[int] = mapped_column(Integer, nullable=False)
    family: Mapped[str] = mapped_column(String(32), nullable=False)
    event_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    source_input_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("source_inputs.input_id", ondelete="RESTRICT")
    )
    episode_decision_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("episode_formation_decisions.decision_id", ondelete="RESTRICT")
    )
    lifecycle_decision_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("memory_lifecycle_decisions.decision_id", ondelete="RESTRICT")
    )
    semantic_decision_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("semantic_formation_decisions.decision_id", ondelete="RESTRICT")
    )
    model_decision_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("model_formation_decisions.decision_id", ondelete="RESTRICT")
    )
    position_decision_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("position_formation_decisions.decision_id", ondelete="RESTRICT")
    )
    reflection_outcome_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("reflection_outcomes.outcome_id", ondelete="RESTRICT")
    )
    relationship_decision_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("relationship_decisions.decision_id", ondelete="RESTRICT")
    )
    checkpoint_approval_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("personality_checkpoint_approvals.approval_id", ondelete="RESTRICT")
    )
    restore_event_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("personality_restore_events.restore_id", ondelete="RESTRICT")
    )
    withdrawal_receipt_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("owner_source_withdrawals.receipt_id", ondelete="RESTRICT")
    )

    # FK columns alone do not establish SQLAlchemy mapper flush order. Explicit
    # edges keep prospective owner/event/binding appends in the same transaction;
    # assignment never grants permission to load an owner's protected body.
    audit_event: Mapped[AuditEventRow] = relationship(lazy="raise")
    identity: Mapped[SatoriIdentityRow] = relationship(lazy="raise")
    source_input: Mapped[SourceInputRow | None] = relationship(lazy="raise")
    episode_decision: Mapped[EpisodeFormationDecisionRow | None] = relationship(lazy="raise")
    lifecycle_decision: Mapped[MemoryLifecycleDecisionRow | None] = relationship(lazy="raise")
    semantic_decision: Mapped[SemanticFormationDecisionRow | None] = relationship(lazy="raise")
    model_decision: Mapped[ModelFormationDecisionRow | None] = relationship(lazy="raise")
    position_decision: Mapped[PositionFormationDecisionRow | None] = relationship(lazy="raise")
    reflection_outcome: Mapped[ReflectionOutcomeRow | None] = relationship(lazy="raise")
    relationship_decision: Mapped[RelationshipDecisionRow | None] = relationship(lazy="raise")
    checkpoint_approval: Mapped[PersonalityCheckpointApprovalRow | None] = relationship(
        lazy="raise"
    )
    restore_event: Mapped[PersonalityRestoreEventRow | None] = relationship(lazy="raise")
    withdrawal_receipt: Mapped[OwnerSourceWithdrawalRow | None] = relationship(lazy="raise")
