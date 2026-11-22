"""Synthetic accepted storage across all five knowledge kinds; no provider mutation path."""

import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import select

from satori.core.semantic import SemanticValueKind
from satori.domain.memory_scope import scope_semantic_key
from satori.domain.positions import normalize_position_text
from satori.domain.semantic_memory import normalize_semantic_value
from satori.domain.source_policy import PayloadRef
from satori.infrastructure.persistence.models import (
    AffectiveTransitionRow,
    ConversationMessageRow,
    InclinationEvidenceRow,
    InclinationRevisionRow,
    MemoryEvidenceRow,
    ModelFormationDecisionRow,
    PositionEvidenceRow,
    PositionFormationDecisionRow,
    PositionRevisionRow,
    ReflectionOutcomeRow,
    ReflectionProposalRow,
    ReflectionRunRow,
    ReflectionSourceRow,
    SatoriInclinationRow,
    SatoriPositionRow,
    SemanticClaimEvidenceRow,
    SemanticClaimRow,
    UserModelClaimEvidenceRow,
    UserModelClaimRevisionRow,
    UserModelClaimRow,
    WorldModelClaimEvidenceRow,
    WorldModelClaimRevisionRow,
    WorldModelClaimRow,
)
from satori.infrastructure.persistence.source_policy import SessionSourceAdmission
from tests.erasure_support import ErasureCase
from tests.test_source_exclusion_production import SECRET
from tests.test_stage13_inclinations_persistence import APPLIED_DELTA, APPRAISAL_PAYLOAD


@dataclass(frozen=True, slots=True)
class KnowledgeFixture:
    user_claim_id: str
    world_claim_id: str
    position_id: str
    inclination_id: str
    position_evidence_ids: tuple[str, ...]
    inclination_evidence_id: str
    scalar_claim_ids: tuple[str, ...]


