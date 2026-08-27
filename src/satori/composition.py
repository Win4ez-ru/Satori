"""Small composition helpers; no domain policy lives here."""

from dataclasses import dataclass

from satori.application.affect.use_cases import (
    AffectiveAppraisalProvider,
    EnsureAffectiveState,
    FinalizeAffectiveInteraction,
    GetAffectiveHistory,
    GetAffectiveStatus,
    PrepareAffectiveContext,
)
from satori.application.cognition.use_cases import (
    DeterministicCognitionPlanner,
    SafeCognitionPipeline,
)
from satori.application.conversation.coherence import SESSION_RECAP_MAX_RECENT_TURNS
from satori.application.conversation.context import (
    CharacterContextComposer,
    ConversationRequestBuilder,
)
from satori.application.conversation.grounding import ResponseGroundingGate
from satori.application.conversation.history import (
    CloseConversationSession,
    GetConversationHistory,
    GetRecentConversation,
    InteractionLog,
    StartConversationSession,
)
from satori.application.conversation.policy import BEHAVIOR_POLICY_V10
from satori.application.conversation.post_processing import ProcessPostResponse
from satori.application.conversation.use_cases import ConversationProvider, TalkToSatori
from satori.application.initial_self.use_cases import (
    ActivateSatori,
    GetInitialSelfSnapshot,
    GetSatoriIdentity,
)
from satori.application.memory.use_cases import (
    EpisodeFormationProvider,
    FormEpisodeForInteraction,
    GetEpisodicMemories,
)
from satori.application.models.use_cases import (
    BackfillCurrentModels,
    FormCurrentModels,
    GetCurrentModels,
    ModelFormationProvider,
)
from satori.application.personality.use_cases import (
    ApplyPersonalityReflection,
    ApprovePersonalityCheckpoint,
    GetPersonalityEvolution,
    RestorePersonalityCheckpoint,
)
from satori.application.positions.use_cases import (
    BackfillSatoriPositions,
    FormSatoriPositions,
    GetSatoriPositions,
    PositionFormationProvider,
)
from satori.application.reflection.ports import ReflectionGenerationPort
from satori.application.reflection.use_cases import (
    ApplyReflectionProposals,
    GetReflections,
    ProcessReflection,
)
from satori.application.relationship.use_cases import (
    EnsureRelationship,
    GetRelationshipForSession,
    GetRelationshipHistory,
    GetRelationshipStatus,
    ProcessRelationshipForInteraction,
    RelationshipProvider,
)
from satori.application.retrieval.policy import RetrievalPolicy
from satori.application.retrieval.use_cases import (
    EmbeddingProvider,
    IndexAllEpisodicMemories,
    IndexEpisodicMemory,
    RetrieveEpisodicMemories,
)
from satori.application.semantic.use_cases import (
    BackfillSemanticMemory,
    FormSemanticMemory,
    GetSemanticClaims,
    RetrieveSemanticClaims,
    SemanticFormationProvider,
)
from satori.config import Settings
from satori.core.clock import Clock, SystemClock
from satori.core.ids import IdGenerator, Uuid4Generator
from satori.domain.affect import EmotionManager
from satori.domain.memory import MemoryManager
from satori.domain.models import UserModelManager, WorldModelManager
from satori.domain.personality_evolution import PersonalityManager
from satori.domain.positions import PositionManager
from satori.domain.relationship import RelationshipManager
from satori.domain.semantic_memory import SemanticMemoryManager
from satori.infrastructure.persistence.affect_uow import (
    SQLAlchemyAffectiveConversationUnitOfWork,
    SQLAlchemyAffectiveStateUnitOfWork,
)
from satori.infrastructure.persistence.conversation_uow import (
    SQLAlchemyConversationHistoryUnitOfWork,
)
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.initial_self_uow import (
    SQLAlchemyInitialSelfUnitOfWork,
)
from satori.infrastructure.persistence.memory_uow import SQLAlchemyEpisodicMemoryUnitOfWork
from satori.infrastructure.persistence.models_uow import SQLAlchemyCurrentModelsUnitOfWork
from satori.infrastructure.persistence.personality_uow import (
    SQLAlchemyPersonalityUnitOfWork,
)
from satori.infrastructure.persistence.positions_uow import SQLAlchemyPositionsUnitOfWork
from satori.infrastructure.persistence.reflection_uow import SQLAlchemyReflectionUnitOfWork
from satori.infrastructure.persistence.relationship_uow import SQLAlchemyRelationshipUnitOfWork
from satori.infrastructure.persistence.retrieval_uow import (
    SQLAlchemyEpisodicMemoryIndexUnitOfWork,
)
from satori.infrastructure.persistence.semantic_uow import SQLAlchemySemanticMemoryUnitOfWork


