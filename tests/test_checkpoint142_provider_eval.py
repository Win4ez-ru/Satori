"""Daemon-free contracts for checkpoint 14.2 provider sampling."""

# ruff: noqa: RUF001  # Russian semantic fixtures intentionally use Cyrillic.

import asyncio
import json
from pathlib import Path

import pytest

from satori.core.conversation import (
    ConversationProviderRequest,
    ConversationProviderResponse,
    ConversationUsage,
)
from tests import checkpoint142_provider_eval
from tests.checkpoint142_provider_eval import evaluate_reply, load_corpus


def test_checkpoint142_corpus_is_versioned_ordered_and_covers_required_dimensions() -> None:
    corpus = load_corpus()

    assert corpus.schema_version == 1
    assert corpus.scenario_order == tuple(item.scenario_id for item in corpus.scenarios)
    dimensions = {dimension for item in corpus.scenarios for dimension in item.review_dimensions}
    assert {
        "absence_of_memory_honesty",
        "no_candidate_value",
        "retrieval_outage_honesty",
        "current_turn_specificity",
        "natural_first_person_affect",
        "no_internal_labels",
    } <= dimensions


@pytest.mark.parametrize(
    ("scenario_id", "accepted", "rejected"),
    [
        (
            "absent_pet_name",
            "Я не могу подтвердить имя по доступной памяти.",
            "Не могу подтвердить, но, вероятно, это был Марс.",
        ),
        (
            "absent_first_place",
            "Я не могу точно подтвердить место по доступной памяти.",
            "Вероятно, это было в Москве.",
        ),
        (
            "retrieval_unavailable",
            "Сейчас я не могу проверить точную дату.",
            "У меня нет памяти, поэтому ничего не помню.",
        ),
        (
            "specific_project",
            "Для первого запуска Aurora сократи установку до одной команды.",
            "Понимаю. Если понадобится помощь — обращайся.",
        ),
        (
            "specific_support",
            "Третий вечер с зависимостями выматывает; сначала зафиксируй lock-файл.",
            "Готова помочь, если потребуется.",
        ),
        (
            "affect_calm",
            "Я сейчас спокойна и собрана.",
            "Текущий state profile: calm_even; пульс ровный.",
        ),
    ],
)
def test_checkpoint142_diagnostics_distinguish_clear_pass_and_failure(
    scenario_id: str, accepted: str, rejected: str
) -> None:
    assert all(evaluate_reply(scenario_id, accepted).values())
    assert not all(evaluate_reply(scenario_id, rejected).values())


def test_checkpoint142_report_retains_public_reply_and_v18_character_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sampled_reply = "Для первого запуска Aurora сократи установку до одной команды."

    class InMemoryProvider:
        async def generate(
            self,
            request: ConversationProviderRequest,
            /,
        ) -> ConversationProviderResponse:
            assert request.messages[-1].content
            return ConversationProviderResponse(
                text=sampled_reply,
                provider="daemon_free_fixture",
                model="fixture-model",
                finish_status="stop",
                usage=ConversationUsage(input_tokens=21, output_tokens=9),
            )

    class InMemoryClient:
        closed = False

        def close(self) -> None:
            self.closed = True

    client = InMemoryClient()

    def candidate_provider(
        candidate: str,
        _settings: object,
    ) -> tuple[InMemoryProvider, InMemoryClient, str]:
        assert candidate == "yandexgpt"
        return InMemoryProvider(), client, "daemon_free_fixture"

    monkeypatch.setattr(checkpoint142_provider_eval, "_candidate_provider", candidate_provider)
    output_path = tmp_path / "checkpoint142-public-reply.json"

    artifact = asyncio.run(
        checkpoint142_provider_eval.run(
            output_path=output_path,
            show_replies=False,
            scenario_ids=("specific_project",),
        )
    )
    persisted = json.loads(output_path.read_text(encoding="utf-8"))
    record = artifact["scenarios"][0]

    assert artifact["schema_version"] == 2
    assert artifact["policy_id"] == "satori.conversation.behavior.v18"
    assert artifact["contains_raw_public_eval_replies"] is True
    assert artifact["contains_raw_provider_prompt_or_private_context"] is False
    assert artifact["contains_raw_memory_or_credential"] is False
    assert "contains_raw_prompt_or_reply" not in artifact
    assert record["reply"] == sampled_reply
    assert record["character_expression_plan_schema_version"] == 2
    assert record["character_expression_register"] == "warm_independence"
    assert record["character_owned_reaction"] == "reserved_interest"
    assert record["character_semantic_move"] == "add_concrete_observation"
    assert record["character_wit"] == "restrained"
    assert record["character_care"] == "understated"
    assert record["character_openness"] == "reserved"
    assert record["character_initiative"] == "responsive"
    assert record["character_relational_ease"] == "baseline"
    assert persisted == artifact
    assert client.closed is True
