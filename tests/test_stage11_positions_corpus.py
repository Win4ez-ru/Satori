"""Versioned deterministic Stage 11 anti-mirroring and evidence corpus."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from satori.core.positions import (
    PositionEvidenceCitation,
    PositionEvidenceRole,
    PositionKind,
    PositionProposal,
    PositionSourceMessage,
    PositionStance,
)
from satori.domain.positions import PositionManager
from tests.fakes import SequenceIdGenerator

CORPUS_PATH = Path(__file__).parent / "fixtures" / "stage11_positions_v1.json"
CORPUS = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize("scenario", CORPUS["scenarios"], ids=lambda item: item["id"])
def test_stage11_position_policy_corpus(scenario: dict[str, object]) -> None:
    assert CORPUS["schema_version"] == 1
    raw_messages = scenario["messages"]
    assert isinstance(raw_messages, list)
    now = datetime(2026, 8, 22, tzinfo=UTC)
    sources = tuple(
        PositionSourceMessage(
            message_id=f"message-{index}",
            interaction_id=f"interaction-{index}",
            identity_id="satori",
            counterparty_id=f"counterparty-{index}",
            observed_at=now + timedelta(minutes=index),
            content=str(content),
        )
        for index, content in enumerate(raw_messages, start=1)
    )
    proposal = PositionProposal(
        proposition="Проверяемая позиция корпуса",
        kind=PositionKind(str(scenario["kind"])),
        stance=PositionStance(str(scenario["stance"])),
        confidence=0.99,
        evidence=tuple(
            PositionEvidenceCitation(
                message_id=item.message_id,
                quote=item.content,
                role=PositionEvidenceRole.ARGUMENT,
            )
            for item in sources
        ),
        value_key=(str(scenario["value_key"]) if scenario["value_key"] is not None else None),
    )
    ids = SequenceIdGenerator(*(f"id-{index}" for index in range(1, 100)))

    plan = PositionManager().evaluate(
        (proposal,),
        identity_id="satori",
        current_message_id=sources[-1].message_id,
        sources=sources,
        value_keys=frozenset({"intellectual_honesty"}),
        existing_positions=(),
        max_positions=3,
        now=now + timedelta(hours=1),
        decision_id="decision",
        new_id=ids.new,
    )

    assert plan.created_count == scenario["expected_created"]
    assert plan.rejected_count == scenario["expected_rejected"]