@dataclass(frozen=True, slots=True)
class InitialSelfServices:
    """Composed Stage 2 use cases without shared mutable domain state."""

    activate: ActivateSatori
    get_identity: GetSatoriIdentity
    get_self: GetInitialSelfSnapshot


@dataclass(frozen=True, slots=True)
class PersonalityServices:
    """Stage 14 personality owner, inspection, approval, and restore boundary."""

    evolution: GetPersonalityEvolution
    approve_checkpoint: ApprovePersonalityCheckpoint
    restore_checkpoint: RestorePersonalityCheckpoint
    apply_reflection: ApplyPersonalityReflection


@dataclass(frozen=True, slots=True)
class ConversationServices:
    """Composed Stage 4 history, conversation, and memory use cases."""

    talk: TalkToSatori
    post_response: ProcessPostResponse
    start_session: StartConversationSession
    close_session: CloseConversationSession
    history: GetConversationHistory
    memories: GetEpisodicMemories
    retrieve_memories: RetrieveEpisodicMemories | None
    index_memories: IndexAllEpisodicMemories | None
    semantic_claims: GetSemanticClaims
    process_semantic: FormSemanticMemory | None
    backfill_semantic: BackfillSemanticMemory | None
    emotion_status: GetAffectiveStatus
    emotion_history: GetAffectiveHistory
    relationship_status: GetRelationshipStatus
    relationship_history: GetRelationshipHistory
    process_relationship: ProcessRelationshipForInteraction | None
    current_models: GetCurrentModels
    process_models: FormCurrentModels | None
    backfill_models: BackfillCurrentModels | None
    positions: GetSatoriPositions
    process_positions: FormSatoriPositions | None
    backfill_positions: BackfillSatoriPositions | None
    reflections: GetReflections
    process_reflection: ProcessReflection | None
    apply_reflection: ApplyReflectionProposals | None
    personality: PersonalityServices


def build_initial_self_services(
    database: Database,
    *,
    clock: Clock | None = None,
    id_generator: IdGenerator | None = None,
) -> InitialSelfServices:
    """Wire application use cases to the SQLAlchemy adapter."""

    active_clock = clock or SystemClock()
    active_id_generator = id_generator or Uuid4Generator()

    def unit_of_work_factory() -> SQLAlchemyInitialSelfUnitOfWork:
        return SQLAlchemyInitialSelfUnitOfWork(database.session_factory)

    get_self = GetInitialSelfSnapshot(unit_of_work_factory=unit_of_work_factory)
    return InitialSelfServices(
        activate=ActivateSatori(
            unit_of_work_factory=unit_of_work_factory,
            clock=active_clock,
            id_generator=active_id_generator,
        ),
        get_identity=GetSatoriIdentity(get_self=get_self),
        get_self=get_self,
    )


