"""SQLAlchemy mapping for the complete Stage 2 initial self."""

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from satori.domain.audit import ActivationAuditEvent
from satori.domain.errors import CorruptSatoriState
from satori.domain.identity import Identity, SeedProvenance
from satori.domain.initial_self import InitialSelfSnapshot
from satori.domain.personality import Personality, PersonalityTrait
from satori.domain.personality_evolution import (
    PERSONALITY_CHECKPOINT_HASH_SCHEMA_VERSION,
    PersonalityCheckpointKind,
    checkpoint_hash,
)
from satori.domain.values import CoreValue, ValueOrigin, Values
from satori.infrastructure.persistence.models.initial_self import (
    AuditEventRow,
    PersonalityStateRow,
    PersonalityTraitRow,
    SatoriIdentityRow,
    ValueRow,
    ValueSetRow,
)
from satori.infrastructure.persistence.models.personality import (
    PersonalityCheckpointRow,
    PersonalityCheckpointTraitRow,
)

PRIMARY_INSTALLATION_SLOT = 1


class SQLAlchemyInitialSelfRepository:
    """Keep ORM details behind the Stage 2 repository port."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self) -> InitialSelfSnapshot | None:
        """Load and map the complete initial self, detecting partial state."""

        identity_row = self._session.execute(
            select(SatoriIdentityRow).where(
                SatoriIdentityRow.installation_slot == PRIMARY_INSTALLATION_SLOT
            )
        ).scalar_one_or_none()
        if identity_row is None:
            return None

        personality_row = self._session.get(PersonalityStateRow, identity_row.identity_id)
        value_set_row = self._session.get(ValueSetRow, identity_row.identity_id)
        trait_rows = tuple(
            self._session.execute(
                select(PersonalityTraitRow)
                .where(PersonalityTraitRow.identity_id == identity_row.identity_id)
                .order_by(PersonalityTraitRow.trait_key)
            ).scalars()
        )
        value_rows = tuple(
            self._session.execute(
                select(ValueRow)
                .where(ValueRow.identity_id == identity_row.identity_id)
                .order_by(ValueRow.value_key)
            ).scalars()
        )
        activation_audits = tuple(
            self._session.execute(
                select(AuditEventRow).where(
                    AuditEventRow.event_type == "satori.activation",
                    AuditEventRow.aggregate_type == "identity",
                    AuditEventRow.aggregate_id == identity_row.identity_id,
                )
            ).scalars()
        )
        if (
            personality_row is None
            or value_set_row is None
            or not trait_rows
            or not value_rows
            or len(activation_audits) != 1
        ):
            raise CorruptSatoriState("persistent identity has incomplete Stage 2 state")

        try:
            provenance = SeedProvenance(
                seed_id=identity_row.seed_id,
                seed_schema_version=identity_row.seed_schema_version,
                seed_content_hash=identity_row.seed_content_hash,
            )
        except (TypeError, ValueError) as error:
            raise CorruptSatoriState(
                "identity seed provenance violates domain invariants"
            ) from error
        activation_audit = activation_audits[0]
        expected_audit_details = {
            "seed_id": provenance.seed_id,
            "seed_schema_version": provenance.seed_schema_version,
            "seed_content_hash": provenance.seed_content_hash,
        }
        if (
            activation_audit.occurred_at != identity_row.activation_time
            or activation_audit.details != expected_audit_details
        ):
            raise CorruptSatoriState("activation audit does not match identity provenance")

        try:
            return InitialSelfSnapshot(
                schema_version=1,
                identity=Identity(
                    identity_id=identity_row.identity_id,
                    name=identity_row.name,
                    activation_time=identity_row.activation_time,
                    identity_version=identity_row.identity_version,
                    seed_provenance=provenance,
                ),
                personality=Personality(
                    schema_version=personality_row.schema_version,
                    aggregate_version=personality_row.aggregate_version,
                    traits=tuple(
                        PersonalityTrait(
                            key=row.trait_key,
                            value=row.value,
                            baseline_value=row.baseline_value,
                        )
                        for row in trait_rows
                    ),
                ),
                values=Values(
                    schema_version=value_set_row.schema_version,
                    aggregate_version=value_set_row.aggregate_version,
                    items=tuple(
                        CoreValue(
                            key=row.value_key,
                            strength=row.strength,
                            description=row.description,
                            origin=ValueOrigin(row.origin),
                        )
                        for row in value_rows
                    ),
                ),
            )
        except (TypeError, ValueError) as error:
            raise CorruptSatoriState(
                "persistent Stage 2 state violates domain invariants"
            ) from error

    def add(self, snapshot: InitialSelfSnapshot, event: ActivationAuditEvent) -> bool:
        """Stage all activation records, atomically claiming the primary slot."""

        identity = snapshot.identity
        if (
            event.identity_id != identity.identity_id
            or event.occurred_at != identity.activation_time
            or event.seed_provenance != identity.seed_provenance
        ):
            raise ValueError("activation audit must describe the staged identity")
        statement = (
            sqlite_insert(SatoriIdentityRow)
            .values(
                identity_id=identity.identity_id,
                installation_slot=PRIMARY_INSTALLATION_SLOT,
                name=identity.name,
                activation_time=identity.activation_time,
                identity_version=identity.identity_version,
                seed_id=identity.seed_provenance.seed_id,
                seed_schema_version=identity.seed_provenance.seed_schema_version,
                seed_content_hash=identity.seed_provenance.seed_content_hash,
            )
            .on_conflict_do_nothing(index_elements=["installation_slot"])
            .returning(SatoriIdentityRow.identity_id)
        )
        if self._session.execute(statement).scalar_one_or_none() is None:
            return False

        self._session.add(
            PersonalityStateRow(
                identity_id=identity.identity_id,
                schema_version=snapshot.personality.schema_version,
                aggregate_version=snapshot.personality.aggregate_version,
                created_at=identity.activation_time,
            )
        )
        self._session.add(
            ValueSetRow(
                identity_id=identity.identity_id,
                schema_version=snapshot.values.schema_version,
                aggregate_version=snapshot.values.aggregate_version,
                created_at=identity.activation_time,
            )
        )

        # These aggregates intentionally have no ORM navigation relationships.
        # Flush their parent rows before staging children so FK ordering remains
        # explicit while every write still belongs to the same UoW transaction.
        self._session.flush()

        activation_checkpoint_hash = checkpoint_hash(
            identity_id=identity.identity_id,
            checkpoint_kind=PersonalityCheckpointKind.ACTIVATION,
            personality=snapshot.personality,
        )
        activation_checkpoint_id = f"personality-checkpoint-{activation_checkpoint_hash}"
        self._session.add(
            PersonalityCheckpointRow(
                checkpoint_id=activation_checkpoint_id,
                identity_id=identity.identity_id,
                personality_schema_version=snapshot.personality.schema_version,
                source_aggregate_version=snapshot.personality.aggregate_version,
                checkpoint_kind=PersonalityCheckpointKind.ACTIVATION.value,
                hash_schema_version=PERSONALITY_CHECKPOINT_HASH_SCHEMA_VERSION,
                checkpoint_hash=activation_checkpoint_hash,
                created_at=identity.activation_time,
            )
        )
        self._session.flush()

        self._session.add_all(
            PersonalityTraitRow(
                identity_id=identity.identity_id,
                trait_key=trait.key,
                value=trait.value,
                baseline_value=trait.baseline_value,
            )
            for trait in snapshot.personality.traits
        )
        self._session.add_all(
            PersonalityCheckpointTraitRow(
                checkpoint_id=activation_checkpoint_id,
                trait_key=trait.key,
                value=trait.value,
                baseline_value=trait.baseline_value,
            )
            for trait in snapshot.personality.traits
        )
        self._session.add_all(
            ValueRow(
                identity_id=identity.identity_id,
                value_key=value.key,
                strength=value.strength,
                description=value.description,
                origin=value.origin.value,
            )
            for value in snapshot.values.items
        )
        self._session.add(
            AuditEventRow(
                event_id=event.event_id,
                schema_version=event.schema_version,
                event_type="satori.activation",
                aggregate_type="identity",
                aggregate_id=event.identity_id,
                occurred_at=event.occurred_at,
                trace_id=event.trace_id,
                details={
                    "seed_id": event.seed_provenance.seed_id,
                    "seed_schema_version": event.seed_provenance.seed_schema_version,
                    "seed_content_hash": event.seed_provenance.seed_content_hash,
                },
            )
        )
        return True
