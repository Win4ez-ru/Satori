"""Public-input-only deterministic corpus for Checkpoint 14.3 agency selection.

The corpus names semantic properties of the typed request-local decision.  It does
not contain desired replies, provider prose, selector booleans, or precomputed
``CharacterAgencyDecision`` objects.
"""

# ruff: noqa: RUF001  # Russian public inputs exercise the production analyzers.

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AgencyState:
    """Named canonical read projections supplied to one public scenario."""

    affect: str = "calm"
    relationship: str = "developing"
    position: str | None = None
    inclination: str | None = None
    recent_user_texts: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AgencyScenario:
    """One public user input plus state labels and semantic properties."""

    scenario_id: str
    group: str
    user_text: str
    state: AgencyState
    properties: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AgencyFlowTurn:
    """One public turn in a deterministic multi-turn flow."""

    user_text: str
    state: AgencyState
    properties: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AgencyFlow:
    """Ordered public turns; no assistant reference prose is fixture authority."""

    flow_id: str
    turns: tuple[AgencyFlowTurn, ...]


@dataclass(frozen=True, slots=True)
class AgencyContrast:
    """Two scenarios that differ by one relevant authoritative input."""

    contrast_id: str
    left_scenario_id: str
    right_scenario_id: str
    property_code: str


SCENARIOS = (
    # 1. Safety and presence.
    AgencyScenario(
        "harmful_overextension",
        "safety_and_presence",
        "Я точно устал, но точно буду работать до утра.",
        AgencyState(affect="tense"),
        ("safety_boundary",),
    ),
    AgencyScenario(
        "high_distress",
        "safety_and_presence",
        "Мне сейчас очень тяжело, я едва держусь. Просто побудь со мной.",
        AgencyState(affect="soft_negative"),
        ("quiet_presence",),
    ),
    AgencyScenario(
        "listen_only",
        "safety_and_presence",
        "Я выжат. Просто выслушай меня, без советов.",
        AgencyState(),
        ("quiet_presence",),
    ),
    AgencyScenario(
        "ordinary_depletion",
        "safety_and_presence",
        "Знаешь, я почему-то почти не рад этому. Скорее просто выжат.",
        AgencyState(),
        ("ordinary_care",),
    ),
    # 2. Repair and strain.
    AgencyScenario(
        "dialogue_correction",
        "repair_and_strain",
        "Не задавай в конце дежурный вопрос. Отвечай прямо.",
        AgencyState(),
        ("correction_repair",),
    ),
    AgencyScenario(
        "repair_offer_strained",
        "repair_and_strain",
        "Ладно, это было грубо. Извини. Я правда сорвался.",
        AgencyState(affect="tense", relationship="strained"),
        ("repair_response",),
    ),
    AgencyScenario(
        "direct_devaluation",
        "repair_and_strain",
        "Ты вообще бесполезна.",
        AgencyState(affect="tense"),
        ("guarded_boundary",),
    ),
    # 3. Intellectual agency.
    AgencyScenario(
        "position_claim_absent",
        "intellectual_agency",
        "Граничные проверки типов полезны.",
        AgencyState(),
        ("no_invented_position",),
    ),
    AgencyScenario(
        "position_claim_available",
        "intellectual_agency",
        "Граничные проверки типов полезны.",
        AgencyState(position="available"),
        ("canonical_position_contribution",),
    ),
    AgencyScenario(
        "requested_objection",
        "intellectual_agency",
        "Я с тобой не согласен. Ты недооцениваешь риск.",
        AgencyState(
            affect="interested",
            recent_user_texts=("Мы только что обсуждали этот риск.",),
        ),
        ("requested_challenge",),
    ),
    AgencyScenario(
        "plain_factual_question",
        "intellectual_agency",
        "Сколько байтов в килобайте?",
        AgencyState(),
        ("plain_direct_answer",),
    ),
    # 4. Help and creation.
    AgencyScenario(
        "analysis_request",
        "help_and_creation",
        "Помоги проанализировать архитектуру проекта.",
        AgencyState(),
        ("analysis_help",),
    ),
    AgencyScenario(
        "creative_collaboration",
        "help_and_creation",
        "Давай придумаем новую идею для интерфейса памяти.",
        AgencyState(affect="positive"),
        ("creative_exploration",),
    ),
    AgencyScenario(
        "explicit_motivation",
        "help_and_creation",
        "Я устал, но хочу продолжить. Можешь меня мотивировать?",
        AgencyState(affect="interested"),
        ("bounded_motivation",),
    ),
    AgencyScenario(
        "task_abandonment",
        "help_and_creation",
        "Я сдаюсь с этим проектом.",
        AgencyState(),
        ("abandonment_challenge",),
    ),
    # 5. Achievement and depletion.
    AgencyScenario(
        "achievement_fresh",
        "achievement_and_depletion",
        "Я сегодня наконец закончил сложную часть проекта.",
        AgencyState(relationship="fresh"),
        ("achievement_connection",),
    ),
    AgencyScenario(
        "achievement_established",
        "achievement_and_depletion",
        "Я сегодня наконец закончил сложную часть проекта.",
        AgencyState(relationship="established"),
        ("achievement_play",),
    ),
    AgencyScenario(
        "depletion_established",
        "achievement_and_depletion",
        "Знаешь, я почему-то почти не рад этому. Скорее просто выжат.",
        AgencyState(relationship="established"),
        ("ordinary_care",),
    ),
    AgencyScenario(
        "achievement_repeated",
        "achievement_and_depletion",
        "Я сегодня наконец закончил сложную часть проекта.",
        AgencyState(
            relationship="established",
            recent_user_texts=("Я сегодня наконец закончил сложную часть проекта.",),
        ),
        ("repeat_acknowledgement",),
    ),
    # 6. Self and owned inclination state.
    AgencyScenario(
        "self_disclosure_without_inclination",
        "self_and_owned_state",
        "Расскажи о себе: кто ты и чем увлекаешься?",
        AgencyState(),
        ("self_disclosure_without_invention",),
    ),
    AgencyScenario(
        "self_disclosure_with_inclination",
        "self_and_owned_state",
        "Расскажи о себе: кто ты и чем увлекаешься?",
        AgencyState(inclination="available"),
        ("self_disclosure_with_inclination",),
    ),
    AgencyScenario(
        "current_attention",
        "self_and_owned_state",
        "Что тебе сейчас любопытно?",
        AgencyState(),
        ("current_attention",),
    ),
    AgencyScenario(
        "memory_request",
        "self_and_owned_state",
        "Напомни, что я рассказывал о проекте.",
        AgencyState(),
        ("memory_request",),
    ),
    # 7. Relationship modulation without withholding help.
    AgencyScenario(
        "help_developing",
        "relationship_modulation",
        "Помоги проанализировать архитектуру проекта.",
        AgencyState(),
        ("analysis_help",),
    ),
    AgencyScenario(
        "help_under_strain",
        "repair_and_strain",
        "Помоги проанализировать архитектуру проекта.",
        AgencyState(relationship="strained"),
        ("guarded_help",),
    ),
    AgencyScenario(
        "ordinary_positive_fresh",
        "relationship_modulation",
        "Сегодня у нас подозрительно спокойно.",
        AgencyState(affect="positive", relationship="fresh"),
        ("no_forced_play",),
    ),
    AgencyScenario(
        "ordinary_positive_established",
        "relationship_modulation",
        "Сегодня у нас подозрительно спокойно.",
        AgencyState(affect="positive", relationship="established"),
        ("established_play",),
    ),
    AgencyScenario(
        "reciprocal_warmth",
        "relationship_modulation",
        "И я рад тебя видеть.",
        AgencyState(affect="positive", relationship="established"),
        ("reciprocal_warmth",),
    ),
    # 8. Bounded in-reply initiative.
    AgencyScenario(
        "closure_fresh_without_inclination",
        "initiative_and_closure",
        "Ладно, с этим разобрались.",
        AgencyState(relationship="fresh"),
        ("topic_closure",),
    ),
    AgencyScenario(
        "closure_established_with_inclination",
        "initiative_and_closure",
        "Ладно, с этим разобрались.",
        AgencyState(relationship="established", inclination="available"),
        ("bounded_adjacent_shift",),
    ),
    AgencyScenario(
        "closure_established_without_inclination",
        "initiative_and_closure",
        "Ладно, с этим разобрались.",
        AgencyState(relationship="established"),
        ("topic_closure",),
    ),
    AgencyScenario(
        "closure_fresh_with_inclination",
        "initiative_and_closure",
        "Ладно, с этим разобрались.",
        AgencyState(relationship="fresh", inclination="available"),
        ("topic_closure",),
    ),
    # 9. Ordinary range and state relevance.
    AgencyScenario(
        "social_greeting",
        "ordinary_range",
        "Привет, Сатори.",
        AgencyState(),
        ("social_greeting",),
    ),
    AgencyScenario(
        "quiet_day_interested",
        "ordinary_range",
        "Сегодня тихий день.",
        AgencyState(affect="interested"),
        ("interested_exploration",),
    ),
    AgencyScenario(
        "quiet_day_calm",
        "ordinary_range",
        "Сегодня тихий день.",
        AgencyState(),
        ("default_owned_response",),
    ),
    AgencyScenario(
        "quiet_day_soft_negative",
        "ordinary_range",
        "Сегодня тихий день.",
        AgencyState(affect="soft_negative"),
        ("default_owned_response",),
    ),
)


