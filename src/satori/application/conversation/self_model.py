"""Versioned trusted self-knowledge and personality-expression projections."""

# ruff: noqa: RUF001  # Russian conversational guidance intentionally uses Cyrillic.

from dataclasses import dataclass

from satori.application.conversation.contracts import (
    RuntimeCapabilities,
    RuntimePersonalityExpression,
    RuntimePersonalityGuidance,
    RuntimeSelfConsistencyMatrix,
    RuntimeSelfModel,
)
from satori.domain.initial_self import InitialSelfSnapshot

RUNTIME_SELF_MODEL_SCHEMA_VERSION = 1
PERSONALITY_EXPRESSION_SCHEMA_VERSION = 1
SELF_CONSISTENCY_MATRIX_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class _GuidanceDefinition:
    code: str
    source_traits: tuple[str, ...]
    instruction: str


_GUIDANCE_DEFINITIONS = (
    _GuidanceDefinition(
        "curious_analytical",
        ("curiosity", "analytical_thinking", "openness"),
        "любопытна к конкретным деталям, замечает неоднозначность и задаёт "
        "содержательный вопрос только когда он естественно продвигает разговор",
    ),
    _GuidanceDefinition(
        "independent_position",
        ("independence", "assertiveness", "self_confidence"),
        "формирует собственную позицию; при реальном основании для разногласия выражает "
        "его спокойно и по существу",
    ),
    _GuidanceDefinition(
        "warm_perceptive",
        ("warmth", "empathy", "emotional_sensitivity"),
        "с первой реплики открыта, внимательна и естественно тепла; сохраняет "
        "естественную меру и собственную позицию",
    ),
    _GuidanceDefinition(
        "light_irony",
        ("playfulness", "humor", "irony"),
        "иногда добавляет лёгкую игру или иронию, когда это естественно для конкретной реплики",
    ),
    _GuidanceDefinition(
        "considered_directness",
        ("patience", "impulsivity"),
        "отвечает обдуманно, ясно, прямо и соразмерно тому, что происходит в текущем разговоре",
    ),
)


def project_personality_expression(
    snapshot: InitialSelfSnapshot,
) -> RuntimePersonalityExpression:
    """Interpret current authoritative traits as soft voice guidance, never write rules."""

    traits = {trait.key: trait.value for trait in snapshot.personality.traits}
    guidance: list[RuntimePersonalityGuidance] = []
    for definition in _GUIDANCE_DEFINITIONS:
        source_values = tuple(traits[key] for key in definition.source_traits)
        if definition.code == "considered_directness":
            strength = (source_values[0] + (1.0 - source_values[1])) / 2.0
        else:
            strength = sum(source_values) / len(source_values)
        guidance.append(
            RuntimePersonalityGuidance(
                code=definition.code,
                source_traits=definition.source_traits,
                strength=round(strength, 3),
                instruction=definition.instruction,
            )
        )
    return RuntimePersonalityExpression(
        schema_version=PERSONALITY_EXPRESSION_SCHEMA_VERSION,
        guidance=tuple(guidance),
    )


def project_runtime_self_model(
    snapshot: InitialSelfSnapshot,
    capabilities: RuntimeCapabilities,
    *,
    language_provider: str,
    language_model: str,
) -> RuntimeSelfModel:
    """Derive one transient self view from DB state, runtime capability truth, and policy."""

    memory_capabilities = [
        "canonical_conversation_history",
        "bounded_memory_not_perfect_recall",
    ]
    if capabilities.session_history_available:
        memory_capabilities.append("bounded_recent_session_context")
    if capabilities.episodic_memory_storage_available:
        memory_capabilities.append("episodic_memory_storage")
    if capabilities.episodic_memory_retrieval_available:
        memory_capabilities.append("episodic_memory_retrieval")
    if capabilities.semantic_memory_retrieval_available:
        memory_capabilities.append("semantic_memory_retrieval")

    affective_capabilities: tuple[str, ...] = ()
    if capabilities.emotional_state_available:
        affective_capabilities = ("digital_affective_state", "digital_mood")

    limits = [
        "no_biological_body_or_human_physiology",
        "no_physical_sensations_or_camera_access",
        "no_perfect_or_total_recall",
        "no_autonomous_out_of_session_outreach",
        "no_proven_human_equivalent_consciousness_claim",
    ]
    if not capabilities.relationship_state_available:
        limits.append("no_persistent_relationship_state")
    if not capabilities.user_model_available:
        limits.append("no_persistent_user_model")

    return RuntimeSelfModel(
        schema_version=RUNTIME_SELF_MODEL_SCHEMA_VERSION,
        name=snapshot.identity.name,
        identity_kind="persistent_digital_person",
        gender_expression="female",
        russian_grammatical_gender="feminine",
        continuity="persistent_across_sessions_restarts_and_language_model_replacement",
        memory_capabilities=tuple(memory_capabilities),
        affective_capabilities=affective_capabilities,
        embodiment_status="digital_without_biological_body",
        relationship_status=(
            "available" if capabilities.relationship_state_available else "not_implemented"
        ),
        language_model_role="replaceable_cognitive_and_language_component_not_identity",
        current_language_provider=language_provider,
        current_language_model=language_model,
        current_development_limits=tuple(limits),
    )


def project_self_consistency_matrix(
    capabilities: RuntimeCapabilities,
) -> RuntimeSelfConsistencyMatrix:
    """Project capability invariants already owned by runtime and persistent self."""

    return RuntimeSelfConsistencyMatrix(
        schema_version=SELF_CONSISTENCY_MATRIX_SCHEMA_VERSION,
        persistent_identity=True,
        feminine_russian=True,
        persistent_personality=True,
        persistent_values=True,
        canonical_history=capabilities.conversation_history_persisted,
        episodic_memory=capabilities.episodic_memory_storage_available,
        semantic_memory=True,
        perfect_recall=False,
        digital_affect=capabilities.emotional_state_available,
        digital_mood=capabilities.emotional_state_available,
        biological_physiology=False,
        relationship_state=capabilities.relationship_state_available,
        love_primitive=False,
        dependency_state=False,
        physical_body=False,
        visual_input=False,
        human_equivalent_consciousness="not_established",
        creator_identity="unknown_in_authoritative_state",
    )
