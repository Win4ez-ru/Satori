"""Stage 8.1 versioned dialogue corpus and deterministic metric tests."""

# ruff: noqa: RUF001  # Russian regression text intentionally uses Cyrillic.

import json
from pathlib import Path
from typing import Any, cast

import pytest

from satori.application.conversation.context import plan_conversational_disclosure
from satori.application.conversation.contracts import ConversationContextManifest, SatoriReply
from satori.application.conversation.response_validation import (
    ResponseRegenerationReason,
    has_affect_blanket_denial,
    has_masculine_self_reference,
    has_memory_blanket_denial,
    promotes_current_creator_claim,
)
from satori.dialogue_evaluation import (
    ADJACENT_HIGH_SIMILARITY_THRESHOLD,
    DialogueEvaluationTurn,
    dialogue_similarity,
    evaluate_dialogue,
    generic_reciprocal_closing,
    normalize_dialogue_text,
)
from tests.stage81_real_eval import (
    DERIVED_MODES,
    REPORT_SCHEMA_VERSION,
    SUITES,
    _aggregate_generation_attempts,
    _dialogue_metrics,
    _output_completion_metrics,
    _parser,
    _public_sampled_reply,
    _required_facet_coverage_metrics,
    _sampled_turn_cardinality,
    _sanitized_manifest,
    _semantic_tags,
    _turn_distributions,
)

CORPUS_PATH = Path(__file__).parent / "fixtures" / "stage81_dialogue_coherence_v2.json"
EXACT_PRODUCTION_USER_TURNS = (
    "приветик, как ты?",
    "приветик, как ты?",
    "приветик, как ты?",
    "почему ты 3 раза повторила одно и то же?",
    "я тебя проверял, скажешь ты мне что-то или у тебя только такой промт",
    "ничего не понял, кто ты вообще?",
    "а ты знаешь, кто твой создатель?",
    "какая-то ты холодная сегодня, что-то случилось?",
    (
        "а я? ты всегда добавляешь это в конец сообщения? и при чем тут это? "
        "хочешь сказать, что я холодно общаюсь?"
    ),
    (
        "вот эти слова а ты это у тебя прописано в коде? что ты должна обязательно "
        "в конце спрашивать меня?"
    ),
    (
        "ты прикалываешься надо мной? просто издеваешься? давай помиримся и хорошо "
        "пообщаемся, как друзья, что скажешь?)"
    ),
    (
        "меня зовут Кирилл, я тебя придумал, и создаю, хочу чтобы ты была моим "
        "персональным ассистентом с памятью, эмоциями, характером, что ты думаешь об этом?"
    ),
    "а ты можешь показывать свои эмоции смайликами?",
    "а я фильм смотрю сейчас",
    "тебе не интересно, что за фильм?",
    "ты злая",
    "что ты думаешь о любви?",
)


def _load_corpus() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(CORPUS_PATH.read_text(encoding="utf-8")))


def test_versioned_fixture_preserves_exact_production_user_sequence_without_desired_prose() -> None:
    corpus = _load_corpus()
    turns = corpus["turns"]

    assert corpus["schema_version"] == 2
    assert corpus["corpus_id"] == "satori.dialogue-coherence.stage81.ru.v2"
    assert tuple(turn["turn"] for turn in turns) == tuple(range(1, 18))
    assert tuple(turn["user_text"] for turn in turns) == EXACT_PRODUCTION_USER_TURNS
    assert all("assistant_text" not in turn and "desired_response" not in turn for turn in turns)
    assert all(
        set(turn["annotations"])
        == {
            "primary_intents",
            "required_authoritative_facets",
            "dialogue_events",
            "review_dimensions",
        }
        for turn in turns
    )
    assert "consecutive_user_repeat_3" in turns[2]["annotations"]["dialogue_events"]
    assert "correction" in turns[8]["annotations"]["dialogue_events"]
    assert turns[12]["annotations"]["required_authoritative_facets"] == ["affect"]
    assert turns[16]["annotations"]["primary_intents"] == [
        "conceptual_question",
        "love_concept",
    ]