def add_knowledge(case: ErasureCase) -> KnowledgeFixture:
    """Seed accepted synthetic storage BEFORE exclusion, then record its actual input closure."""
    fixture = KnowledgeFixture(
        "rich-user-claim",
        "rich-world-claim",
        "rich-position",
        "rich-inclination",
        ("rich-position-a", "rich-position-b"),
        "rich-inclination-edge",
        ("rich-number", "rich-boolean"),
    )
    with case.runtime.shared() as snapshot, case.database.session_factory() as session:
        assert not snapshot.excluded_message_ids
        session.info["source_snapshot"] = snapshot
        first = session.get(ConversationMessageRow, case.message_id)
        second = session.get(ConversationMessageRow, case.derived_message_id)
        assert first is not None
        assert second is not None
        assert isinstance(first.content, str)
        assert isinstance(second.content, str)
        now = second.created_at
        identity, person = case.scope.identity_id, case.scope.counterparty_id
        common = dict(
            identity_id=identity,
            counterparty_id=person,
            schema_version=1,
            aggregate_version=1,
            policy_version=1,
            formation_version=1,
            normalization_version=1,
            value_kind="text",
            confidence=0.72,
            status="current",
            epistemic_kind="hypothesis",
            valid_from=now,
            valid_until=None,
            last_observed_at=now,
            expires_at=None,
            superseded_by_claim_id=None,
            created_at=now,
            updated_at=now,
        )
        session.add(
            UserModelClaimRow(
                **common,
                claim_id=fixture.user_claim_id,
                claim_key="1" * 64,
                predicate="project",
                value=SECRET,
                normalized_value=SECRET.casefold(),
            )
        )
        session.add(
            WorldModelClaimRow(
                **common,
                claim_id=fixture.world_claim_id,
                claim_key="2" * 64,
                predicate="status",
                subject_kind="project",
                subject_label=SECRET,
                normalized_subject_label=SECRET.casefold(),
                value="active",
                normalized_value="active",
            )
        )
        position = f"Проект {SECRET} требует внимания."
        session.add(
            SatoriPositionRow(
                position_id=fixture.position_id,
                position_key="3" * 64,
                identity_id=identity,
                schema_version=1,
                aggregate_version=1,
                policy_version=1,
                formation_version=1,
                normalization_version=1,
                proposition=position,
                normalized_proposition=normalize_position_text(position),
                kind="hypothesis",
                stance="support",
                confidence=0.68,
                status="active",
                value_key=None,
                competing_with_position_id=None,
                superseded_by_position_id=None,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            SatoriInclinationRow(
                inclination_id=fixture.inclination_id,
                inclination_key="4" * 64,
                identity_id=identity,
                schema_version=1,
                aggregate_version=1,
                policy_version=1,
                normalization_version=1,
                kind="interest",
                topic=SECRET,
                normalized_topic=SECRET.casefold(),
                alternative_topic=None,
                normalized_alternative_topic=None,
                score=0.35,
                confidence=0.61,
                stability=0.42,
                state_as_of=now,
                last_accepted_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()
        for model, claim_id in (
            (UserModelClaimEvidenceRow, fixture.user_claim_id),
            (WorldModelClaimEvidenceRow, fixture.world_claim_id),
        ):
            session.add(
                model(
                    evidence_id=claim_id + "-edge",
                    claim_id=claim_id,
                    source_message_id=first.message_id,
                    source_interaction_id=first.interaction_id,
                    observed_at=first.created_at,
                )
            )
        for message, evidence_id in zip(
            (first, second), fixture.position_evidence_ids, strict=True
        ):
            session.add(
                PositionEvidenceRow(
                    evidence_id=evidence_id,
                    position_id=fixture.position_id,
                    source_message_id=message.message_id,
                    source_interaction_id=message.interaction_id,
                    source_counterparty_id=person,
                    quote=message.content,
                    normalized_signature=normalize_position_text(str(message.content)),
                    role="observation",
                    observed_at=message.created_at,
                )
            )
        session.flush()
        # The fixture retains real numerical/evidence history; deletion must not rewrite it.
        session.add(
            ModelFormationDecisionRow(
                decision_id="rich-model-decision",
                idempotency_key="rich-model-decision",
                source_interaction_id=first.interaction_id,
                source_message_id=first.message_id,
                identity_id=identity,
                counterparty_id=person,
                formation_version=1,
                policy_version=1,
                kind="applied",
                reason_code="synthetic_fixture",
                user_created_count=1,
                user_merged_count=0,
                user_superseded_count=0,
                user_disputed_count=0,
                user_rejected_count=0,
                world_created_count=1,
                world_merged_count=0,
                world_superseded_count=0,
                world_disputed_count=0,
                world_rejected_count=0,
                user_claim_ids=[fixture.user_claim_id],
                world_claim_ids=[fixture.world_claim_id],
                decided_at=now,
                trace_id="rich-fixture",
                formation_method="synthetic",
                provider="fake",
                model="fake",
            )
        )
        session.add(
            PositionFormationDecisionRow(
                decision_id="rich-position-decision",
                idempotency_key="rich-position-decision",
                source_interaction_id=first.interaction_id,
                source_message_id=first.message_id,
                identity_id=identity,
                formation_version=1,
                policy_version=1,
                kind="applied",
                reason_code="synthetic_fixture",
                created_count=1,
                merged_count=0,
                superseded_count=0,
                competing_count=0,
                rejected_count=0,
                position_ids=[fixture.position_id],
                decided_at=now,
                trace_id="rich-fixture",
                formation_method="synthetic",
                provider="fake",
                model="fake",
            )
        )
        session.add(
            ReflectionRunRow(
                run_id="rich-reflection",
                run_key="5" * 64,
                identity_id=identity,
                schema_version=2,
                policy_version=1,
                purpose="general",
                trigger_kind="explicit_local",
                source_set_hash="6" * 64,
                status="completed",
                aggregate_version=3,
                attempt_count=1,
                created_at=now,
                updated_at=now,
                completed_at=now,
            )
        )
        session.flush()
        for revision_model, claim_id in (
            (UserModelClaimRevisionRow, fixture.user_claim_id),
            (WorldModelClaimRevisionRow, fixture.world_claim_id),
        ):
            session.add(
                revision_model(
                    revision_id=claim_id + "-revision",
                    claim_id=claim_id,
                    claim_version=1,
                    decision_id="rich-model-decision",
                    kind="created",
                    prior_status=None,
                    new_status="current",
                    prior_confidence=None,
                    new_confidence=0.72,
                    prior_expires_at=None,
                    new_expires_at=None,
                    reason_code="synthetic_fixture",
                    occurred_at=now,
                )
            )
        session.add(
            PositionRevisionRow(
                revision_id="rich-position-revision",
                position_id=fixture.position_id,
                position_version=1,
                decision_id="rich-position-decision",
                reflection_outcome_id=None,
                kind="created",
                prior_status=None,
                new_status="active",
                prior_confidence=None,
                new_confidence=0.68,
                reason_code="synthetic_fixture",
                occurred_at=now,
            )
        )
        affect = session.scalar(
            select(AffectiveTransitionRow).where(
                AffectiveTransitionRow.interaction_id == first.interaction_id,
            )
        )
        if affect is None:
            # An accepted synthetic numerical trail, without changing the materialized self.
            affect = AffectiveTransitionRow(
                transition_id="rich-affective-transition",
                identity_id=identity,
                interaction_id=first.interaction_id,
                source_message_id=first.message_id,
                trace_id="rich-fixture-affect",
                appraisal_schema_version=1,
                emotion_policy_version=1,
                mood_policy_version=1,
                base_state_version=1,
                resulting_state_version=2,
                base_mood_version=1,
                resulting_mood_version=2,
                appraised_at=first.created_at,
                committed_at=first.created_at,
                appraisal_confidence=0.8,
                appraisal_payload=dict(APPRAISAL_PAYLOAD),
                source_refs=[first.interaction_id],
                reason_codes=["synthetic_fixture"],
                applied_delta=dict(APPLIED_DELTA),
                mood_delta={"valence": 0.01, "energy": 0.01, "tension": 0.0},
                state_before={},
                state_after={},
                provider="fake",
                model="fake",
                appraisal_method="synthetic_fixture",
            )
            session.add(affect)
            session.flush()
        quote_hash = hashlib.sha256(first.content.encode()).hexdigest()
        session.add(
            ReflectionSourceRow(
                source_id="rich-reflection-source",
                run_id="rich-reflection",
                ordinal=0,
                kind="position_evidence",
                evidence_edge_id=fixture.position_evidence_ids[0],
                evidence_edge_version=1,
                root_interaction_id=first.interaction_id,
                root_message_id=first.message_id,
                root_counterparty_id=person,
                observed_at=first.created_at,
                content_hash=quote_hash,
                upstream_lineage_kind="position",
                upstream_lineage_id=fixture.position_id,
                affective_transition_id=affect.transition_id,
                affective_state_version=affect.resulting_state_version,
                affective_signal_hash="7" * 64,
            )
        )
        session.add(
            ReflectionProposalRow(
                proposal_id="rich-reflection-proposal",
                run_id="rich-reflection",
                ordinal=0,
                target_owner="satori_inclinations",
                payload={"retained_fixture_topic": SECRET},
                evidence_source_ids=["rich-reflection-source"],
                created_at=now,
            )
        )
        session.flush()
        session.add(
            ReflectionOutcomeRow(
                outcome_id="rich-reflection-outcome",
                proposal_id="rich-reflection-proposal",
                target_policy_version=1,
                decision="accepted",
                reason_code="synthetic_fixture",
                target_aggregate_type="satori_inclinations",
                target_aggregate_id=fixture.inclination_id,
                decided_at=now,
            )
        )
        session.add(
            InclinationEvidenceRow(
                evidence_id=fixture.inclination_evidence_id,
                inclination_id=fixture.inclination_id,
                reflection_source_id="rich-reflection-source",
                affective_transition_id=affect.transition_id,
                affective_state_version=affect.resulting_state_version,
                affective_signal_hash="7" * 64,
                source_message_id=first.message_id,
                source_interaction_id=first.interaction_id,
                source_session_id=first.session_id,
                source_counterparty_id=person,
                content_hash=quote_hash,
                content_signature="8" * 64,
                role="topic",
                signal=0.35,
                observed_at=first.created_at,
                accepted_at=now,
            )
        )
        session.flush()
        session.add(
            InclinationRevisionRow(
                revision_id="rich-inclination-revision",
                inclination_id=fixture.inclination_id,
                inclination_version=1,
                reflection_outcome_id="rich-reflection-outcome",
                kind="created",
                prior_score=None,
                new_score=0.35,
                applied_delta=0.35,
                prior_confidence=None,
                new_confidence=0.61,
                prior_stability=None,
                new_stability=0.42,
                state_as_of=now,
                reason_code="synthetic_fixture",
                occurred_at=now,
            )
        )
        source_memory = session.scalar(
            select(MemoryEvidenceRow).where(
                MemoryEvidenceRow.source_message_id == first.message_id,
            )
        )
        assert source_memory is not None
        # Retired supported-storage scalar artifacts test removal, not new formation vocabulary.
        for claim_id, kind, value in (
            (fixture.scalar_claim_ids[0], SemanticValueKind.NUMBER, 37),
            (fixture.scalar_claim_ids[1], SemanticValueKind.BOOLEAN, True),
        ):
            normalized = normalize_semantic_value(kind, value)
            material = dict(
                claim_kind="hypothesis",
                normalization_version=1,
                polarity=True,
                predicate="age" if kind is SemanticValueKind.NUMBER else "likes",
                subject="user",
                value=normalized,
                value_kind=kind.value,
            )
            base = hashlib.sha256(
                json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            session.add(
                SemanticClaimRow(
                    claim_id=claim_id,
                    claim_key=scope_semantic_key(base, case.scope),
                    schema_version=1,
                    aggregate_version=1,
                    subject="user",
                    predicate=material["predicate"],
                    value_kind=kind.value,
                    value=value,
                    normalized_value=normalized,
                    polarity=True,
                    claim_kind="hypothesis",
                    confidence=0.31,
                    status="retracted",
                    valid_from=now,
                    valid_until=now,
                    superseded_by_claim_id=None,
                    created_at=now,
                    updated_at=now,
                    formation_method="synthetic_retained_artifact",
                    formation_version=1,
                    normalization_version=1,
                )
            )
            session.flush()
            session.add(
                SemanticClaimEvidenceRow(
                    semantic_evidence_id=claim_id + "-edge",
                    claim_id=claim_id,
                    memory_id=source_memory.memory_id,
                    memory_evidence_id=source_memory.evidence_id,
                    root_message_id=first.message_id,
                    root_interaction_id=first.interaction_id,
                    source_kind="explicit_user_statement",
                    observed_at=first.created_at,
                )
            )
        session.flush()
        admission = SessionSourceAdmission(session)
        first_input = admission.prepare(root_message_ids=(first.message_id,), payload_refs=())
        mixed_input = admission.prepare(
            root_message_ids=(first.message_id, second.message_id), payload_refs=()
        )
        for payload_kind, payload_id in (
            ("user_model_claim", fixture.user_claim_id),
            ("world_model_claim", fixture.world_claim_id),
            *(("semantic_claim", claim_id) for claim_id in fixture.scalar_claim_ids),
        ):
            admission.record(payload_kind, payload_id, 1, first_input)
        admission.record("position", fixture.position_id, 1, mixed_input)
        session.flush()
        reflection_input = admission.prepare(
            root_message_ids=(first.message_id,),
            payload_refs=(PayloadRef("position", fixture.position_id, 1),),
        )
        admission.record("reflection_proposal", "rich-reflection-proposal", 1, reflection_input)
        session.flush()
        inclination_input = admission.prepare(
            root_message_ids=(first.message_id,),
            payload_refs=(PayloadRef("reflection_proposal", "rich-reflection-proposal", 1),),
        )
        admission.record("inclination", fixture.inclination_id, 1, inclination_input)
        session.commit()
    return fixture
