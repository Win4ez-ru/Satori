"""ORM persistence models; never exposed as domain entities."""

from satori.infrastructure.persistence.base import Base
from satori.infrastructure.persistence.models.affect import (
    AffectiveStateRow,
    AffectiveTransitionRow,
)
from satori.infrastructure.persistence.models.conversation import (
    ConversationInteractionRow,
    ConversationMessageRow,
    ConversationSessionRow,
)
from satori.infrastructure.persistence.models.initial_self import (
    AuditEventRow,
    PersonalityStateRow,
    PersonalityTraitRow,
    SatoriIdentityRow,
    ValueRow,
    ValueSetRow,
)
from satori.infrastructure.persistence.models.memory import (
    EpisodeFormationDecisionRow,
    EpisodicMemoryRow,
    MemoryEvidenceRow,
)
from satori.infrastructure.persistence.models.models import (
    ModelFormationDecisionRow,
    UserModelClaimEvidenceRow,
    UserModelClaimRevisionRow,
    UserModelClaimRow,
    WorldModelClaimEvidenceRow,
    WorldModelClaimRevisionRow,
    WorldModelClaimRow,
)
from satori.infrastructure.persistence.models.personality import (
    PersonalityCheckpointApprovalRow,
    PersonalityCheckpointRow,
    PersonalityCheckpointTraitRow,
    PersonalityEvidenceRow,
    PersonalityRestoreEventRow,
    PersonalityRevisionRow,
)
from satori.infrastructure.persistence.models.positions import (
    InclinationEvidenceRow,
    InclinationRevisionRow,
    PositionEvidenceRow,
    PositionFormationDecisionRow,
    PositionRevisionRow,
    SatoriInclinationRow,
    SatoriPositionRow,
)
from satori.infrastructure.persistence.models.reflection import (
    ReflectionAttemptRow,
    ReflectionOutcomeRow,
    ReflectionProposalRow,
    ReflectionRunRow,
    ReflectionSourceRow,
)
from satori.infrastructure.persistence.models.relationship import (
    RelationshipDecisionRow,
    RelationshipStateRow,
    RelationshipTransitionRow,
)
from satori.infrastructure.persistence.models.retrieval import EpisodicMemoryEmbeddingRow
from satori.infrastructure.persistence.models.semantic import (
    SemanticClaimEvidenceRow,
    SemanticClaimRevisionRow,
    SemanticClaimRow,
    SemanticFormationDecisionRow,
)

__all__ = (
    "AffectiveStateRow",
    "AffectiveTransitionRow",
    "AuditEventRow",
    "Base",
    "ConversationInteractionRow",
    "ConversationMessageRow",
    "ConversationSessionRow",
    "EpisodeFormationDecisionRow",
    "EpisodicMemoryEmbeddingRow",
    "EpisodicMemoryRow",
    "InclinationEvidenceRow",
    "InclinationRevisionRow",
    "MemoryEvidenceRow",
    "ModelFormationDecisionRow",
    "PersonalityCheckpointApprovalRow",
    "PersonalityCheckpointRow",
    "PersonalityCheckpointTraitRow",
    "PersonalityEvidenceRow",
    "PersonalityRestoreEventRow",
    "PersonalityRevisionRow",
    "PersonalityStateRow",
    "PersonalityTraitRow",
    "PositionEvidenceRow",
    "PositionFormationDecisionRow",
    "PositionRevisionRow",
    "ReflectionAttemptRow",
    "ReflectionOutcomeRow",
    "ReflectionProposalRow",
    "ReflectionRunRow",
    "ReflectionSourceRow",
    "RelationshipDecisionRow",
    "RelationshipStateRow",
    "RelationshipTransitionRow",
    "SatoriIdentityRow",
    "SatoriInclinationRow",
    "SatoriPositionRow",
    "SemanticClaimEvidenceRow",
    "SemanticClaimRevisionRow",
    "SemanticClaimRow",
    "SemanticFormationDecisionRow",
    "UserModelClaimEvidenceRow",
    "UserModelClaimRevisionRow",
    "UserModelClaimRow",
    "ValueRow",
    "ValueSetRow",
    "WorldModelClaimEvidenceRow",
    "WorldModelClaimRevisionRow",
    "WorldModelClaimRow",
)