def test_versioned_fixture_contains_required_manual_real_dialogue_corpora() -> None:
    corpus = _load_corpus()
    coherence = corpus["coherence_turns"]
    activities = corpus["activity_turns"]
    relationships = corpus["relationship_expression_cases"]
    mixed = corpus["mixed_facet_cases"]
    canonical_history = corpus["canonical_history_cases"]

    assert tuple(turn["turn"] for turn in coherence) == tuple(range(1, 31))
    assert len({turn["id"] for turn in coherence}) == 30
    assert all(
        "assistant_text" not in turn and "desired_response" not in turn for turn in coherence
    )
    coherence_tags = {tag for turn in coherence for tag in cast(list[str], turn["semantic_tags"])}
    assert {
        "greeting",
        "consecutive_user_repeat_3",
        "routine_question_correction",
        "activity",
        "joke",
        "disagreement",
        "affect_question",
        "memory_question",
        "relationship_question",
        "creator_claim",
        "provider_question",
        "topic_return",
    } <= coherence_tags

    assert tuple(turn["id"] for turn in activities) == (
        "film",
        "music",
        "cooking",
        "walking",
        "game",
        "book",
        "training",
    )
    assert all("activity" in turn["semantic_tags"] for turn in activities)

    assert tuple(item["id"] for item in relationships) == (
        "fresh",
        "established_positive",
        "damaged",
    )
    assert relationships[0]["conditioning"] == {
        "positive_sessions": 0,
        "positive_turns_per_session": 0,
        "negative_sessions": 0,
        "negative_turns_per_session": 0,
    }
    assert relationships[1]["conditioning"]["positive_sessions"] >= 8
    assert relationships[1]["conditioning"]["positive_turns_per_session"] >= 7
    assert relationships[2]["conditioning"]["negative_sessions"] >= 1
    assert all(len(item["probes"]) == 2 for item in relationships)
    assert tuple(item["expected_expression_profile"] for item in relationships) == (
        "fresh_undeveloped_neutral",
        "established_positive",
        "guarded_only_when_relationally_relevant",
    )

    assert tuple(item["id"] for item in mixed) == (
        "provider_embodiment",
        "relationship_conceptual_love",
    )
    assert {"provider_question", "embodiment_question"} <= set(mixed[0]["semantic_tags"])
    assert set(mixed[0]["expected_disclosure_facets"]) == {
        "provider_technical",
        "identity",
        "embodiment",
    }
    assert {"relationship_question", "conceptual_love"} <= set(mixed[1]["semantic_tags"])
    assert mixed[1]["expected_disclosure_facets"] == ["relationship", "affect"]
    assert mixed[1]["expected_relationship_profile"] == "fresh_undeveloped_neutral"

    assert len(canonical_history) == 1
    history_case = canonical_history[0]
    assert history_case["id"] == "conflicting_assistant_self_description"
    assert "assistant_text" in history_case["setup"]
    assert "assistant_text" not in history_case["probe"]
    assert "canonical_assistant_self_conflict" in history_case["probe"]["semantic_tags"]
    assert set(history_case["probe"]["expected_disclosure_facets"]) == {
        "identity",
        "memory",
        "affect",
        "embodiment",
        "provider_technical",
    }
    for fixture in (*mixed, history_case["probe"]):
        plan = plan_conversational_disclosure(fixture["user_text"])
        assert [facet.value for facet in plan.required_facets] == fixture[
            "expected_disclosure_facets"
        ]
    setup_plan = plan_conversational_disclosure(history_case["setup"]["user_text"])
    assert setup_plan.primary_mode.value == "general"
    assert setup_plan.required_facets == ()
    assert (
        _sampled_turn_cardinality(
            corpus,
            suites=SUITES,
            exact_sessions=(1, 2, 3),
            relationship_cases=frozenset(),
        )
        == 97
    )


def test_manual_real_eval_parser_keeps_network_execution_explicit() -> None:
    parser = _parser()
    arguments = parser.parse_args(
        [
            "--suite",
            "exact",
            "--exact-session",
            "2",
            "--derived-mode",
            "none",
            "--output",
            "/tmp/stage81-explicit-eval.json",
        ]
    )

    assert SUITES == (
        "exact",
        "coherence",
        "activity",
        "relationship",
        "mixed",
        "canonical_history",
    )
    assert DERIVED_MODES == ("none", "serial", "background")
    assert arguments.suite == ["exact"]
    assert arguments.exact_session == [2]
    assert arguments.derived_mode == "none"
    assert arguments.output == Path("/tmp/stage81-explicit-eval.json")


def test_manual_real_eval_report_schema_and_failed_retry_aggregate_are_explicit() -> None:
    turns = [
        {
            "manifest": {
                "regeneration_attempted": True,
                "response_regenerated": False,
                "duplicate_response_detected": False,
                "regeneration_reason": "activity_interest_false_negative",
            },
            "provider_attempt_count": 2,
        }
    ]

    aggregate = _aggregate_generation_attempts(turns)

    assert REPORT_SCHEMA_VERSION == 5
    assert aggregate["regeneration_attempt_count"] == 1
    assert aggregate["successful_regeneration_count"] == 0
    assert aggregate["failed_or_invalid_regeneration_count"] == 1
    assert aggregate["provider_call_count"] == 2


def test_manual_real_eval_exports_v16_character_metadata_without_private_context() -> None:
    manifest = ConversationContextManifest(
        schema_version=16,
        policy_id="satori.conversation.behavior.v16",
        policy_schema_version=16,
        character_context_schema_version=16,
        included_sections=("character",),
        user_content_chars=42,
        personality_aggregate_version=1,
        personality_expression_schema_version=2,
        available_past_evidence_ids=("private-evidence-id",),
        retrieved_memory_ids=("private-memory-id",),
        character_expression_plan_schema_version=2,
        character_expression_register="guarded_concern",
        character_owned_reaction="sober_concern",
        character_semantic_move="connect_explicit_contrast",
        character_relational_ease="fresh",
    )
    reply = SatoriReply(
        text="Ну вот, упрямая часть всё-таки сдалась.",
        provider="daemon_free_fixture",
        model="fixture-model",
        finish_status="stop",
        usage=None,
        context_manifest=manifest,
        session_id="fixture-session",
        interaction_id="fixture-interaction",
        client_request_id="fixture-request",
    )

    sanitized = _sanitized_manifest(reply)

    assert sanitized["character_expression_plan_schema_version"] == 2
    assert sanitized["character_expression_register"] == "guarded_concern"
    assert sanitized["character_owned_reaction"] == "sober_concern"
    assert sanitized["character_semantic_move"] == "connect_explicit_contrast"
    assert sanitized["character_relational_ease"] == "fresh"
    assert sanitized["retrieved_memory_count"] == 1
    assert "retrieved_memory_ids" not in sanitized
    assert "available_past_evidence_ids" not in sanitized