def build_conversation_services(
    database: Database,
    initial_self: InitialSelfServices,
    provider: ConversationProvider,
    episode_provider: EpisodeFormationProvider,
    settings: Settings,
    *,
    clock: Clock | None = None,
    id_generator: IdGenerator | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    semantic_provider: SemanticFormationProvider | None = None,
    appraisal_provider: AffectiveAppraisalProvider | None = None,
    relationship_provider: RelationshipProvider | None = None,
    model_provider: ModelFormationProvider | None = None,
    position_provider: PositionFormationProvider | None = None,
    reflection_provider: ReflectionGenerationPort | None = None,
) -> ConversationServices:
    """Wire InteractionLog and MemoryManager to replaceable provider capabilities."""

    active_clock = clock or SystemClock()
    active_id_generator = id_generator or Uuid4Generator()

    def conversation_uow_factory() -> SQLAlchemyConversationHistoryUnitOfWork:
        return SQLAlchemyConversationHistoryUnitOfWork(database.session_factory)

    def memory_uow_factory() -> SQLAlchemyEpisodicMemoryUnitOfWork:
        return SQLAlchemyEpisodicMemoryUnitOfWork(database.session_factory)

    def index_uow_factory() -> SQLAlchemyEpisodicMemoryIndexUnitOfWork:
        return SQLAlchemyEpisodicMemoryIndexUnitOfWork(database.session_factory)

    def semantic_uow_factory() -> SQLAlchemySemanticMemoryUnitOfWork:
        return SQLAlchemySemanticMemoryUnitOfWork(database.session_factory)

    def affect_uow_factory() -> SQLAlchemyAffectiveStateUnitOfWork:
        return SQLAlchemyAffectiveStateUnitOfWork(database.session_factory)

    def affective_conversation_uow_factory() -> SQLAlchemyAffectiveConversationUnitOfWork:
        return SQLAlchemyAffectiveConversationUnitOfWork(database.session_factory)

    def relationship_uow_factory() -> SQLAlchemyRelationshipUnitOfWork:
        return SQLAlchemyRelationshipUnitOfWork(database.session_factory)

    def models_uow_factory() -> SQLAlchemyCurrentModelsUnitOfWork:
        return SQLAlchemyCurrentModelsUnitOfWork(database.session_factory)

    def positions_uow_factory() -> SQLAlchemyPositionsUnitOfWork:
        return SQLAlchemyPositionsUnitOfWork(database.session_factory)

    def reflection_uow_factory() -> SQLAlchemyReflectionUnitOfWork:
        return SQLAlchemyReflectionUnitOfWork(database.session_factory)

    def personality_uow_factory() -> SQLAlchemyPersonalityUnitOfWork:
        return SQLAlchemyPersonalityUnitOfWork(database.session_factory)

    interaction_log = InteractionLog(
        unit_of_work_factory=conversation_uow_factory,
        clock=active_clock,
        id_generator=active_id_generator,
        default_counterparty_id=settings.default_counterparty_id,
    )
    form_episode = FormEpisodeForInteraction(
        unit_of_work_factory=memory_uow_factory,
        provider=episode_provider,
        memory_manager=MemoryManager(),
        clock=active_clock,
        id_generator=active_id_generator,
    )
    retrieval_policy = RetrievalPolicy(
        minimum_similarity=settings.retrieval_minimum_similarity,
        candidate_limit=settings.retrieval_candidate_limit,
        top_k=settings.retrieval_top_k,
        max_context_chars=settings.retrieval_max_context_chars,
        semantic_weight=settings.retrieval_semantic_weight,
        importance_weight=settings.retrieval_importance_weight,
        recency_weight=settings.retrieval_recency_weight,
        recency_half_life_days=settings.retrieval_recency_half_life_days,
    )
    retrieve_memories = (
        RetrieveEpisodicMemories(
            unit_of_work_factory=index_uow_factory,
            provider=embedding_provider,
            policy=retrieval_policy,
        )
        if embedding_provider is not None
        else None
    )
    index_memory = (
        IndexEpisodicMemory(
            unit_of_work_factory=index_uow_factory,
            provider=embedding_provider,
            clock=active_clock,
        )
        if embedding_provider is not None
        else None
    )
    index_memories = (
        IndexAllEpisodicMemories(
            unit_of_work_factory=index_uow_factory,
            provider=embedding_provider,
            clock=active_clock,
        )
        if embedding_provider is not None
        else None
    )
    form_semantic = (
        FormSemanticMemory(
            unit_of_work_factory=semantic_uow_factory,
            provider=semantic_provider,
            manager=SemanticMemoryManager(),
            clock=active_clock,
            id_generator=active_id_generator,
            max_claims_per_memory=settings.semantic_max_claims_per_memory,
            max_source_memories=settings.semantic_max_source_memories,
        )
        if semantic_provider is not None
        else None
    )
    retrieve_semantic = RetrieveSemanticClaims(
        unit_of_work_factory=semantic_uow_factory,
        top_k=settings.semantic_retrieval_top_k,
        max_context_chars=settings.semantic_retrieval_max_context_chars,
    )
    ensure_affect = EnsureAffectiveState(unit_of_work_factory=affect_uow_factory)
    ensure_relationship = EnsureRelationship(
        unit_of_work_factory=relationship_uow_factory,
        clock=active_clock,
        id_generator=active_id_generator,
    )
    get_relationship = GetRelationshipForSession(
        unit_of_work_factory=relationship_uow_factory,
        ensure=ensure_relationship,
    )
    process_relationship = (
        ProcessRelationshipForInteraction(
            unit_of_work_factory=relationship_uow_factory,
            ensure=ensure_relationship,
            provider=relationship_provider,
            manager=RelationshipManager(),
            clock=active_clock,
            id_generator=active_id_generator,
        )
        if relationship_provider is not None
        else None
    )
    form_models = (
        FormCurrentModels(
            unit_of_work_factory=models_uow_factory,
            provider=model_provider,
            user_manager=UserModelManager(),
            world_manager=WorldModelManager(),
            clock=active_clock,
            id_generator=active_id_generator,
            max_source_messages=settings.model_formation_max_source_messages,
            max_user_claims=settings.model_formation_max_user_claims,
            max_world_claims=settings.model_formation_max_world_claims,
        )
        if model_provider is not None
        else None
    )
    form_positions = (
        FormSatoriPositions(
            unit_of_work_factory=positions_uow_factory,
            provider=position_provider,
            manager=PositionManager(),
            clock=active_clock,
            id_generator=active_id_generator,
            max_source_messages=settings.position_formation_max_source_messages,
            max_positions=settings.position_formation_max_positions,
        )
        if position_provider is not None
        else None
    )
    personality_manager = PersonalityManager()
    personality_evolution = GetPersonalityEvolution(
        unit_of_work_factory=personality_uow_factory,
        clock=active_clock,
    )
    apply_personality_reflection = ApplyPersonalityReflection(
        unit_of_work_factory=personality_uow_factory,
        manager=personality_manager,
        clock=active_clock,
        id_generator=active_id_generator,
    )
    personality_services = PersonalityServices(
        evolution=personality_evolution,
        approve_checkpoint=ApprovePersonalityCheckpoint(
            unit_of_work_factory=personality_uow_factory,
            clock=active_clock,
            id_generator=active_id_generator,
        ),
        restore_checkpoint=RestorePersonalityCheckpoint(
            unit_of_work_factory=personality_uow_factory,
            manager=personality_manager,
            clock=active_clock,
            id_generator=active_id_generator,
        ),
        apply_reflection=apply_personality_reflection,
    )
    process_reflection = (
        ProcessReflection(
            reflection_uow_factory=reflection_uow_factory,
            positions_uow_factory=positions_uow_factory,
            provider=reflection_provider,
            clock=active_clock,
            id_generator=active_id_generator,
            personality_context=personality_evolution,
            personality_manager=personality_manager,
        )
        if reflection_provider is not None
        else None
    )
    apply_reflection = (
        ApplyReflectionProposals(
            reflection_uow_factory=reflection_uow_factory,
            positions_uow_factory=positions_uow_factory,
            manager=PositionManager(),
            clock=active_clock,
            id_generator=active_id_generator,
            personality_router=apply_personality_reflection,
        )
        if reflection_provider is not None
        else None
    )
    prepare_affect = PrepareAffectiveContext(
        ensure_state=ensure_affect,
        manager=EmotionManager(),
        provider=appraisal_provider,
        clock=active_clock,
    )
    get_current_models = GetCurrentModels(unit_of_work_factory=models_uow_factory)
    get_positions = GetSatoriPositions(
        unit_of_work_factory=positions_uow_factory,
        top_k=settings.position_context_top_k,
        max_context_chars=settings.position_context_max_chars,
    )
    talk = TalkToSatori(
        get_self=initial_self.get_self,
        context_composer=CharacterContextComposer(
            language_provider=settings.conversation_provider.value,
            language_model=settings.conversation_model,
        ),
        request_builder=ConversationRequestBuilder(
            policy=BEHAVIOR_POLICY_V10,
            max_context_chars=settings.conversation_max_context_chars,
            temperature=settings.conversation_temperature,
            max_output_tokens=settings.conversation_max_output_tokens,
        ),
        grounding_gate=ResponseGroundingGate(),
        interaction_log=interaction_log,
        provider=provider,
        max_user_chars=settings.conversation_max_input_chars,
        max_response_chars=settings.conversation_max_response_chars,
        retrieve_memories=retrieve_memories,
        retrieve_semantic=retrieve_semantic,
        prepare_affect=prepare_affect,
        finalize_affect=FinalizeAffectiveInteraction(
            unit_of_work_factory=affective_conversation_uow_factory,
            clock=active_clock,
            id_generator=active_id_generator,
        ),
        recent_conversation=GetRecentConversation(
            unit_of_work_factory=conversation_uow_factory,
            max_turns=settings.recent_conversation_max_turns,
            max_chars=settings.recent_conversation_max_chars,
        ),
        recap_conversation=GetRecentConversation(
            unit_of_work_factory=conversation_uow_factory,
            max_turns=SESSION_RECAP_MAX_RECENT_TURNS,
            max_chars=settings.recent_conversation_max_chars,
        ),
        get_relationship=get_relationship,
        get_current_models=get_current_models,
        get_positions=get_positions,
        cognition_pipeline=SafeCognitionPipeline(
            planner=DeterministicCognitionPlanner(),
            fallback=DeterministicCognitionPlanner(),
        ),
    )
    return ConversationServices(
        talk=talk,
        post_response=ProcessPostResponse(
            interaction_log=interaction_log,
            form_episode=form_episode,
            index_memory=index_memory,
            form_semantic=form_semantic,
            process_relationship=process_relationship,
            form_models=form_models,
            form_positions=form_positions,
            process_reflection=process_reflection,
            apply_reflection=apply_reflection,
            identity_id_provider=lambda: initial_self.get_identity.execute().identity_id,
        ),
        start_session=StartConversationSession(
            get_self=initial_self.get_self,
            unit_of_work_factory=conversation_uow_factory,
            clock=active_clock,
            id_generator=active_id_generator,
            default_counterparty_id=settings.default_counterparty_id,
        ),
        close_session=CloseConversationSession(
            unit_of_work_factory=conversation_uow_factory,
            clock=active_clock,
        ),
        history=GetConversationHistory(unit_of_work_factory=conversation_uow_factory),
        memories=GetEpisodicMemories(unit_of_work_factory=memory_uow_factory),
        retrieve_memories=retrieve_memories,
        index_memories=index_memories,
        semantic_claims=GetSemanticClaims(unit_of_work_factory=semantic_uow_factory),
        process_semantic=form_semantic,
        backfill_semantic=(
            BackfillSemanticMemory(
                unit_of_work_factory=semantic_uow_factory,
                form_semantic=form_semantic,
            )
            if form_semantic is not None
            else None
        ),
        emotion_status=GetAffectiveStatus(
            ensure_state=ensure_affect,
            unit_of_work_factory=affect_uow_factory,
            clock=active_clock,
        ),
        emotion_history=GetAffectiveHistory(unit_of_work_factory=affect_uow_factory),
        relationship_status=GetRelationshipStatus(
            unit_of_work_factory=relationship_uow_factory,
            ensure=ensure_relationship,
        ),
        relationship_history=GetRelationshipHistory(
            unit_of_work_factory=relationship_uow_factory,
            ensure=ensure_relationship,
        ),
        process_relationship=process_relationship,
        current_models=get_current_models,
        process_models=form_models,
        backfill_models=(
            BackfillCurrentModels(
                unit_of_work_factory=models_uow_factory,
                form_models=form_models,
            )
            if form_models is not None
            else None
        ),
        positions=get_positions,
        process_positions=form_positions,
        backfill_positions=(
            BackfillSatoriPositions(
                unit_of_work_factory=positions_uow_factory,
                form_positions=form_positions,
            )
            if form_positions is not None
            else None
        ),
        reflections=GetReflections(reflection_uow_factory=reflection_uow_factory),
        process_reflection=process_reflection,
        apply_reflection=apply_reflection,
        personality=personality_services,
    )
