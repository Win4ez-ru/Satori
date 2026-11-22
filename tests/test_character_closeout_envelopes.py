"""Offline request-envelope stress; random public prose is never Character evidence.

The synthetic usage envelope is deliberately a fixture, not a tokenization claim.
Long output fills the legal application character boundary to test input byte caps.
No model, credentials, network transport or production state is involved.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import socket
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from satori.config import Environment
from tests.character_closeout_api_test import EvaluationSettings, RetryAuthorization, _ports
from tests.character_closeout_plan import (
    CAP_BY_OPERATION,
    CloseoutPaidLedger,
    build_execution_plan,
    wire_json_bytes,
)
from tests.character_closeout_runtime import FixedEvaluationClock, build_runtime, run_turn
from tests.test_character_closeout_api_adapters import (
    ScriptedCloseoutTransport,
    offline_api_response,
)


class EnvelopeLedger(CloseoutPaidLedger):
    def __init__(self, plan: Mapping[str, Any]) -> None:
        super().__init__(plan, lambda _snapshot: None)
        self.maximum_wire_bytes: dict[str, int] = {}

    def reserve(self, operation: str, model: str, payload: Mapping[str, Any]) -> str:
        size = len(wire_json_bytes(payload))
        self.maximum_wire_bytes[operation] = max(self.maximum_wire_bytes.get(operation, 0), size)
        return super().reserve(operation, model, payload)


class LongPublicOutputTransport(ScriptedCloseoutTransport):
    """Keep the newest complete historical pair at exactly 6000 characters."""

    def __init__(self) -> None:
        super().__init__()
        self.foreground_count = 0

    def post_json(
        self,
        path: str,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes:
        schema = payload.get("text", {}).get("format", {}).get("name", "foreground")
        if path == "/responses" and schema == "foreground":
            self.calls.append((path, dict(payload)))
            self.foreground_count += 1
            current_user = payload["input"][-1]["content"]
            assert isinstance(current_user, str)
            characters = 6000 - len(current_user)
            generator = random.Random(self.foreground_count)
            # Four-byte code points stress UTF-8 separately from the character budget.
            alphabet = "абвгдежзийклмнопрстуфхцчшщэюя" + "😀🦊🌌🎨🔧🧭"
            text = "".join(generator.choice(alphabet) for _ in range(characters))
            return json.dumps(offline_api_response(text=text), ensure_ascii=False).encode("utf-8")
        return super().post_json(
            path, payload, timeout_seconds=timeout_seconds, max_response_bytes=max_response_bytes
        )


def test_long_recent_context_and_real_owner_memory_fit_frozen_wire_envelopes(
    tmp_path: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("offline request-envelope stress attempted network")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    corpus_path = project_root / "docs/evaluation/character-closeout-scenarios.v1.json"
    corpus = json.loads(corpus_path.read_text())
    plan = build_execution_plan(corpus_path, source_hashes={"offline-stress-fixture.py": "0" * 64})
    ledger = EnvelopeLedger(plan)
    transport = LongPublicOutputTransport()
    active = build_runtime(
        tmp_path / "stress.db",
        source_policy_directory=tmp_path / "source-policy",
        settings=EvaluationSettings(environment=Environment.TEST),
        ports=_ports(transport, ledger, credential="synthetic-never-sent"),
        clock=FixedEvaluationClock(),
        activate=True,
        alembic_config=project_root / "alembic.ini",
    )
    handler = RetryAuthorization(ledger)
    logger = active.services.talk.logger
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    outcomes: list[dict[str, Any]] = []
    try:
        session = active.services.start_session.execute()
        for row, authored in zip(plan["turns"][:6], corpus["cases"][0]["user_turns"], strict=True):
            ledger.begin_turn(row["turn_id"])
            try:
                outcome = asyncio.run(
                    run_turn(
                        active,
                        turn_id=row["turn_id"],
                        user_text=authored["text"],
                        session_id=session.session_id,
                    )
                )
            except Exception as error:
                pytest.fail(
                    "offline envelope stress stopped: "
                    + type(error).__name__
                    + "; maximum measured UTF-8 wire bytes="
                    + json.dumps(ledger.maximum_wire_bytes, sort_keys=True)
                )
            assert outcome["complete"] is True
            outcomes.append(outcome)
            ledger.end_turn()
            active.clock.advance(60)
        assert max(row["manifest"]["recent_conversation_chars"] for row in outcomes) == 6000
        assert any(row["manifest"]["retrieved_memory_ids"] for row in outcomes)
        assert any(row["manifest"]["retrieved_semantic_claim_ids"] for row in outcomes)
        assert active.snapshot()["memories"]
        for operation, maximum in ledger.maximum_wire_bytes.items():
            assert maximum <= CAP_BY_OPERATION[operation].max_body_bytes
        print(
            "SYNTHETIC_ENVELOPE_MAXIMUM_WIRE_BYTES="
            + json.dumps(ledger.maximum_wire_bytes, sort_keys=True)
        )
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)
        active.close()