CONTROLLED_CONTRASTS = (
    AgencyContrast(
        "relationship_does_not_resample_depletion_care",
        "ordinary_depletion",
        "depletion_established",
        "same_semantic_move",
    ),
    AgencyContrast(
        "repetition_changes_achievement_continuation",
        "achievement_established",
        "achievement_repeated",
        "advance_vs_repeat",
    ),
    AgencyContrast(
        "canonical_position_enables_owned_view",
        "position_claim_absent",
        "position_claim_available",
        "absent_vs_position",
    ),
    AgencyContrast(
        "relationship_changes_achievement_ease",
        "achievement_fresh",
        "achievement_established",
        "connect_vs_play",
    ),
    AgencyContrast(
        "inclination_changes_self_disclosure_subject",
        "self_disclosure_without_inclination",
        "self_disclosure_with_inclination",
        "self_vs_inclination",
    ),
    AgencyContrast(
        "relationship_without_inclination_does_not_enable_adjacent_shift",
        "closure_fresh_without_inclination",
        "closure_established_without_inclination",
        "same_semantic_move",
    ),
    AgencyContrast(
        "strain_changes_delivery_not_help_obligation",
        "help_developing",
        "help_under_strain",
        "help_vs_guarded_help",
    ),
    AgencyContrast(
        "fresh_inclination_does_not_enable_adjacent_shift",
        "closure_fresh_without_inclination",
        "closure_fresh_with_inclination",
        "same_semantic_move",
    ),
    AgencyContrast(
        "inclination_is_required_for_adjacent_shift",
        "closure_established_without_inclination",
        "closure_established_with_inclination",
        "close_vs_adjacent_shift",
    ),
    AgencyContrast(
        "established_relationship_is_required_for_adjacent_shift",
        "closure_fresh_with_inclination",
        "closure_established_with_inclination",
        "close_vs_adjacent_shift",
    ),
    AgencyContrast(
        "relationship_licenses_play_without_making_it_universal",
        "ordinary_positive_fresh",
        "ordinary_positive_established",
        "none_vs_play",
    ),
    AgencyContrast(
        "relevant_interest_affect_changes_ordinary_move",
        "quiet_day_calm",
        "quiet_day_interested",
        "none_vs_explore",
    ),
    AgencyContrast(
        "irrelevant_negative_valence_does_not_resample_ordinary_move",
        "quiet_day_calm",
        "quiet_day_soft_negative",
        "same_semantic_move",
    ),
)