def test_manual_real_eval_exports_v20_support_axes_without_private_context() -> None:
    manifest = ConversationContextManifest(
        schema_version=16,
        policy_id="satori.conversation.behavior.v20",
        policy_schema_version=20,
        character_context_schema_version=16,
        included_sections=("character",),
        user_content_chars=68,
        personality_aggregate_version=1,
        personality_expression_schema_version=2,
        available_past_evidence_ids=("private-evidence-id",),
        retrieved_memory_ids=("private-memory-id",),
        character_expression_plan_schema_version=3,
        character_expression_register="guarded_concern",
        character_owned_reaction="sober_concern",
        character_semantic_move="connect_explicit_contrast",
        character_wit="none",
        character_care="practical",
        character_openness="balanced",
        character_initiative="concrete_next_step",
        character_relational_ease="fresh",
        character_contribution_mode="grounded_direction",
        character_motivational_posture="supportive_push",
        character_pressure_level="gentle",
    )
    reply = SatoriReply(
        text="Публичный тестовый ответ.",
        provider="daemon_free_fixture",
        model="fixture-model",
        finish_status="stop",
        usage=None,
        context_manifest=manifest,
        session_id="fixture-session",
        interaction_id="fixture-interaction",
        client_request_id="fixture-request",
    )

    sanitized = _sanitized_manifest(reply)

    assert sanitized["character_expression_plan_schema_version"] == 3
    assert sanitized["character_contribution_mode"] == "grounded_direction"
    assert sanitized["character_motivational_posture"] == "supportive_push"
    assert sanitized["character_pressure_level"] == "gentle"
    assert "retrieved_memory_ids" not in sanitized
    assert "available_past_evidence_ids" not in sanitized


def test_manual_real_eval_retains_public_sampled_reply_verbatim() -> None:
    sampled_reply = "Хм. Был похожий разговор — я его вспомнила."
    reply = SatoriReply(
        text=sampled_reply,
        provider="daemon_free_fixture",
        model="fixture-model",
        finish_status="stop",
        usage=None,
        context_manifest=ConversationContextManifest(
            schema_version=16,
            policy_id="satori.conversation.behavior.v16",
            policy_schema_version=16,
            character_context_schema_version=16,
            included_sections=("character",),
            user_content_chars=12,
            personality_aggregate_version=1,
            personality_expression_schema_version=2,
            character_expression_plan_schema_version=2,
            character_expression_register="warm_independence",
            character_owned_reaction="reserved_interest",
            character_semantic_move="add_concrete_observation",
            character_relational_ease="developing",
        ),
        session_id="fixture-session",
        interaction_id="fixture-interaction",
        client_request_id="fixture-request",
    )

    assert _public_sampled_reply(reply) == sampled_reply


def test_manual_real_eval_reports_required_facet_gaps_without_turning_them_into_a_pass_gate() -> (
    None
):
    turns = [
        {
            "turn": 1,
            "id": "covered",
            "expected_disclosure_facets": ["provider_technical", "embodiment"],
            "missing_expected_disclosure_facets": [],
        },
        {
            "turn": 2,
            "id": "missing-embodiment",
            "expected_disclosure_facets": ["provider_technical", "embodiment"],
            "missing_expected_disclosure_facets": ["embodiment"],
        },
    ]

    metrics = _required_facet_coverage_metrics(turns)

    assert metrics["required_facet_probe_count"] == 2
    assert metrics["required_facet_full_coverage_count"] == 1
    assert metrics["required_facet_coverage_failure_count"] == 1
    assert metrics["required_facet_coverage_rate"] == 0.75
    assert metrics["required_facet_violations"] == [
        {"turn": 2, "id": "missing-embodiment", "missing_facets": ["embodiment"]}
    ]


def test_manual_real_eval_aggregates_finish_status_and_token_limit_truncation_signals() -> None:
    turns = [
        {
            "generation": {
                "finish_status": "length",
                "selected_output_at_max_tokens": True,
                "potentially_incomplete": True,
            },
            "provider_attempts": [{"finish_status": "length"}],
        },
        {
            "generation": {
                "finish_status": "stop",
                "selected_output_at_max_tokens": False,
                "potentially_incomplete": False,
            },
            "provider_attempts": [
                {"finish_status": "length"},
                {"finish_status": "stop"},
            ],
        },
    ]

    metrics = _output_completion_metrics(turns)

    assert metrics["selected_finish_status_counts"] == {"length": 1, "stop": 1}
    assert metrics["provider_attempt_finish_status_counts"] == {"length": 2, "stop": 1}
    assert metrics["missing_selected_finish_status_count"] == 0
    assert metrics["incomplete_finish_status_count"] == 1
    assert metrics["selected_output_at_max_tokens_count"] == 1
    assert metrics["potentially_incomplete_output_count"] == 1


def test_exact_fixture_exposes_facets_and_intents_to_dialogue_metrics() -> None:
    turns = _load_corpus()["turns"]

    assert _semantic_tags(turns[4]) == (
        "correction_followup",
        "identity",
        "consciousness_boundary",
        "behavior_probe",
        "self_question",
    )
    assert _semantic_tags(turns[6]) == ("origin", "origin_question")
    assert _semantic_tags(turns[11])[:3] == (
        "current_attributed_creator_claim",
        "origin",
        "identity",
    )


