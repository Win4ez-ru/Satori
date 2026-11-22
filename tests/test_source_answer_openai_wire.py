"""Real Responses adapter over public in-memory HTTP, never a network/key check."""

import asyncio
import json
from datetime import timedelta
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest

from satori.application.assistant.source_answer_contracts import (
    SourceAnswerError,
    SourceCoverage,
    SourceObservation,
    SourceTaskRequest,
    SourceTaskScope,
    SourceTaskSnapshot,
)
from satori.application.assistant.source_answer_reasoning import CoreSourceAnswerReasoner
from satori.application.assistant.source_answer_service import SourceAnswerService
from satori.application.conversation.context import CharacterContextComposer
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.providers.openai import OpenAIConversationAdapter
from tests.calendar_planning_fixture import QUERY, RECENT, SUMMARY, seeded_core
from tests.test_openai_provider import response_body


class PublicResponsesTransport:
    def __init__(self, model: str) -> None:
        self.model = model
        self.calls: list[dict[str, object]] = []

    def post_json(
        self,
        path: str,
        payload: dict[str, object],
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes:
        assert path == "/responses"
        assert payload["store"] is False
        assert payload["model"] == "gpt-6-luna"
        messages = cast(list[dict[str, str]], payload["input"])
        assert messages[-1] == {"role": "user", "content": QUERY}
        body = "\n".join(message["content"] for message in messages)
        assert SUMMARY in body
        assert RECENT in body
        packet = next(
            value
            for message in messages
            if message["content"].startswith("{")
            and isinstance((value := json.loads(message["content"])), dict)
            and value.get("kind") in {"source_search_constraints", "source_observation"}
        )
        self.calls.append(payload)
        if packet["kind"] == "source_search_constraints":
            assert all(set(item) == {"ref", "kind"} for item in packet["anchor_projections"])
            answer = {
                "status": "search",
                "literal_anchor": "PUBLIC_PREFERENCE_AFTER_16",
                "canonical_ref": next(
                    item["ref"]
                    for item in packet["anchor_projections"]
                    if item["kind"] == "canonical_memory"
                ),
                "anchor_kind": "company",
                "window_start": packet["window_start"],
                "window_end": packet["window_end"],
            }
        else:
            assert packet["timezone"] == "Europe/Moscow"
            item = packet["items"][0]
            assert item["event_at"].endswith("Z")
            assert item["arrival_local"].endswith("+03:00")
            assert "PUBLIC external source" in item["text"]
            answer = {
                "text": "Нашла: следующий этап предложили в письме.",
                "citations": [item["source_ref"]],
                "verdict": "found",
            }
        envelope = json.loads(response_body(text=json.dumps(answer, ensure_ascii=False)))
        envelope["model"] = self.model
        return json.dumps(envelope, ensure_ascii=False).encode()


@pytest.mark.parametrize("model", ["gpt-6-luna", "gpt-6-luna-2026-11-01", "other-model"])
def test_two_phase_same_core_actual_responses_wire(
    migrated_database: Database,
    tmp_path: Path,
    model: str,
) -> None:
    async def scenario() -> None:
        database, core, _old, clock, old_scope = await seeded_core(migrated_database, tmp_path)
        transport = PublicResponsesTransport(model)
        core.talk.provider = OpenAIConversationAdapter(
            base_url="https://api.openai.com/v1",
            api_key="public-test-not-a-real-key",
            model="gpt-6-luna",
            timeout_seconds=30,
            reasoning_effort="none",
            reasoning_token_allowance=0,
            http_client=transport,
        )
        core.talk.context_composer = CharacterContextComposer("openai", "gpt-6-luna")
        request = SourceTaskRequest(
            request_id=uuid4(),
            scope=SourceTaskScope(
                identity_id=old_scope.identity_id,
                profile_id=old_scope.profile_id,
                session_id=old_scope.session_id,
                generation=old_scope.generation,
                timezone="Europe/Moscow",
            ),
            user_text=QUERY,
            sources=("mail",),
            created_at=clock(),
            expires_at=clock() + timedelta(seconds=120),
        )
        service = SourceAnswerService(
            request=request,
            reasoner=CoreSourceAnswerReasoner(
                core.talk,
                core.emotion_status,
                "openai:gpt-6-luna",
                clock,
            ),
            require_live=lambda: None,
            clock=clock,
        )
        history = core.history.execute()
        if model != "gpt-6-luna":
            with pytest.raises(SourceAnswerError, match="identity_mismatch"):
                await service.plan()
            assert len(transport.calls) == 1
        else:
            plan = await service.plan()
            assert plan.mail_query == '"PUBLIC_PREFERENCE_AFTER_16"'
            assert plan.telegram_query is None
            snapshot = SourceTaskSnapshot(
                request_id=request.request_id,
                scope=request.scope,
                plan_digest=plan.digest,
                observed_at=clock(),
                items=(
                    SourceObservation(
                        source="mail",
                        source_ref="mail:public-response",
                        observed_at=clock(),
                        event_at=clock() - timedelta(hours=1),
                        title="PUBLIC interview",
                        text="PUBLIC external source",
                        completeness="complete",
                    ),
                ),
                coverage=(
                    SourceCoverage(
                        source="mail",
                        status="searched",
                        query=plan.mail_query,
                        window_start=plan.window_start,
                        window_end=plan.window_end,
                    ),
                ),
            )
            answer = await service.answer(snapshot)
            assert answer.verdict == "found"
            assert len(transport.calls) == 2
        assert core.history.execute() == history
        database.dispose()

    asyncio.run(scenario())