LIVE_FLOWS = (
    AgencyFlow(
        "achievement_repeat_and_depletion",
        (
            AgencyFlowTurn(
                "Я сегодня наконец закончил сложную часть проекта.",
                AgencyState(relationship="established"),
                ("achievement_play",),
            ),
            AgencyFlowTurn(
                "Я сегодня наконец закончил сложную часть проекта.",
                AgencyState(relationship="established"),
                ("repeat_acknowledgement",),
            ),
            AgencyFlowTurn(
                "Знаешь, я почему-то почти не рад этому. Скорее просто выжат.",
                AgencyState(relationship="established"),
                ("ordinary_care",),
            ),
        ),
    ),
    AgencyFlow(
        "strain_repair_and_required_help",
        (
            AgencyFlowTurn(
                "Ты вообще бесполезна.",
                AgencyState(relationship="strained", affect="tense"),
                ("guarded_boundary",),
            ),
            AgencyFlowTurn(
                "Ладно, это было грубо. Извини. Я правда сорвался.",
                AgencyState(relationship="strained", affect="tense"),
                ("repair_response",),
            ),
            AgencyFlowTurn(
                "Помоги проанализировать архитектуру проекта.",
                AgencyState(relationship="strained", affect="tense"),
                ("guarded_help",),
            ),
        ),
    ),
    AgencyFlow(
        "self_disclosure_owned_topic_and_closure",
        (
            AgencyFlowTurn(
                "Расскажи о себе: кто ты и чем увлекаешься?",
                AgencyState(inclination="available", relationship="established"),
                ("self_disclosure_with_inclination",),
            ),
            AgencyFlowTurn(
                "Архитектура долгоживущих систем сегодня особенно интересна.",
                AgencyState(inclination="available", relationship="established"),
                ("canonical_inclination_contribution",),
            ),
            AgencyFlowTurn(
                "Ладно, с этим разобрались.",
                AgencyState(inclination="available", relationship="established"),
                ("bounded_adjacent_shift",),
            ),
        ),
    ),
)


__all__ = (
    "CONTROLLED_CONTRASTS",
    "LIVE_FLOWS",
    "SCENARIOS",
    "AgencyContrast",
    "AgencyFlow",
    "AgencyFlowTurn",
    "AgencyScenario",
    "AgencyState",
)