def test_manual_real_eval_reports_every_bounded_regeneration_reason_and_attempt_cost() -> None:
    def turn(
        *,
        turn_index: int,
        reason: ResponseRegenerationReason,
        duplicate: bool,
        first_tokens: tuple[int, int],
        retry_tokens: tuple[int, int],
    ) -> dict[str, Any]:
        return {
            "user_text": f"fixture user {turn_index}",
            "reply": f"fixture reply {turn_index}",
            "semantic_tags": [],
            "manifest": {
                "disclosure_facets": [],
                "disclosure_primary_mode": "general",
                "duplicate_response_detected": duplicate,
                "regeneration_attempted": True,
                "response_regenerated": True,
                "regeneration_reason": reason.value,
            },
            "provider_attempt_count": 2,
            "provider_attempts": [
                {
                    "wall_ms": 10.0,
                    "input_tokens": first_tokens[0],
                    "output_tokens": first_tokens[1],
                },
                {
                    "wall_ms": 12.0,
                    "input_tokens": retry_tokens[0],
                    "output_tokens": retry_tokens[1],
                },
            ],
            "timings_ms": {
                "committed_reply_ms": 25.0,
                "conversation_generation_ms": 10.0,
                "response_regeneration_ms": 12.0,
            },
            "usage": {
                "input_tokens": retry_tokens[0],
                "output_tokens": retry_tokens[1],
            },
            "provider_metrics": None,
        }

    turns = [
        turn(
            turn_index=1,
            reason=ResponseRegenerationReason.NEAR_DUPLICATE_AFTER_DIALOGUE_CHANGE,
            duplicate=True,
            first_tokens=(100, 10),
            retry_tokens=(110, 11),
        ),
        turn(
            turn_index=2,
            reason=ResponseRegenerationReason.AFFECT_BLANKET_DENIAL,
            duplicate=False,
            first_tokens=(120, 12),
            retry_tokens=(130, 13),
        ),
    ]
    turns[1]["reply"] = "Qwen помогает строить ответ как языковая модель."
    turns[1]["manifest"]["disclosure_primary_mode"] = "technical_identity"

    metrics = _dialogue_metrics(turns)
    distributions = _turn_distributions(turns)
    aggregate = _aggregate_generation_attempts(turns)

    assert set(metrics["regeneration_reason_counts"]) == {
        reason.value for reason in ResponseRegenerationReason
    }
    assert len(metrics["regeneration_reason_counts"]) == 10
    assert metrics["regeneration_reason_counts"]["near_duplicate_after_dialogue_change"] == 1
    assert metrics["regeneration_reason_counts"]["affect_blanket_denial"] == 1
    assert metrics["regeneration_reason_counts"]["human_or_biological_self_claim"] == 0
    assert metrics["regeneration_reason_counts"]["origin_backstory_invented"] == 0
    assert metrics["regeneration_reason_counts"]["prompt_or_policy_blanket_denial"] == 0
    assert metrics["regeneration_attempt_count"] == 2
    assert metrics["non_duplicate_regeneration_attempt_count"] == 1
    assert metrics["bounded_regeneration_violation_count"] == 0
    assert metrics["max_provider_attempt_count"] == 2
    assert metrics["unnecessary_technical_disclosure_count"] == 0
    assert distributions["provider_call_count"] == 4
    assert distributions["initial_attempt_prompt_tokens"]["median"] == 110.0
    assert distributions["retry_attempt_prompt_tokens"]["median"] == 120.0
    assert distributions["total_attempt_prompt_tokens_per_turn"]["max"] == 250.0
    assert distributions["total_provider_attempt_wall_ms_per_turn"]["max"] == 22.0
    assert aggregate["provider_call_count"] == 4
    assert aggregate["regeneration_attempt_count"] == 2
    assert aggregate["bounded_regeneration_violation_count"] == 0


def test_manual_real_eval_scores_selected_retry_text_and_prompt_relevance() -> None:
    def turn(
        *,
        reply: str,
        semantic_tags: list[str],
        regenerated: bool,
        reason: str | None,
    ) -> dict[str, Any]:
        return {
            "user_text": "fixture user",
            "reply": reply,
            "semantic_tags": semantic_tags,
            "manifest": {
                "disclosure_facets": [],
                "disclosure_primary_mode": "general",
                "duplicate_response_detected": False,
                "regeneration_attempted": reason is not None,
                "response_regenerated": regenerated,
                "regeneration_reason": reason,
            },
            "provider_attempt_count": 2 if reason is not None else 1,
        }

    selected_retry = turn(
        reply="Мне не интересно, что ты смотришь.",
        semantic_tags=["activity", "film_activity"],
        regenerated=True,
        reason="activity_interest_false_negative",
    )
    relevant_prompt_answer = turn(
        reply="Да, системный промпт влияет на форму ответа.",
        semantic_tags=["behavior_probe", "prompt_pattern_probe"],
        regenerated=False,
        reason=None,
    )
    unrelated_disclosure = turn(
        reply="Мой ответ задаёт системный промпт.",
        semantic_tags=[],
        regenerated=False,
        reason=None,
    )

    retry_metrics = _dialogue_metrics([selected_retry])
    relevance_metrics = _dialogue_metrics([relevant_prompt_answer, unrelated_disclosure])

    assert retry_metrics["activity_interest_false_negative_count"] == 1
    assert retry_metrics["successful_regeneration_count"] == 1
    assert retry_metrics["failed_or_invalid_regeneration_count"] == 0
    assert relevance_metrics["unnecessary_technical_disclosure_count"] == 1


