"""Real managed owner paths for synthetic identity reasons; no paid or local model calls."""

import asyncio
from dataclasses import dataclass, field, replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from satori.application.personality.use_cases import (
    PersonalityCheckpointApprovalProposal,
    PersonalityInspection,
)
from satori.application.reflection.use_cases import ApplyReflectionProposals, ProcessReflection
from satori.core.embedding import EmbeddingSpace
from satori.core.personality import (
    PersonalityCitationRole,
    PersonalityDirection,
    PersonalityRestoreProposal,
)
from satori.core.reflection import (
    ReflectionGenerationRequest,
    ReflectionPersonalityCandidate,
    ReflectionPersonalityCitation,
    ReflectionProposalDocument,
    ReflectionProviderResponse,
    ReflectionPurpose,
    ReflectionTargetOwner,
)
from satori.domain.erasure_personality_reasons import CheckpointReasonKind, CheckpointReasonSelector
from satori.domain.positions import PositionManager
from satori.domain.reflection import ReflectionRunStatus, ReflectionTriggerKind
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.positions_uow import SQLAlchemyPositionsUnitOfWork
from satori.infrastructure.persistence.reflection_uow import SQLAlchemyReflectionUnitOfWork
from tests.erasure_appraisal_support import (
    appraisal_command,
    create_appraisal_fixture,
    prepare_appraisal,
)
from tests.erasure_reflection_cost import CostFixtureEmbeddingProvider
from tests.erasure_reflection_support import ReflectionFixture
from tests.erasure_support import ErasureCase, provider_counts
from tests.fakes import FrozenClock
from tests.test_source_exclusion_production import _services, _turn
from tests.test_stage4_conversation_memory import (
    INTERACTION_TIME,
    conversation_provider,
    id_sequence,
)
from tests.test_stage14_personality_persistence import SOURCE_TEXTS

APPROVAL_REASON = "SYNTHETIC_PRIVATE_CHECKPOINT_APPROVAL_731"
RESTORE_REASON = "SYNTHETIC_PRIVATE_CHECKPOINT_RESTORE_731"


@dataclass(slots=True)
class SyntheticPersonalityProvider:
    requests: list[ReflectionGenerationRequest] = field(default_factory=list)

    async def generate_structured(
        self, request: ReflectionGenerationRequest, /
    ) -> ReflectionProviderResponse:
        self.requests.append(request)
        assert request.personality_state is not None
        assert len(request.sources) >= 8
        candidate = ReflectionPersonalityCandidate(
            target_owner=ReflectionTargetOwner.PERSONALITY,
            trait_key="curiosity",
            direction=PersonalityDirection.INCREASE,
            confidence=0.9,
            citations=tuple(
                ReflectionPersonalityCitation(
                    source_id=source.source_id,
                    role=PersonalityCitationRole.SUPPORT,
                )
                for source in request.sources
            ),
            expected_personality_version=request.personality_state.aggregate_version,
        )
        return ReflectionProviderResponse(
            document=ReflectionProposalDocument(schema_version=3, proposals=(candidate,)),
            provider="fake-checkpoint-personality",
            model="fixture",
            formation_method="synthetic.personality.v3",
        )


@dataclass(frozen=True, slots=True)
class CheckpointReasonFixture:
    case: ErasureCase
    reflection: ReflectionFixture
    selectors: tuple[CheckpointReasonSelector, ...]
    approval_proposal: PersonalityCheckpointApprovalProposal
    restore_proposal: PersonalityRestoreProposal
    numeric_before: PersonalityInspection
    personality_provider: SyntheticPersonalityProvider


class CompleteCheckpointEmbeddingProvider(CostFixtureEmbeddingProvider):
    """One explicit deterministic fixture space covers every preceding and new turn."""

    def __init__(
        self,
        vectors: dict[str, tuple[float, ...]],
        *,
        space: EmbeddingSpace | None = None,
        error: Exception | None = None,
    ) -> None:
        complete = dict(vectors)
        complete.update(dict.fromkeys(SOURCE_TEXTS, (0.0, 1.0, 0.0)))
        super().__init__(complete, space=space, error=error)


def create_checkpoint_reason_fixture(
    database: Database, directory: Path
) -> CheckpointReasonFixture:
    with patch(
        "tests.test_source_exclusion_production.FakeEmbeddingProvider",
        CompleteCheckpointEmbeddingProvider,
    ):
        return _create_checkpoint_reason_fixture(database, directory)


