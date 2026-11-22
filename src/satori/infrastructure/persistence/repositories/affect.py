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
from satori.domain.affect_history import (
    AffectiveTransitionHeader,
    AffectiveTransitionHistory,
    validate_affective_numeric_record,
)
from satori.domain.erasure_appraisal import (
    MAX_APPRAISAL_TARGETS,
    AppraisalBodyAction,
    AppraisalBodyTarget,
)
from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.persistence.erasure_body_codec import decode_body_action
from satori.infrastructure.persistence.models.affect import (
    AffectiveStateRow,
    AffectiveTransitionRow,
)
from satori.infrastructure.persistence.models.conversation import (
    ConversationInteractionRow,
    ConversationMessageRow,
    ConversationSessionRow,
)
from satori.infrastructure.persistence.models.erasure_bodies import ErasureBodyApplicationRow
from satori.infrastructure.persistence.models.initial_self import AuditEventRow
from satori.infrastructure.persistence.models.source_policy import SourceInputRow
from satori.infrastructure.persistence.source_policy import (
    _require_historical_source_inputs,
    policy_snapshot,
    require_source_inputs,
)


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
        header = self._header_for_interaction(interaction_id)
        if (
            header is None
            or header.explanation_state != "available"
            or not self._input_available(header)
        ):
            return None
        row = self._session.scalar(
            select(AffectiveTransitionRow)
            .where(
                AffectiveTransitionRow.interaction_id == interaction_id,
                AffectiveTransitionRow.explanation_state == "available",
                AffectiveTransitionRow.explanation_action_id.is_(None),
                AffectiveTransitionRow.reason_codes.is_not(None),
            )
            .execution_options(populate_existing=True)
        )
        return self._map_transition(row) if row is not None else None

    def list_transitions(self, *, limit: int | None = None) -> Sequence[AffectiveTransition]:
        self._check_limit(limit)
        results = []
        for header in self.list_transition_headers():
            record = self.get_transition_for_interaction(header.interaction_id)
            if record is not None:
                results.append(record)
                if len(results) == limit:
                    break
        return tuple(results)

    @staticmethod
    def _check_limit(limit: int | None) -> None:
        if limit is not None and (type(limit) is not int or limit < 1):
            raise ValueError("transition history limit must be positive")

    def _header_for_interaction(self, interaction_id: str) -> AffectiveTransitionHeader | None:
        columns = tuple(
            AffectiveTransitionRow.__table__.c[name]
            for name in AffectiveTransitionHeader.__dataclass_fields__
        )
        row = (
            self._session.execute(
                select(*columns).where(AffectiveTransitionRow.interaction_id == interaction_id)
            )
            .mappings()
            .one_or_none()
        )
        return AffectiveTransitionHeader(**row) if row is not None else None

    def list_transition_headers(
        self, *, limit: int | None = None
    ) -> tuple[AffectiveTransitionHeader, ...]:
        self._check_limit(limit)
        columns = tuple(
            AffectiveTransitionRow.__table__.c[name]
            for name in AffectiveTransitionHeader.__dataclass_fields__
        )
        query = select(*columns).order_by(
            AffectiveTransitionRow.committed_at.desc(), AffectiveTransitionRow.transition_id.desc()
        )
        if limit is not None:
            query = query.limit(limit)
        return tuple(
            AffectiveTransitionHeader(**row) for row in self._session.execute(query).mappings()
        )

    def list_numerical_history(
        self, *, limit: int | None = None
    ) -> tuple[AffectiveTransitionHistory, ...]:
        self._check_limit(limit)
        columns = tuple(
            column for column in AffectiveTransitionRow.__table__.c if column.name != "reason_codes"
        )
        query = select(*columns).order_by(
            AffectiveTransitionRow.committed_at.desc(), AffectiveTransitionRow.transition_id.desc()
        )
        if limit is not None:
            query = query.limit(limit)
        return tuple(
            validate_affective_numeric_record(dict(row))
            for row in self._session.execute(query).mappings()
        )

    def _input_available(self, header: AffectiveTransitionHeader) -> bool:
        input_id = header.source_input_id
        if policy_snapshot(self._session) is None:
            return True
        if input_id is None:
            return False
        row = self._session.get(SourceInputRow, input_id)
        if (
            row is None
            or row.proof_status != "complete"
            or row.legacy
            or row.identity_id != header.identity_id
            or header.source_message_id not in row.root_message_ids
            or not self._primary_scope_matches(
                identity_id=header.identity_id,
                interaction_id=header.interaction_id,
                message_id=header.source_message_id,
                completed=True,
            )
        ):
            return False
        try:
            _require_historical_source_inputs(self._session, input_id)
        except SourcePolicyError:
            return False
        return True

    def _primary_scope_matches(
        self,
        *,
        identity_id: str,
        interaction_id: str,
        message_id: str,
        counterparty_id: str | None = None,
        completed: bool = False,
    ) -> bool:
        query = (
            select(ConversationMessageRow.message_id)
            .join(
                ConversationInteractionRow,
                ConversationInteractionRow.interaction_id == ConversationMessageRow.interaction_id,
            )
            .join(
                ConversationSessionRow,
                ConversationSessionRow.session_id == ConversationInteractionRow.session_id,
            )
            .where(
                ConversationMessageRow.message_id == message_id,
                ConversationMessageRow.interaction_id == interaction_id,
                ConversationMessageRow.session_id == ConversationSessionRow.session_id,
                ConversationMessageRow.role == "user",
                ConversationMessageRow.sequence == 1,
                ConversationMessageRow.schema_version == 1,
                ConversationInteractionRow.schema_version == 1,
                ConversationSessionRow.schema_version == 1,
                ConversationSessionRow.scope_verified.is_(True),
                ConversationSessionRow.identity_id == identity_id,
                ConversationInteractionRow.status == "completed"
                if completed
                else ConversationInteractionRow.status.in_(("pending", "completed")),
            )
        )
        if counterparty_id is not None:
            query = query.where(ConversationSessionRow.counterparty_id == counterparty_id)
        return self._session.scalar(query) is not None

    def _validate_input_link(self, transition: AffectiveTransition, input_id: str | None) -> None:
        managed = policy_snapshot(self._session) is not None
        if input_id is None:
            if managed:
                raise SourcePolicyError("source_input_required")
            return
        source = self._session.get(SourceInputRow, input_id)
        if (
            source is None
            or source.identity_id != transition.identity_id
            or source.proof_status != "complete"
            or source.legacy
            or transition.source_message_id not in source.root_message_ids
            or not self._primary_scope_matches(
                identity_id=transition.identity_id,
                interaction_id=transition.interaction_id,
                message_id=transition.source_message_id,
            )
        ):
            raise SourcePolicyError("appraisal_body_provenance_unproven")

    def erase_explanation_bodies(
        self, targets: tuple[AppraisalBodyTarget, ...], *, action_id: str
    ) -> None:
        from satori.infrastructure.persistence.appraisal_material import read_appraisal_material

        if (
            not 1 <= len(targets) <= MAX_APPRAISAL_TARGETS
            or any(not isinstance(target, AppraisalBodyTarget) for target in targets)
            or len({target.transition_id for target in targets}) != len(targets)
        ):
            raise SourcePolicyError("appraisal_body_target_invalid")
        receipt = self._session.get(ErasureBodyApplicationRow, action_id)
        if receipt is None:
            raise SourcePolicyError("body_action_missing")
        try:
            approved = decode_body_action(receipt.action)
        except (ValueError, TypeError, KeyError, RecursionError):
            raise SourcePolicyError("body_action_target_mismatch") from None
        if not isinstance(approved, AppraisalBodyAction) or (
            approved.action_id != receipt.action_id
            or approved.floor_id != receipt.floor_id
            or approved.proposal.root.identity_id != receipt.identity_id
            or approved.proposal.root.message_id != receipt.root_message_id
            or approved.proposal.root.lineage_id != receipt.lineage_id
            or approved.proposal.request_id != receipt.request_id
            or approved.result_body_revision != receipt.body_revision
            or any(target not in approved.proposal.targets for target in targets)
        ):
            raise SourcePolicyError("body_action_target_mismatch")
        index = read_appraisal_material(
            self._session, tuple(target.transition_id for target in targets), require_terminal=False
        )
        for target in targets:
            transition = index.transitions.get(target.transition_id)
            if (
                transition is None
                or not self._primary_scope_matches(
                    identity_id=approved.proposal.root.identity_id,
                    interaction_id=str(transition["interaction_id"]),
                    message_id=str(transition["source_message_id"]),
                    counterparty_id=approved.proposal.root.counterparty_id,
                    completed=True,
                )
                or transition["identity_id"] != approved.proposal.root.identity_id
                or transition["explanation_state"] != "available"
                or transition["explanation_action_id"] is not None
                or transition["source_input_id"] != target.source_input_id
                or transition["audit_event_id"] != target.audit_event_id
                or index.digest(target.transition_id, terminal=False) != target.body_digest
                or index.digest(target.transition_id, terminal=True) != target.terminal_digest
                or index.metadata_reference_count(target.transition_id)
                != target.metadata_reference_count
            ):
                raise SourcePolicyError("body_owner_state_changed")
        connection = self._session.connection()
        for target in targets:
            changed = connection.exec_driver_sql(
                "UPDATE audit_events SET "
                "details=json_remove(details,'$.appraisal_reason_codes') WHERE event_id=?",
                (target.audit_event_id,),
            )
            if changed.rowcount != 1:
                raise SourcePolicyError("body_owner_state_changed")
            changed = connection.exec_driver_sql(
                "UPDATE affective_transitions SET "
                "reason_codes=NULL,explanation_state='erased',explanation_action_id=? "
                "WHERE transition_id=? AND explanation_state='available' AND "
                "explanation_action_id IS NULL",
                (action_id, target.transition_id),
            )
            if changed.rowcount != 1:
                raise SourcePolicyError("body_owner_state_changed")
        self._session.expire_all()

    def apply_transition(
        self,
        transition: AffectiveTransition,
        *,
        audit_event_id: str,
        source_input_id: str | None = None,
    ) -> bool:
        existing = self._header_for_interaction(transition.interaction_id)
        if existing is not None:
            return False
        require_source_inputs(self._session, source_input_id)
        self._validate_input_link(transition, source_input_id)
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
            if self._header_for_interaction(transition.interaction_id) is not None:
                return False
            raise AffectiveStateConflict(
                "affective state changed after tentative expression was generated"
            )
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
        self._session.add(
            self._transition_row(
                transition, source_input_id=source_input_id, audit_event_id=audit_event_id
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
    def _transition_row(
        cls,
        transition: AffectiveTransition,
        *,
        source_input_id: str | None = None,
        audit_event_id: str | None = None,
    ) -> AffectiveTransitionRow:
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
            source_input_id=source_input_id,
            audit_event_id=audit_event_id,
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
        if (
            row.explanation_state != "available"
            or row.explanation_action_id is not None
            or row.reason_codes is None
        ):
            raise SourcePolicyError("appraisal_explanation_unavailable")
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