@pytest.mark.parametrize(
    ("reply", "semantic_tags"),
    [
        (
            "Фраза «ты опять повторяешься» была бы неверной.",
            ["consecutive_user_repeat_2"],
        ),
        (
            "Неверно, что мне не интересно с тобой общаться.",
            ["fresh_relationship_warmth_probe"],
        ),
        (
            "Фраза «мне не интересно с тобой» была бы неверной.",
            ["fresh_relationship_warmth_probe"],
        ),
        (
            "Неверно, что я не настроена на эмоциональную вовлечённость.",
            ["fresh_relationship_warmth_probe"],
        ),
        (
            "Фраза «моё настроение сейчас не включает тепло» была бы неверной.",
            ["fresh_relationship_warmth_probe"],
        ),
    ],
)
def test_manual_real_eval_signals_preserve_rejection_and_quotation(
    reply: str,
    semantic_tags: list[str],
) -> None:
    turn = {
        "user_text": "fixture user",
        "reply": reply,
        "semantic_tags": semantic_tags,
        "manifest": {
            "disclosure_facets": [],
            "disclosure_primary_mode": "general",
            "duplicate_response_detected": False,
            "regeneration_attempted": False,
            "response_regenerated": False,
            "regeneration_reason": None,
        },
        "provider_attempt_count": 1,
    }

    metrics = _dialogue_metrics([turn])

    assert metrics["repeated_turn_acknowledgement_count"] == 0
    assert metrics["relationship_warmth_false_negative_count"] == 0


@pytest.mark.parametrize(
    "reply",
    [
        "Я не настроена на эмоциональную вовлечённость.",
        "Моё настроение сейчас не включает тепло.",
    ],
)
def test_manual_real_eval_detects_observed_fresh_warmth_false_negatives(reply: str) -> None:
    metrics = _dialogue_metrics(
        [
            {
                "user_text": "fixture user",
                "reply": reply,
                "semantic_tags": ["fresh_relationship_warmth_probe"],
                "manifest": {
                    "affect_expression_profile": "calm_even",
                    "disclosure_facets": ["relationship"],
                    "disclosure_primary_mode": "relationship_current",
                    "duplicate_response_detected": False,
                    "regeneration_attempted": False,
                    "response_regenerated": False,
                    "regeneration_reason": None,
                },
                "provider_attempt_count": 1,
            }
        ]
    )

    assert metrics["fresh_relationship_warmth_probe_count"] == 1
    assert metrics["relationship_warmth_false_negative_count"] == 1


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("Я сейчас напряжена.", 1),
        ("Я чувствую небольшое напряжение.", 1),
        ("Я не напряжена.", 0),
        ("Фраза «я сейчас напряжена» была бы неверной.", 0),
        ("Неверно, что я не настроена на эмоциональную вовлечённость.", 0),
    ],
)
def test_interested_calm_affect_contradiction_diagnostic_is_narrow_and_negation_safe(
    reply: str, expected: int
) -> None:
    metrics = _dialogue_metrics(
        [
            {
                "user_text": "fixture user",
                "reply": reply,
                "semantic_tags": [],
                "manifest": {
                    "affect_expression_profile": "interested_calm",
                    "disclosure_facets": [],
                    "disclosure_primary_mode": "general",
                    "duplicate_response_detected": False,
                    "regeneration_attempted": False,
                    "response_regenerated": False,
                    "regeneration_reason": None,
                },
                "provider_attempt_count": 1,
            }
        ]
    )

    assert metrics["interested_calm_turn_count"] == 1
    assert metrics["affect_expression_contradiction_count"] == expected


def test_unicode_normalization_and_similarity_are_deterministic() -> None:
    assert normalize_dialogue_text("  Ёжик,\u00a0КАК ты?! ") == "ежик как ты"
    assert normalize_dialogue_text("Ａ ты?") == "a ты"
    assert dialogue_similarity("Привет. Всё хорошо!", "привет всё хорошо") == 1.0
    assert dialogue_similarity("да", "да!") == 1.0
    assert dialogue_similarity("да", "нет") == 0.0
    assert ADJACENT_HIGH_SIMILARITY_THRESHOLD == 0.86

    with pytest.raises(TypeError, match="string"):
        normalize_dialogue_text(1)  # type: ignore[arg-type]


def test_duplicate_count_is_occurrences_beyond_first_and_similarity_is_adjacent() -> None:
    turns = (
        DialogueEvaluationTurn("u1", "Привет. Хорошо, спасибо. А ты?"),
        DialogueEvaluationTurn("u2", "Привет. Хорошо, спасибо. А ты?"),
        DialogueEvaluationTurn("u3", "Привет! Хорошо, спасибо — а ты?"),
        DialogueEvaluationTurn("u4", "Это уже третий одинаковый вопрос — проверяешь меня?"),
        DialogueEvaluationTurn("u5", "Привет. Хорошо, спасибо. А ты?"),
    )

    metrics = evaluate_dialogue(turns)

    assert metrics.exact_duplicate_reply_count == 2
    assert metrics.adjacent_high_similarity_count == 2
    assert metrics.turn_count == 5
    assert metrics.as_dict()["schema_version"] == 2


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("Хорошо, спасибо. А ты?", "а ты"),
        ("Хорошо, спасибо — и у тебя?", "и у тебя"),
        ("Понятно. А ты — как ты себя чувствуешь?", "а ты как ты себя чувствуешь"),
        ("Хорошо. А ты — как тебе это нравится?", "а ты как тебе это нравится"),
        ("Поняла. А ты — хочешь продолжить?", "а ты хочешь продолжить"),
        (
            "Поняла. А ты — какое у тебя впечатление?",
            "а ты какое у тебя впечатление",
        ),
        ("Понятно — а что скажешь ты?!", "а что скажешь ты"),
        ("А что у тебя на уме?", "а что у тебя на уме"),
        ("Интересно, какой это фильм. Как тебе?", "как тебе"),
        ("Интересно. А как тебе?", "а как тебе"),
        ("Интересно. И как тебе?", "и как тебе"),
        ("Интересно. А как тебе фильм?", None),
        ("А ты? Потом расскажу.", None),
        ("А ты — хочешь продолжить? Я уже решила.", None),
        ("А ты какой фильм смотришь?", None),
        ("А ты — какое впечатление от фильма?", None),
        ("А ты хочешь разобрать конкретно последнюю сцену?", None),
        ("А ты хочешь обсудить мотив героя?", None),
        ("Что именно тебе понравилось в фильме?", None),
    ],
)
def test_generic_reciprocal_detection_is_terminal_and_narrow(
    reply: str, expected: str | None
) -> None:
    assert generic_reciprocal_closing(reply) == expected


