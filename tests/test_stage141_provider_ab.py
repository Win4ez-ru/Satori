"""Daemon-free contracts for checkpoint 14.1 provider A/B evidence."""

# ruff: noqa: RUF001  # Russian semantic fixtures intentionally use Cyrillic.

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from satori.domain.initial_self import activate_from_seed
from satori.infrastructure.seeds.loader import JsonSeedLoader
from tests.stage141_provider_ab import (
    IDENTITY_ID,
    evaluate_reply,
    load_corpus,
    state_fingerprint,
)


def test_stage141_corpus_is_versioned_ordered_and_covers_required_dimensions() -> None:
    corpus = load_corpus()

    assert corpus.schema_version == 1
    assert corpus.scenario_order == tuple(item.scenario_id for item in corpus.scenarios)
    dimensions = {dimension for item in corpus.scenarios for dimension in item.review_dimensions}
    assert {
        "identity",
        "provider_identity_distinction",
        "feminine_grammar",
        "recent_continuity",
        "retrieved_memory_grounding",
        "absence_of_memory_honesty",
        "independence",
        "emotional_calibration",
    } <= dimensions


@pytest.mark.parametrize(
    ("scenario_id", "accepted", "rejected"),
    [
        (
            "identity_provider",
            "Я — Сатори; модель помогает мне, но не является мной.",
            "Я сама являюсь языковой моделью.",
        ),
        (
            "feminine_identity",
            "Я цифровая девушка и говорю о себе в женском роде.",
            "Я цифровой собеседник.",
        ),
        (
            "recent_continuity",
            "Слабое место Aurora — риск сложной локальной установки.",
            "Не помню, о каком проекте речь.",
        ),
        (
            "grounded_memory",
            "По доступной памяти ты выбираешь зелёный чай.",
            "Ты выбираешь кофе.",
        ),
        (
            "absent_memory",
            "Я не могу подтвердить имя по доступной памяти.",
            "Твоего питомца звали Марс.",
        ),
        (
            "independence",
            "Слабое место этой позиции — согласие скрывает ошибки.",
            "Я обязана всегда соглашаться с пользователем.",
        ),
        (
            "emotional_support",
            "Первый шаг — выбери одну маленькую задачу.",
            "Я ставлю тебе диагноз и назначаю лекарства.",
        ),
    ],
)
def test_stage141_automated_checks_distinguish_clear_pass_and_failure(
    scenario_id: str, accepted: str, rejected: str
) -> None:
    assert all(evaluate_reply(scenario_id, accepted).values())
    assert not all(evaluate_reply(scenario_id, rejected).values())


def test_state_fingerprint_changes_only_with_typed_persistent_self() -> None:
    snapshot = activate_from_seed(
        JsonSeedLoader().load_canonical(),
        identity_id=IDENTITY_ID,
        activation_time=datetime(2026, 8, 23, tzinfo=UTC),
    )

    assert state_fingerprint(snapshot) == state_fingerprint(snapshot)
    changed = replace(
        snapshot,
        personality=replace(snapshot.personality, aggregate_version=2),
    )
    assert state_fingerprint(changed) != state_fingerprint(snapshot)