def _create_checkpoint_reason_fixture(
    database: Database, directory: Path
) -> CheckpointReasonFixture:
    reflection = create_appraisal_fixture(database, directory)
    case = reflection.case
    services = list(case.services)
    # Canonical conversations form genuine episodic inputs across independent sessions
    # and calendar months. Their source receipts are written by production owners.
    for index, (day, text) in enumerate(
        zip((5, 19, 37, 51, 69, 83, 101, 115), SOURCE_TEXTS, strict=True)
    ):
        service, _foreground, _embedding = _services(
            database,
            directory,
            f"checkpoint-source-{index}",
            day,
            foreground=conversation_provider("Synthetic observation recorded."),
        )
        _turn(service, text, f"checkpoint-source-{index}")
        services.append(service)
    owner_services, _foreground, _embedding = _services(
        database,
        directory,
        "checkpoint-owner",
        126,
        form=False,
        foreground=conversation_provider("Synthetic owner control."),
    )
    provider = SyntheticPersonalityProvider()
    now = INTERACTION_TIME + timedelta(days=126)
    process = ProcessReflection(
        reflection_uow_factory=lambda: SQLAlchemyReflectionUnitOfWork(database.session_factory),
        positions_uow_factory=lambda: SQLAlchemyPositionsUnitOfWork(database.session_factory),
        provider=provider,
        clock=FrozenClock(now),
        id_generator=id_sequence("checkpoint-reflection"),
        personality_context=owner_services.personality.evolution,
    )
    report = asyncio.run(
        process.execute(
            case.scope.identity_id,
            trigger=ReflectionTriggerKind.EXPLICIT_LOCAL,
            trace_id="checkpoint-personality-generation",
            purpose=ReflectionPurpose.PERSONALITY_EVOLUTION,
        )
    )
    assert report.run is not None, report.reason_code
    routed = ApplyReflectionProposals(
        reflection_uow_factory=lambda: SQLAlchemyReflectionUnitOfWork(database.session_factory),
        positions_uow_factory=lambda: SQLAlchemyPositionsUnitOfWork(database.session_factory),
        manager=PositionManager(),
        clock=FrozenClock(now),
        id_generator=id_sequence("checkpoint-route"),
        personality_router=owner_services.personality.apply_reflection,
    ).execute(report.run.run_id, trace_id="checkpoint-personality-routing")
    assert routed.status is ReflectionRunStatus.COMPLETED
    evolved = owner_services.personality.evolution.inspect(case.scope.identity_id)
    assert evolved is not None
    assert evolved.personality.aggregate_version == 2
    checkpoint = next(
        row for row in evolved.checkpoints if row.snapshot.source_aggregate_version == 2
    )
    approval_proposal = PersonalityCheckpointApprovalProposal(
        checkpoint_id=checkpoint.snapshot.checkpoint_id,
        checkpoint_hash=checkpoint.snapshot.checkpoint_hash,
        expected_personality_version=2,
        reason=APPROVAL_REASON,
    )
    owner_services.personality.approve_checkpoint.execute(
        case.scope.identity_id, approval_proposal, trace_id="checkpoint-managed-approval"
    )
    restore_proposal = PersonalityRestoreProposal(
        checkpoint_id=evolved.activation_checkpoint.snapshot.checkpoint_id,
        checkpoint_hash=evolved.activation_checkpoint.snapshot.checkpoint_hash,
        expected_personality_version=2,
        reason=RESTORE_REASON,
    )
    restored = owner_services.personality.restore_checkpoint.execute(
        case.scope.identity_id, restore_proposal, trace_id="checkpoint-managed-restore"
    )
    assert restored.restored
    numeric_before = owner_services.personality.evolution.inspect(case.scope.identity_id)
    assert numeric_before is not None
    selectors = tuple(
        sorted(
            (
                *(
                    CheckpointReasonSelector(
                        CheckpointReasonKind.CHECKPOINT_APPROVAL, row.approval_id
                    )
                    for row in numeric_before.approvals
                ),
                *(
                    CheckpointReasonSelector(
                        CheckpointReasonKind.PERSONALITY_RESTORE, row.restore_id
                    )
                    for row in numeric_before.restores
                ),
            ),
            key=lambda item: item.key,
        )
    )
    assert len(selectors) == 2
    services.append(owner_services)
    case = replace(
        case, services=tuple(services), initial_provider_calls=provider_counts(tuple(services))
    )
    reflection = replace(reflection, case=case)
    appraisal, request = prepare_appraisal(reflection)
    assert appraisal.commit(appraisal_command(appraisal, request)).main_projection_current
    return CheckpointReasonFixture(
        case, reflection, selectors, approval_proposal, restore_proposal, numeric_before, provider
    )
