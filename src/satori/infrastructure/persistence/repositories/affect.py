"""SQLAlchemy adapter for authoritative Stage 7 affective state."""

from collections.abc import Sequence
from typing import cast

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from satori.core.affect import AffectiveAppraisalProposal
from satori.domain.affect import (
    AffectiveDelta,
    AffectiveStateConflict,
    AffectiveStateSnapshot,
    AffectiveTransition,
    FastAffectiveState,
    MoodDelta,
    MoodState,
)
from satori.infrastructure.persistence.models.affect import (
    AffectiveStateRow,
    AffectiveTransitionRow,
)
from satori.infrastructure.persistence.models.initial_self import AuditEventRow


def _as_float(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(f"persistent {field_name} must be numeric")
    return float(value)


class SQLAlchemyAffectiveStateRepository:
    """Map immutable affect values and enforce optimistic single-writer commits."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_state(self, identity_id: str) -> AffectiveStateSnapshot | None:
        row = self._session.get(AffectiveStateRow, identity_id)
        return self._map_state(row) if row is not None else None

    def add_initial_state(self, state: AffectiveStateSnapshot) -> bool:
        statement = (
            sqlite_insert(AffectiveStateRow)
            .values(**self._state_values(state))
            .on_conflict_do_nothing(index_elements=["identity_id"])
            .returning(AffectiveStateRow.identity_id)
        )
        return self._session.execute(statement).scalar_one_or_none() is not None

    def get_transition_for_interaction(self, interaction_id: str) -> AffectiveTransition | None:
        row = self._session.execute(
            select(AffectiveTransitionRow).where(
                AffectiveTransitionRow.interaction_id == interaction_id
            )
        ).scalar_one_or_none()
        return self._map_transition(row) if row is not None else None

    def list_transitions(self, *, limit: int | None = None) -> Sequence[AffectiveTransition]:
        query = select(AffectiveTransitionRow).order_by(
            AffectiveTransitionRow.committed_at.desc(),
            AffectiveTransitionRow.transition_id.desc(),
        )
        if limit is not None:
            if type(limit) is not int or limit < 1:
                raise ValueError("transition history limit must be positive")
            query = query.limit(limit)
        rows = tuple(self._session.execute(query).scalars())
        return tuple(self._map_transition(row) for row in rows)

    def apply_transition(self, transition: AffectiveTransition, *, audit_event_id: str) -> bool:
        existing = self.get_transition_for_interaction(transition.interaction_id)
        if existing is not None:
            return False
        after = transition.after
        values = self._state_values(after)
        values.pop("identity_id")
        updated_identity = self._session.execute(
            update(AffectiveStateRow)
            .where(
                AffectiveStateRow.identity_id == transition.identity_id,
                AffectiveStateRow.state_version == transition.before.state_version,
                AffectiveStateRow.mood_version == transition.before.mood_version,
            )
            .values(**values)
            .returning(AffectiveStateRow.identity_id)
        ).scalar_one_or_none()
        if updated_identity is None:
            if self.get_transition_for_interaction(transition.interaction_id) is not None:
                return False
            raise AffectiveStateConflict(
                "affective state changed after tentative expression was generated"
            )
        self._session.add(self._transition_row(transition))
        self._session.add(
            AuditEventRow(
                event_id=audit_event_id,
                schema_version=1,
                event_type="emotion.transition_applied",
                aggregate_type="affective_state",
                aggregate_id=transition.identity_id,
                occurred_at=transition.committed_at,
                trace_id=transition.trace_id,
                details={
                    "transition_id": transition.transition_id,
                    "interaction_id": transition.interaction_id,
                    "source_message_id": transition.source_message_id,
                    "base_state_version": transition.before.state_version,
                    "resulting_state_version": transition.after.state_version,
                    "base_mood_version": transition.before.mood_version,
                    "resulting_mood_version": transition.after.mood_version,
                    "emotion_policy_version": transition.after.emotion_policy_version,
                    "appraisal_schema_version": transition.after.appraisal_schema_version,
                    "mood_policy_version": transition.after.mood_policy_version,
                    "source_refs": list(transition.proposal.source_refs),
                    "decision_reason_code": "bounded_appraisal_applied",
                    "appraisal_reason_codes": list(transition.proposal.reason_codes),
                },
            )
        )
        self._session.flush()
        return True

    @staticmethod
    def _state_values(state: AffectiveStateSnapshot) -> dict[str, object]:
        return {
            "identity_id": state.identity_id,
            "schema_version": state.schema_version,
            "state_version": state.state_version,
            "mood_version": state.mood_version,
            "as_of": state.as_of,
            "emotion_policy_version": state.emotion_policy_version,
            "appraisal_schema_version": state.appraisal_schema_version,
            "mood_policy_version": state.mood_policy_version,
            **state.fast.as_mapping(),
            "mood_valence": state.mood.valence,
            "mood_energy": state.mood.energy,
            "mood_tension": state.mood.tension,
        }

    @staticmethod
    def _map_state(row: AffectiveStateRow) -> AffectiveStateSnapshot:
        return AffectiveStateSnapshot(
            identity_id=row.identity_id,
            schema_version=row.schema_version,
            state_version=row.state_version,
            mood_version=row.mood_version,
            as_of=row.as_of,
            emotion_policy_version=row.emotion_policy_version,
            appraisal_schema_version=row.appraisal_schema_version,
            mood_policy_version=row.mood_policy_version,
            fast=FastAffectiveState(
                valence=row.valence,
                arousal=row.arousal,
                tension=row.tension,
                curiosity=row.curiosity,
                interest=row.interest,
                amusement=row.amusement,
                concern=row.concern,
                frustration=row.frustration,
                situational_confidence=row.situational_confidence,
            ),
            mood=MoodState(
                valence=row.mood_valence,
                energy=row.mood_energy,
                tension=row.mood_tension,
            ),
        )

    @classmethod
    def _transition_row(cls, transition: AffectiveTransition) -> AffectiveTransitionRow:
        proposal = transition.proposal
        return AffectiveTransitionRow(
            transition_id=transition.transition_id,
            identity_id=transition.identity_id,
            interaction_id=transition.interaction_id,
            source_message_id=transition.source_message_id,
            trace_id=transition.trace_id,
            appraisal_schema_version=proposal.schema_version,
            emotion_policy_version=transition.after.emotion_policy_version,
            mood_policy_version=transition.after.mood_policy_version,
            base_state_version=transition.before.state_version,
            resulting_state_version=transition.after.state_version,
            base_mood_version=transition.before.mood_version,
            resulting_mood_version=transition.after.mood_version,
            appraised_at=transition.after.as_of,
            committed_at=transition.committed_at,
            appraisal_confidence=proposal.appraisal_confidence,
            appraisal_payload={
                "pleasantness": proposal.pleasantness,
                "activation": proposal.activation,
                "novelty": proposal.novelty,
                "salience": proposal.salience,
                "uncertainty": proposal.uncertainty,
                "curiosity_signal": proposal.curiosity_signal,
                "interest_signal": proposal.interest_signal,
                "humor_signal": proposal.humor_signal,
                "concern_signal": proposal.concern_signal,
                "frustration_signal": proposal.frustration_signal,
                "confidence_signal": proposal.confidence_signal,
            },
            source_refs=list(proposal.source_refs),
            reason_codes=list(proposal.reason_codes),
            applied_delta=transition.applied_delta.as_mapping(),
            mood_delta=transition.mood_delta.as_mapping(),
            state_before=cls._snapshot_payload(transition.before),
            state_after=cls._snapshot_payload(transition.after),
            provider=transition.provider,
            model=transition.model,
            appraisal_method=transition.appraisal_method,
        )

    @staticmethod
    def _snapshot_payload(state: AffectiveStateSnapshot) -> dict[str, object]:
        return {
            "identity_id": state.identity_id,
            "schema_version": state.schema_version,
            "state_version": state.state_version,
            "mood_version": state.mood_version,
            "as_of": state.as_of.isoformat(),
            "emotion_policy_version": state.emotion_policy_version,
            "appraisal_schema_version": state.appraisal_schema_version,
            "mood_policy_version": state.mood_policy_version,
            "fast": state.fast.as_mapping(),
            "mood": state.mood.as_mapping(),
        }

    @classmethod
    def _map_transition(cls, row: AffectiveTransitionRow) -> AffectiveTransition:
        payload = row.appraisal_payload
        proposal = AffectiveAppraisalProposal(
            schema_version=row.appraisal_schema_version,
            pleasantness=_as_float(payload["pleasantness"], "pleasantness"),
            activation=_as_float(payload["activation"], "activation"),
            novelty=_as_float(payload["novelty"], "novelty"),
            salience=_as_float(payload["salience"], "salience"),
            uncertainty=_as_float(payload["uncertainty"], "uncertainty"),
            curiosity_signal=_as_float(payload["curiosity_signal"], "curiosity_signal"),
            interest_signal=_as_float(payload["interest_signal"], "interest_signal"),
            humor_signal=_as_float(payload["humor_signal"], "humor_signal"),
            concern_signal=_as_float(payload["concern_signal"], "concern_signal"),
            frustration_signal=_as_float(payload["frustration_signal"], "frustration_signal"),
            confidence_signal=_as_float(payload["confidence_signal"], "confidence_signal"),
            appraisal_confidence=row.appraisal_confidence,
            source_refs=tuple(row.source_refs),
            reason_codes=tuple(row.reason_codes),
        )
        return AffectiveTransition(
            transition_id=row.transition_id,
            identity_id=row.identity_id,
            interaction_id=row.interaction_id,
            source_message_id=row.source_message_id,
            trace_id=row.trace_id,
            proposal=proposal,
            before=cls._snapshot_from_payload(row.state_before),
            after=cls._snapshot_from_payload(row.state_after),
            applied_delta=AffectiveDelta.from_mapping(
                {key: _as_float(value, f"{key} delta") for key, value in row.applied_delta.items()}
            ),
            mood_delta=MoodDelta.from_mapping(
                {
                    key: _as_float(value, f"mood {key} delta")
                    for key, value in row.mood_delta.items()
                }
            ),
            provider=row.provider,
            model=row.model,
            appraisal_method=row.appraisal_method,
            committed_at=row.committed_at,
        )

    @staticmethod
    def _snapshot_from_payload(payload: dict[str, object]) -> AffectiveStateSnapshot:
        from datetime import datetime

        fast = cast(dict[str, object], payload["fast"])
        mood = cast(dict[str, object], payload["mood"])
        return AffectiveStateSnapshot(
            identity_id=str(payload["identity_id"]),
            schema_version=int(cast(int, payload["schema_version"])),
            state_version=int(cast(int, payload["state_version"])),
            mood_version=int(cast(int, payload["mood_version"])),
            as_of=datetime.fromisoformat(str(payload["as_of"])),
            emotion_policy_version=int(cast(int, payload["emotion_policy_version"])),
            appraisal_schema_version=int(cast(int, payload["appraisal_schema_version"])),
            mood_policy_version=int(cast(int, payload["mood_policy_version"])),
            fast=FastAffectiveState.from_mapping(
                {key: _as_float(value, key) for key, value in fast.items()}
            ),
            mood=MoodState.from_mapping(
                {key: _as_float(value, f"mood {key}") for key, value in mood.items()}
            ),
        )
