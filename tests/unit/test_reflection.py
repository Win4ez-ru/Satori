"""Deterministic versioned Stage 12-14 reflection contract tests."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from satori.application.reflection.use_cases import ApplyReflectionProposals
from satori.core.inclinations import (
    InclinationAffectiveSignal,
    InclinationKind,
    InclinationStateReference,
)
from satori.core.personality import (
    PersonalityCitationRole,
    PersonalityDirection,
    PersonalityStateReference,
)
from satori.core.positions import (
    PositionEvidenceRole,
    PositionKind,
    PositionStance,
    PositionValueReference,
)
from satori.core.reflection import (
    ReflectionCitation,
    ReflectionGenerationRequest,
    ReflectionInclinationCandidate,
    ReflectionLineageKind,
    ReflectionPersonalityCandidate,
    ReflectionPersonalityCitation,
    ReflectionPositionCandidate,
    ReflectionProposalDocument,
    ReflectionPurpose,
    ReflectionSource,
    ReflectionSourceKind,
    ReflectionTargetOwner,
)
from satori.domain.reflection import (
    REFLECTION_POLICY_VERSION,
    REFLECTION_POLICY_VERSION_V1,
    REFLECTION_POLICY_VERSION_V2,
    REFLECTION_POLICY_VERSION_V3,
    REFLECTION_SCHEMA_VERSION,
    REFLECTION_SCHEMA_VERSION_V1,
    REFLECTION_SCHEMA_VERSION_V2,
    REFLECTION_SCHEMA_VERSION_V3,
    ReflectionRun,
    ReflectionRunStatus,
    ReflectionSourceRecord,
    ReflectionTriggerKind,
    affective_signal_hash,
    candidate_evidence_source_ids,
    complete_reflection_run,
    proposal_payload,
    reflection_proposal_id,
    reflection_run_id,
    reflection_run_key,
    source_set_hash,
    validate_candidate_sources,
)


def test_only_proposal_ready_and_applying_runs_require_routing() -> None:
    assert ReflectionRunStatus.PROPOSALS_READY.requires_routing
    assert ReflectionRunStatus.APPLYING.requires_routing
    assert not ReflectionRunStatus.PENDING_GENERATION.requires_routing
    assert not ReflectionRunStatus.RETRYABLE_FAILURE.requires_routing
    assert not ReflectionRunStatus.COMPLETED.requires_routing
    assert not ReflectionRunStatus.EXHAUSTED.requires_routing


def test_reflection_completion_is_monotonic_and_replay_safe() -> None:
    now = datetime(2026, 8, 22, tzinfo=UTC)
    applying = ReflectionRun(
        run_id="run-1",
        run_key="key-1",
        identity_id="identity-1",
        schema_version=1,
        policy_version=1,
        trigger_kind=ReflectionTriggerKind.EXPLICIT_LOCAL,
        source_set_hash="a" * 64,
        status=ReflectionRunStatus.APPLYING,
        aggregate_version=3,
        attempt_count=1,
        created_at=now,
        updated_at=now,
    )

    completed = complete_reflection_run(applying, completed_at=now)

    assert completed.status is ReflectionRunStatus.COMPLETED
    assert completed.aggregate_version == 4
    assert completed.completed_at == now
    assert complete_reflection_run(completed, completed_at=now) is completed
    with pytest.raises(ValueError, match="only an applying"):
        complete_reflection_run(
            replace(applying, status=ReflectionRunStatus.PROPOSALS_READY),
            completed_at=now,
        )


def source(source_id: str, ordinal: int) -> ReflectionSourceRecord:
    return ReflectionSourceRecord(
        source_id=source_id,
        run_id="run-1",
        ordinal=ordinal,
        kind=ReflectionSourceKind.POSITION_EVIDENCE,
        evidence_edge_id=f"edge-{ordinal}",
        evidence_edge_version=1,
        root_interaction_id=f"interaction-{ordinal}",
        root_message_id=f"message-{ordinal}",
        root_counterparty_id="person-1",
        observed_at=datetime(2026, 8, 1 + ordinal, tzinfo=UTC),
        content_hash=f"{ordinal:064x}",
    )


def candidate() -> ReflectionPositionCandidate:
    return ReflectionPositionCandidate(
        target_owner=ReflectionTargetOwner.SATORI_POSITIONS,
        proposition="Проверяемые основания улучшают решения",
        kind=PositionKind.BELIEF,
        stance=PositionStance.SUPPORT,
        confidence=0.8,
        evidence=(
            ReflectionCitation("source-0", PositionEvidenceRole.ARGUMENT),
            ReflectionCitation("source-1", PositionEvidenceRole.OBSERVATION),
        ),
    )


def affective() -> InclinationAffectiveSignal:
    return InclinationAffectiveSignal(
        transition_id="transition-1",
        resulting_state_version=2,
        signal_hash="f" * 64,
        pleasantness=0.4,
        novelty=0.7,
        salience=0.8,
        curiosity_signal=0.6,
        interest_signal=0.75,
        concern_signal=0.1,
        frustration_signal=0.0,
        appraisal_confidence=0.9,
    )


def inclination_candidate() -> ReflectionInclinationCandidate:
    return ReflectionInclinationCandidate(
        target_owner=ReflectionTargetOwner.SATORI_INCLINATIONS,
        kind=InclinationKind.INTEREST,
        topic="архитектура",
        alternative_topic=None,
        confidence=0.8,
        source_ids=("source-0", "source-1"),
    )


def test_source_set_and_run_identity_are_ordered_deterministic_and_trigger_neutral() -> None:
    sources = (source("source-1", 1), source("source-0", 0))
    digest = source_set_hash(sources)
    assert digest == source_set_hash(tuple(reversed(sources)))
    key = reflection_run_key(identity_id="identity-1", source_hash=digest)
    assert key == reflection_run_key(identity_id="identity-1", source_hash=digest)
    assert reflection_run_id(key).startswith("reflection-run-")


def test_current_reflection_versions_are_v2_with_explicit_v1_compatibility() -> None:
    assert (REFLECTION_SCHEMA_VERSION, REFLECTION_POLICY_VERSION) == (2, 2)
    assert (REFLECTION_SCHEMA_VERSION_V1, REFLECTION_POLICY_VERSION_V1) == (1, 1)
    assert (REFLECTION_SCHEMA_VERSION_V2, REFLECTION_POLICY_VERSION_V2) == (2, 2)
    assert (REFLECTION_SCHEMA_VERSION_V3, REFLECTION_POLICY_VERSION_V3) == (3, 3)


def test_v3_source_identity_is_purpose_separated_and_requires_lineage_without_affect() -> None:
    plain = source("source-0", 0)
    v3_source = replace(
        plain,
        upstream_lineage_kind=ReflectionLineageKind.POSITION,
        upstream_lineage_id="position-1",
    )
    digest = source_set_hash(
        (v3_source,),
        schema_version=REFLECTION_SCHEMA_VERSION_V3,
        purpose=ReflectionPurpose.PERSONALITY_EVOLUTION,
    )
    key = reflection_run_key(
        identity_id="identity-1",
        source_hash=digest,
        schema_version=REFLECTION_SCHEMA_VERSION_V3,
        policy_version=REFLECTION_POLICY_VERSION_V3,
        purpose=ReflectionPurpose.PERSONALITY_EVOLUTION,
    )

    assert digest != source_set_hash((plain,), schema_version=REFLECTION_SCHEMA_VERSION_V2)
    assert key == reflection_run_key(
        identity_id="identity-1",
        source_hash=digest,
        schema_version=REFLECTION_SCHEMA_VERSION_V3,
        policy_version=REFLECTION_POLICY_VERSION_V3,
        purpose=ReflectionPurpose.PERSONALITY_EVOLUTION,
    )
    with pytest.raises(ValueError, match="requires upstream lineage"):
        source_set_hash(
            (plain,),
            schema_version=REFLECTION_SCHEMA_VERSION_V3,
            purpose=ReflectionPurpose.PERSONALITY_EVOLUTION,
        )
    with pytest.raises(ValueError, match="cannot contain affect"):
        source_set_hash(
            (
                replace(
                    v3_source,
                    affective_transition_id="transition-1",
                    affective_state_version=2,
                    affective_signal_hash="f" * 64,
                ),
            ),
            schema_version=REFLECTION_SCHEMA_VERSION_V3,
            purpose=ReflectionPurpose.PERSONALITY_EVOLUTION,
        )
    with pytest.raises(ValueError, match="require the general purpose"):
        reflection_run_key(
            identity_id="identity-1",
            source_hash=digest,
            purpose=ReflectionPurpose.PERSONALITY_EVOLUTION,
        )


def test_v3_request_and_document_are_personality_only_and_direction_only() -> None:
    now = datetime(2026, 8, 22, tzinfo=UTC)
    sources = tuple(
        ReflectionSource(
            source_id=f"source-{index}",
            kind=ReflectionSourceKind.POSITION_EVIDENCE,
            evidence_edge_id=f"edge-{index}",
            evidence_edge_version=1,
            root_interaction_id=f"interaction-{index}",
            root_message_id=f"message-{index}",
            root_counterparty_id="person-1",
            observed_at=now,
            content_hash=f"{index:064x}",
            quote=f"Независимое каноническое наблюдение номер {index}",
            root_session_id=f"session-{index}",
            upstream_lineage_kind=ReflectionLineageKind.POSITION,
            upstream_lineage_id=f"position-{index // 2}",
        )
        for index in range(8)
    )
    candidate = ReflectionPersonalityCandidate(
        target_owner=ReflectionTargetOwner.PERSONALITY,
        trait_key="curiosity",
        direction=PersonalityDirection.INCREASE,
        confidence=0.86,
        citations=tuple(
            ReflectionPersonalityCitation(
                source_id=item.source_id,
                role=PersonalityCitationRole.SUPPORT,
            )
            for item in sources
        ),
        expected_personality_version=7,
    )
    state = PersonalityStateReference(identity_id="identity-1", aggregate_version=7)
    request = ReflectionGenerationRequest(
        schema_version=3,
        trace_id="trace-3",
        run_id="run-3",
        identity_id="identity-1",
        policy_version=3,
        max_proposals=1,
        sources=sources,
        current_positions=(),
        values=(),
        purpose=ReflectionPurpose.PERSONALITY_EVOLUTION,
        personality_state=state,
    )
    document = ReflectionProposalDocument(schema_version=3, proposals=(candidate,))

    assert request.personality_state == state
    assert request.current_positions == ()
    assert request.values == ()
    assert request.current_inclinations == ()
    assert document.proposals == (candidate,)
    assert candidate_evidence_source_ids(candidate) == tuple(
        f"source-{index}" for index in range(8)
    )
    validate_candidate_sources(
        candidate,
        allowed_source_ids=frozenset(item.source_id for item in sources),
    )
    payload = proposal_payload(candidate)
    assert set(payload) == {
        "citations",
        "confidence",
        "direction",
        "expected_personality_version",
        "target_owner",
        "trait_key",
    }
    routed = ApplyReflectionProposals._personality_proposal(payload)
    assert routed.trait_key == "curiosity"
    assert routed.expected_personality_version == 7
    with pytest.raises(ValueError, match="payload shape"):
        ApplyReflectionProposals._personality_proposal({**payload, "delta": 0.005})
    with pytest.raises(ValueError, match="V1/V2"):
        ReflectionProposalDocument(schema_version=2, proposals=(candidate,))
    with pytest.raises(ValueError, match="only personality"):
        ReflectionProposalDocument(schema_version=3, proposals=(inclination_candidate(),))
    with pytest.raises(ValueError, match="count exceeds"):
        ReflectionProposalDocument(schema_version=3, proposals=(candidate, candidate))
    with pytest.raises(ValueError, match="general target state"):
        replace(
            request,
            values=(PositionValueReference(key="truth", description="Truth matters"),),
        )
    with pytest.raises(ValueError, match="no affect"):
        replace(request, sources=(replace(sources[0], affective=affective()), *sources[1:]))


def test_v1_hash_ignores_nullable_attachment_while_v2_binds_it() -> None:
    plain = source("source-0", 0)
    attached = replace(
        plain,
        affective_transition_id="transition-1",
        affective_state_version=2,
        affective_signal_hash="f" * 64,
    )

    v1 = source_set_hash((plain,), schema_version=REFLECTION_SCHEMA_VERSION_V1)
    assert v1 == source_set_hash((attached,), schema_version=REFLECTION_SCHEMA_VERSION_V1)
    v2_without = source_set_hash((plain,), schema_version=REFLECTION_SCHEMA_VERSION_V2)
    v2_with = source_set_hash((attached,), schema_version=REFLECTION_SCHEMA_VERSION_V2)
    assert len({v1, v2_without, v2_with}) == 3
    assert reflection_run_key(
        identity_id="identity-1",
        source_hash=v1,
        schema_version=REFLECTION_SCHEMA_VERSION_V1,
        policy_version=REFLECTION_POLICY_VERSION_V1,
    ) != reflection_run_key(
        identity_id="identity-1",
        source_hash=v2_with,
        schema_version=REFLECTION_SCHEMA_VERSION_V2,
        policy_version=REFLECTION_POLICY_VERSION_V2,
    )


def test_reflection_source_attachment_is_all_or_none() -> None:
    with pytest.raises(ValueError, match="all-or-none"):
        replace(source("source-0", 0), affective_transition_id="transition-1")


def test_affective_signal_hash_binds_transition_source_appraisal_and_owner_delta() -> None:
    source_record = source("source-0", 0)
    appraisal = {
        "pleasantness": 0.4,
        "activation": 0.5,
        "novelty": 0.7,
        "salience": 0.8,
        "uncertainty": 0.2,
        "curiosity_signal": 0.6,
        "interest_signal": 0.75,
        "humor_signal": 0.1,
        "concern_signal": 0.1,
        "frustration_signal": 0.0,
        "confidence_signal": 0.4,
    }
    delta = {
        "valence": 0.1,
        "arousal": 0.1,
        "tension": 0.0,
        "curiosity": 0.2,
        "interest": 0.2,
        "amusement": 0.0,
        "concern": 0.0,
        "frustration": 0.0,
        "situational_confidence": 0.1,
    }

    def build_hash(
        *,
        interaction_id: str = "interaction-0",
        source_input: ReflectionSourceRecord = source_record,
        novelty: float = 0.7,
        interest_delta: float = 0.2,
    ) -> str:
        return affective_signal_hash(
            transition_id="transition-1",
            identity_id="identity-1",
            interaction_id=interaction_id,
            source_message_id="message-0",
            resulting_state_version=2,
            source=source_input,
            appraisal_schema_version=1,
            appraisal_payload={**appraisal, "novelty": novelty},
            appraisal_confidence=0.9,
            applied_delta={**delta, "interest": interest_delta},
        )

    digest = build_hash()

    assert digest == build_hash()
    assert digest != build_hash(interaction_id="interaction-1")
    assert digest != build_hash(source_input=replace(source_record, content_hash="1" * 64))
    assert digest != build_hash(novelty=0.71)
    assert digest != build_hash(interest_delta=0.21)


def test_inclination_state_reference_is_bounded_and_time_aware() -> None:
    reference = InclinationStateReference(
        inclination_id="inclination-1",
        aggregate_version=3,
        kind=InclinationKind.PREFERENCE,
        topic="PostgreSQL",
        alternative_topic="SQLite",
        score=-0.25,
        confidence=0.7,
        stability=0.6,
        state_as_of=datetime(2026, 8, 22, tzinfo=UTC),
    )
    assert reference.score == -0.25
    with pytest.raises(ValueError, match="interest score"):
        replace(reference, kind=InclinationKind.INTEREST, alternative_topic=None, score=-0.01)


def test_v2_document_accepts_strict_inclination_but_v1_rejects_it() -> None:
    proposal = inclination_candidate()
    document = ReflectionProposalDocument(schema_version=2, proposals=(proposal,))
    assert document.proposals == (proposal,)
    assert candidate_evidence_source_ids(proposal) == ("source-0", "source-1")
    with pytest.raises(ValueError, match="schema v1"):
        ReflectionProposalDocument(schema_version=1, proposals=(proposal,))


def test_v1_request_excludes_stage13_state_and_v2_accepts_bounded_projection() -> None:
    source_view = ReflectionSource(
        source_id="source-0",
        kind=ReflectionSourceKind.POSITION_EVIDENCE,
        evidence_edge_id="edge-0",
        evidence_edge_version=1,
        root_interaction_id="interaction-0",
        root_message_id="message-0",
        root_counterparty_id="person-1",
        observed_at=datetime(2026, 8, 1, tzinfo=UTC),
        content_hash="0" * 64,
        quote="Архитектура оказалась неожиданно увлекательной",
        affective=affective(),
        root_session_id="session-1",
    )
    reference = InclinationStateReference(
        inclination_id="inclination-1",
        aggregate_version=1,
        kind=InclinationKind.INTEREST,
        topic="архитектура",
        alternative_topic=None,
        score=0.2,
        confidence=0.7,
        stability=0.4,
        state_as_of=datetime(2026, 8, 22, tzinfo=UTC),
    )
    with pytest.raises(ValueError, match="schema v1"):
        ReflectionGenerationRequest(
            schema_version=1,
            trace_id="trace-1",
            run_id="run-1",
            identity_id="identity-1",
            policy_version=1,
            max_proposals=3,
            sources=(source_view,),
            current_positions=(),
            values=(),
        )
    request = ReflectionGenerationRequest(
        schema_version=2,
        trace_id="trace-1",
        run_id="run-1",
        identity_id="identity-1",
        policy_version=2,
        max_proposals=3,
        sources=(source_view,),
        current_positions=(),
        values=(),
        current_inclinations=(reference,),
    )
    assert request.current_inclinations == (reference,)
    assert request.sources[0].root_session_id == "session-1"
    with pytest.raises(ValueError, match="root_session_id"):
        replace(source_view, root_session_id=" ")


def test_proposal_identity_is_stable_and_citations_must_be_fixed_sources() -> None:
    proposal = candidate()
    assert reflection_proposal_id(
        run_id="run-1", ordinal=0, candidate=proposal
    ) == reflection_proposal_id(run_id="run-1", ordinal=0, candidate=proposal)
    assert candidate_evidence_source_ids(proposal) == ("source-0", "source-1")
    validate_candidate_sources(proposal, allowed_source_ids=frozenset({"source-0", "source-1"}))
    with pytest.raises(ValueError, match="outside the fixed run set"):
        validate_candidate_sources(proposal, allowed_source_ids=frozenset({"source-0"}))


def test_source_set_rejects_non_contiguous_ordinals() -> None:
    with pytest.raises(ValueError, match="contiguous"):
        source_set_hash((source("source-1", 1),))