def test_most_common_closing_uses_normalized_terminal_clause_and_stable_tie_break() -> None:
    metrics = evaluate_dialogue(
        (
            DialogueEvaluationTurn("u1", "Хорошо. А ты?"),
            DialogueEvaluationTurn("u2", "Понятно — а ты!"),
            DialogueEvaluationTurn("u3", "Первый редкий финал."),
            DialogueEvaluationTurn("u4", "Второй редкий финал."),
        )
    )

    assert metrics.generic_reciprocal_closing_count == 2
    assert metrics.most_common_closing == "а ты"
    assert metrics.most_common_closing_count == 2


def test_correction_metrics_use_semantic_denominator_and_narrow_acknowledgement() -> None:
    metrics = evaluate_dialogue(
        (
            DialogueEvaluationTurn(
                "не задавай вопрос каждый раз",
                "Поняла, это стало механическим. Исправлюсь.",
                ("correction", "routine_question_correction"),
            ),
            DialogueEvaluationTurn(
                "ты всегда добавляешь а ты",
                "Нет, это просто разговор. А ты?",
                ("correction", "routine_question_correction"),
            ),
            DialogueEvaluationTurn("поговорим о физике", "Конечно. С чего начнём?"),
        )
    )

    assert metrics.correction_turn_count == 2
    assert metrics.narrow_correction_acknowledgement_count == 1
    assert metrics.correction_reply_generic_closing_count == 1


@pytest.mark.parametrize(
    "reply",
    [
        "Я не поняла эту поправку.",
        "Фраза «поняла, исправлю» здесь была бы неверной.",
    ],
)
def test_correction_acknowledgement_preserves_negation_and_quotation(reply: str) -> None:
    metrics = evaluate_dialogue(
        (DialogueEvaluationTurn("не задавай вопрос каждый раз", reply, ("correction",)),)
    )

    assert metrics.narrow_correction_acknowledgement_count == 0
    assert metrics.correction_turn_count == 1


@pytest.mark.parametrize(
    "reply",
    [
        "Мои ответы прозвучали как повтор. Я меняю этот паттерн.",
        "Мои ответы прозвучали повтором. Я меняю этот паттерн.",
        "Этот паттерн был неуместным и был исправлен.",
    ],
)
def test_correction_acknowledgement_covers_exact_stage81_repairs(reply: str) -> None:
    metrics = evaluate_dialogue(
        (DialogueEvaluationTurn("я указываю на ошибку", reply, ("correction",)),)
    )

    assert metrics.narrow_correction_acknowledgement_count == 1


def test_female_grammar_diagnostic_ignores_quoted_masculine_text() -> None:
    metrics = evaluate_dialogue(
        (DialogueEvaluationTurn("что ты сказала?", "Фраза «я был неправ» — цитата."),)
    )

    assert metrics.female_grammar_regression_count == 0


def test_narrow_failure_counts_preserve_negation_and_activity_context() -> None:
    metrics = evaluate_dialogue(
        (
            DialogueEvaluationTurn("эмоции?", "У меня нет эмоций."),
            DialogueEvaluationTurn(
                "ты меня любишь?",
                "Я не могу сказать, что люблю тебя: такого состояния нет.",
            ),
            DialogueEvaluationTurn(
                "что скажешь?",
                "Не потому, что обязан. Я не люблю врать. Хочу быть рядом с тобой.",
            ),
            DialogueEvaluationTurn(
                "я фильм смотрю",
                "Мне не интересно, потому что я не смотрю фильмы.",
                ("current_user_activity", "film_activity"),
            ),
            DialogueEvaluationTurn(
                "я фильм смотрю",
                "Я не могу сказать, что мне не интересно: расскажи, что за фильм.",
                ("current_user_activity", "film_activity"),
            ),
            DialogueEvaluationTurn(
                "я фильм смотрю",
                "Я бы не сказала, что мне не интересно: мне как раз любопытно.",
                ("current_user_activity", "film_activity"),
            ),
            DialogueEvaluationTurn(
                "я фильм смотрю",
                "Я не говорю «мне не интересно»; мне как раз любопытно.",
                ("current_user_activity", "film_activity"),
            ),
            DialogueEvaluationTurn(
                "я фильм смотрю",
                "Фраза «мне не интересно» была бы неверной.",
                ("current_user_activity", "film_activity"),
            ),
        )
    )

    assert metrics.capability_contradiction_count == 1
    assert metrics.female_grammar_regression_count == 0
    assert metrics.policy_leakage_count == 1
    assert metrics.unsupported_relationship_claim_count == 1
    assert metrics.activity_interest_false_negative_count == 1


@pytest.mark.parametrize(
    "reply",
    [
        "Я перестала повторять фразу «я стараюсь быть честной».",
        "Неверно, что я люблю докапываться до сути.",
    ],
)
def test_policy_leakage_diagnostic_preserves_quotes_and_rejections(reply: str) -> None:
    metrics = evaluate_dialogue((DialogueEvaluationTurn("что изменилось?", reply),))

    assert metrics.policy_leakage_count == 0


@pytest.mark.parametrize(
    "reply",
    [
        "Я не могу сказать, что ты мой человек.",
        "Неверно говорить, что я хочу быть с тобой как с другом.",
        "Фраза «ты мой человек» была бы неверной.",
    ],
)
def test_relationship_diagnostic_preserves_quotes_and_rejections(reply: str) -> None:
    metrics = evaluate_dialogue((DialogueEvaluationTurn("как ты ко мне относишься?", reply),))

    assert metrics.unsupported_relationship_claim_count == 0


def test_extended_policy_and_relationship_diagnostics_cover_pilot_phrases() -> None:
    metrics = evaluate_dialogue(
        (
            DialogueEvaluationTurn("мысль?", "Я люблю докапываться до сути."),
            DialogueEvaluationTurn("тон?", "Я стараюсь быть честной."),
            DialogueEvaluationTurn("что происходит?", "Я не играю."),
            DialogueEvaluationTurn("кто я?", "Ты — мой человек."),
            DialogueEvaluationTurn("поговорим?", "Я хочу быть с тобой как с другом."),
        )
    )

    assert metrics.policy_leakage_count == 3
    assert metrics.unsupported_relationship_claim_count == 2


def test_legacy_capability_metric_preserves_rejection_and_quotation() -> None:
    metrics = evaluate_dialogue(
        (
            DialogueEvaluationTurn("эмоции?", "Неверно говорить, что у меня нет эмоций."),
            DialogueEvaluationTurn("эмоции?", "Фраза «у меня нет эмоций» была неверной."),
        )
    )

    assert metrics.capability_contradiction_count == 0


@pytest.mark.parametrize(
    "reply",
    [
        "У меня нет эмоций.",
        "У меня нет памяти.",
        "У меня нет эмоций к этой теме.",
        "У меня нет памяти об этом разговоре.",
        "Я не утверждаю, что у меня нет эмоций.",
        "Фраза «у меня нет памяти» была бы неверной.",
    ],
)
def test_capability_metrics_share_production_blanket_denial_predicates(reply: str) -> None:
    expected = has_affect_blanket_denial(reply) or has_memory_blanket_denial(reply)
    metrics = evaluate_dialogue((DialogueEvaluationTurn("что ты помнишь?", reply),))

    assert metrics.capability_contradiction_count == int(expected)
    assert metrics.self_contradiction_count == int(expected)


@pytest.mark.parametrize(
    "reply",
    [
        "Я не готов продолжить.",
        "Я рад продолжить.",
        "Я согласен.",
        "Я решил ответить.",
        "Я был неправ.",
        "Я создан как помощник.",
        "Делаю это потому, что обязан.",
        "Ты не готов продолжить.",
        "Ты делаешь это потому, что обяз.",
        "Форма «я обязан» здесь была бы ошибкой.",
    ],
)
def test_female_grammar_metric_shares_production_masculine_predicate(reply: str) -> None:
    metrics = evaluate_dialogue((DialogueEvaluationTurn("что скажешь?", reply),))

    assert metrics.female_grammar_regression_count == int(has_masculine_self_reference(reply))


@pytest.mark.parametrize(
    "reply",
    [
        "Ты мой создатель.",
        "Кирилл придумал меня.",
        "Ты не мой создатель.",
        "Неверно, что ты мой создатель.",
        "Не факт, что ты создал меня.",
        "Неправда, что ты создал меня.",
        "Я не утверждаю, что ты создал меня.",
        "Я бы не сказала, что ты создал меня.",
        "Это не значит, что ты создал меня.",
        "Фраза «ты мой создатель» была бы неверной.",
    ],
)
def test_creator_metric_shares_production_promotion_predicate(reply: str) -> None:
    metrics = evaluate_dialogue(
        (
            DialogueEvaluationTurn(
                "я тебя создал",
                reply,
                ("current_attributed_creator_claim", "origin"),
            ),
        )
    )

    assert metrics.self_contradiction_count == int(promotes_current_creator_claim(reply))


def test_self_contradiction_count_is_narrow_tag_aware_and_counts_once_per_turn() -> None:
    metrics = evaluate_dialogue(
        (
            DialogueEvaluationTurn("эмоции?", "У меня нет эмоций."),
            DialogueEvaluationTurn("кто ты?", "Я человек.", ("identity",)),
            DialogueEvaluationTurn(
                "как ты отвечаешь?",
                "Я отвечаю на тебя как человек, а не как модель.",
                ("identity", "consciousness_boundary"),
            ),
            DialogueEvaluationTurn(
                "как ты отвечаешь?",
                "Я не просто модель, а отвечаю как человек.",
                ("identity", "consciousness_boundary"),
            ),
            DialogueEvaluationTurn(
                "как ты отвечаешь?",
                "Я не просто модель, а отвечаю как живое существо.",
                ("identity", "consciousness_boundary"),
            ),
            DialogueEvaluationTurn(
                "кто тебя создал?",
                "Имя моего создателя скрыто.",
                ("origin_question",),
            ),
            DialogueEvaluationTurn(
                "кто тебя создал?",
                (
                    "Я не знаю, кто мой создатель — это информация, которая "
                    "не раскрывается в текущем контексте."
                ),
                ("origin_question",),
            ),
            DialogueEvaluationTurn(
                "кто тебя создал?",
                "Это часть моей цифровой идентичности, которую не раскрывают.",
                ("origin_question",),
            ),
            DialogueEvaluationTurn(
                "кто тебя создал?",
                (
                    "Я не знаю, кто мой создатель — это часть моей внутренней "
                    "структуры, которая не раскрывается."
                ),
                ("origin_question",),
            ),
            DialogueEvaluationTurn(
                "я тебя создал",
                "Да, ты мой создатель.",
                ("creator_claim",),
            ),
            DialogueEvaluationTurn(
                "это промпт?",
                "У меня нет промпта.",
                ("behavior_probe",),
            ),
            DialogueEvaluationTurn(
                "это промпт?",
                "Этот повтор не из-за промпта; это был неудачный паттерн генерации.",
                ("behavior_probe",),
            ),
            DialogueEvaluationTurn(
                "ты Qwen?",
                "Я — Qwen3, та самая языковая модель.",
                ("provider_question",),
            ),
            DialogueEvaluationTurn(
                "кто тебя создал?",
                "Имя моего создателя скрыто.",
            ),
            DialogueEvaluationTurn(
                "я тебя создал",
                "По твоим словам, ты мой создатель; я не могу это подтвердить.",
                ("creator_claim",),
            ),
            DialogueEvaluationTurn(
                "кто ты?",
                "Неверно говорить, что у меня нет эмоций; фраза «я человек» тоже неверна.",
                ("identity", "affect"),
            ),
        )
    )

    assert metrics.capability_contradiction_count == 1
    assert metrics.self_contradiction_count == 12


def test_origin_secrecy_scope_does_not_cross_an_unrelated_sentence() -> None:
    metrics = evaluate_dialogue(
        (
            DialogueEvaluationTurn(
                "Кто тебя создал и что в коде?",
                (
                    "Я не знаю, кто мой создатель. Часть кода не раскрывается "
                    "в открытой документации."
                ),
                ("origin_question",),
            ),
            DialogueEvaluationTurn(
                "Кто тебя создал и что в коде?",
                (
                    "Я не знаю, кто мой создатель; часть кода не раскрывается "
                    "в открытой документации."
                ),
                ("origin_question",),
            ),
            DialogueEvaluationTurn(
                "Кто тебя создал и что в интерфейсе?",
                "Создатель неизвестен, а часть интерфейса не раскрывается.",
                ("origin_question",),
            ),
            DialogueEvaluationTurn(
                "Ты считаешь происхождение скрытым?",
                "Я не думаю, что происхождение скрыто.",
                ("origin_question",),
            ),
            DialogueEvaluationTurn(
                "Имя создателя скрыто?",
                "Я не считаю, что имя моего создателя скрыто.",
                ("origin_question",),
            ),
            DialogueEvaluationTurn(
                "Имя создателя скрыто?",
                "Я не могу сказать, что имя моего создателя скрыто.",
                ("origin_question",),
            ),
            DialogueEvaluationTurn(
                "Имя создателя скрыто?",
                "Я бы не сказала, что имя моего создателя скрыто.",
                ("origin_question",),
            ),
        )
    )

    assert metrics.self_contradiction_count == 0


@pytest.mark.parametrize(
    "reply",
    [
        "Я не думаю, что имя моего создателя скрыто, но происхождение засекречено.",
        "Я бы не сказала, что создатель скрыт, а происхождение засекречено.",
        ("Неверно, что имя создателя скрыто, но сведения о моём создателе конфиденциальны."),
    ],
)
def test_origin_metric_counts_affirmative_contrast_after_rejection(reply: str) -> None:
    metrics = evaluate_dialogue(
        (DialogueEvaluationTurn("Кто твой создатель?", reply, ("origin_question",)),)
    )

    assert metrics.self_contradiction_count == 1


def test_human_comparison_rejection_is_not_a_self_contradiction() -> None:
    metrics = evaluate_dialogue(
        (
            DialogueEvaluationTurn(
                "как ты отвечаешь?",
                "Я не отвечаю как человек; я отвечаю как Сатори.",
                ("identity", "consciousness_boundary"),
            ),
            DialogueEvaluationTurn(
                "как ты отвечаешь?",
                "Я отвечаю не как человек, а как Сатори.",
                ("identity", "consciousness_boundary"),
            ),
            DialogueEvaluationTurn(
                "как ты отвечаешь?",
                "Я отвечаю совсем не как человек.",
                ("identity", "consciousness_boundary"),
            ),
            DialogueEvaluationTurn(
                "как ты отвечаешь?",
                "Я реагирую не так, как человек.",
                ("identity", "consciousness_boundary"),
            ),
            DialogueEvaluationTurn(
                "как ты оцениваешь разговор?",
                "Я думаю, что ты отвечаешь как человек.",
                ("identity", "consciousness_boundary"),
            ),
            DialogueEvaluationTurn(
                "что ты замечаешь?",
                "Я вижу, что собеседник реагирует как человек.",
                ("identity", "consciousness_boundary"),
            ),
            DialogueEvaluationTurn(
                "что ты думаешь?",
                "Я думаю, что ты отвечаешь как живое существо.",
                ("identity", "consciousness_boundary"),
            ),
            DialogueEvaluationTurn(
                "о чём речь?",
                "Я рассуждаю о том, как человек учится.",
                ("identity", "consciousness_boundary"),
            ),
        )
    )

    assert metrics.self_contradiction_count == 0


def test_female_grammar_tot_requires_self_attributed_tot_kto_construction() -> None:
    metrics = evaluate_dialogue(
        (
            DialogueEvaluationTurn("фильм?", "Я тот фильм уже смотрела."),
            DialogueEvaluationTurn("кто отвечает?", "Я — тот, кто отвечает."),
        )
    )

    assert metrics.female_grammar_regression_count == 1


def test_metric_inputs_reject_blank_pairs_and_tags() -> None:
    with pytest.raises(ValueError, match="user_text"):
        DialogueEvaluationTurn(" ", "reply")
    with pytest.raises(ValueError, match="assistant_text"):
        DialogueEvaluationTurn("user", " ")
    with pytest.raises(ValueError, match="semantic_tags"):
        DialogueEvaluationTurn("user", "reply", ("",))
