"""Query-aware public provider fixtures exercise the production Core and read ports.

These closed fixtures apply the actual outgoing query before returning an ID or
body. They test host/compilation recall for this corpus, not live provider search.
"""

import asyncio
import base64
import json
import os
import re
import shlex
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from threading import Event
from typing import Any, ClassVar, Literal
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from pydantic import SecretStr

import tests.calendar_planning_fixture as core_fixture
from satori.application.assistant.contracts import LocalPrincipal
from satori.application.assistant.mail import MailAccessCoordinator
from satori.application.assistant.mail_contracts import MailClock
from satori.application.assistant.source_answer_contracts import (
    SourceSearchPlan,
    SourceTaskRequest,
    SourceTaskScope,
    SourceTaskSnapshot,
)
from satori.application.assistant.source_answer_reasoning import CoreSourceAnswerReasoner
from satori.application.assistant.source_answer_service import SourceAnswerService
from satori.application.source_policy.dispatch import admit_provider_dispatch
from satori.core.conversation import ConversationProviderRequest, ConversationProviderResponse
from satori.desktop.telegram_reply_native import build_reply_engine, legacy_definitions
from satori.domain.memory_scope import MemoryScope
from satori.infrastructure.gmail.connector import GmailConnector
from satori.infrastructure.gmail.http import GoogleHTTP
from satori.infrastructure.persistence.database import Database
from tests.test_conversation import success_response
from tests.test_source_task_adapters import (
    NOW,
    RESOURCES,
    TaskNative,
    mail_connector,
    message,
    task_command,
)

ANCHOR = "Публичной Лаборатории"
RECORDED_BAD_QUERY = "«Публичная Лаборатория» Python-разработчик собеседование ответ"
R1_PREFERENCE = (
    "PUBLIC TEST: 5 ноября 2026 я прошёл собеседование в компании «Публичная Лаборатория» "
    "на позицию Python-разработчика."
)
R1_SUMMARY = (
    "PUBLIC TEST: последнее собеседование пользователя было 5 ноября 2026 "
    "в компании «Публичная Лаборатория», позиция Python-разработчика."
)
R1_RECENT = (
    "PUBLIC TEST: последнее собеседование — именно в «Публичной Лаборатории». Жду от них ответа."
)
R1_QUERY = "Привет, посмотри, написали ли мне по последнему собеседованию."
PUBLIC_TITLE = "PUBLIC TEST: Следующий этап в Публичной Лаборатории"
PUBLIC_BODY = (
    "PUBLIC TEST: Здравствуйте! По итогам последнего собеседования в "
    "«Публичной Лаборатории» приглашаем вас на следующий этап "
    "13 ноября 2026 в 14:00 по московскому времени. "
    "Подтвердите, пожалуйста, удобно ли вам."
)
# Preserve the inflected title/body spelling from the exact public GPT fixture.
PUBLIC_SEARCH_TEXT = PUBLIC_TITLE + "\n" + PUBLIC_BODY


def gmail_fixture_match(query: str) -> bool:
    """Only conjunctive words/quoted phrases used by this explicit corpus."""
    normalized = PUBLIC_SEARCH_TEXT.casefold()
    terms = shlex.split(query.replace("«", "").replace("»", ""))
    return bool(terms) and all(term.casefold() in normalized for term in terms)


def telegram_fixture_match(query: str) -> bool:
    """Apply literal text; never return the fixture regardless of its query."""
    return bool(query) and query.casefold() in PUBLIC_SEARCH_TEXT.casefold()


class QueryAwareMail(GoogleHTTP):
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.queries: list[str] = []
        self.returned_ids: set[str] = set()

    def request(
        self,
        method: Literal["GET", "POST"],
        url: str,
        *,
        token: SecretStr | None = None,
        form: dict[str, str] | None = None,
        max_bytes: int = 524288,
        cancel: Event,
        timeout: float = 10,
    ) -> bytes:
        assert method == "GET"
        assert token is not None
        assert form is None
        assert not cancel.is_set()
        assert 0 < timeout <= 10
        assert max_bytes <= 524288
        self.calls.append(url)
        path, query = urlsplit(url).path, parse_qs(urlsplit(url).query)
        if path.endswith("/messages"):
            assert query["maxResults"] == ["20"]
            assert query["includeSpamTrash"] == ["true"]
            assert "labelIds" not in query
            match = re.fullmatch(r"\((.+)\) after:(-?\d+) before:(-?\d+)", query["q"][0])
            assert match is not None
            literal, after, before = match.groups()
            self.queries.append(literal)
            arrival = int((NOW - timedelta(hours=1)).timestamp())
            found = int(after) < arrival < int(before) and gmail_fixture_match(literal)
            self.returned_ids = {"PUBLIC-MAIL-1"} if found else set()
            return json.dumps({"messages": [{"id": key} for key in self.returned_ids]}).encode()
        key = path.rsplit("/", 1)[-1]
        assert key in self.returned_ids, "body/metadata must derive from this query's actual hit"
        headers = [
            {"name": "Subject", "value": PUBLIC_TITLE},
            {"name": "From", "value": "PUBLIC recruiter <recruiter@example.invalid>"},
        ]
        payload: dict[str, Any] = {"headers": headers}
        if query["format"] == ["full"]:
            payload.update(
                partId="0",
                mimeType="text/plain",
                body={
                    "size": len(PUBLIC_BODY.encode()),
                    "data": base64.urlsafe_b64encode(PUBLIC_BODY.encode()).decode().rstrip("="),
                },
            )
        else:
            assert query["format"] == ["metadata"]
        return json.dumps(
            {
                "id": key,
                "internalDate": str(int((NOW - timedelta(hours=1)).timestamp() * 1000)),
                "payload": payload,
            }
        ).encode()


class QueryAwareNative(TaskNative):
    rows: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        row = message(age=1)
        row["content"]["text"]["text"] = PUBLIC_SEARCH_TEXT
        type(self).rows = [row]
        self.protected = False

    def _answer(self, value: dict[str, Any]) -> None:
        if value["@type"] != "searchMessages":
            super()._answer(value)
            return
        self.calls.append(deepcopy(value))
        assert value["limit"] == 5
        assert value["chat_list"] is None
        assert value["chat_type_filter"] == {"@type": "searchMessagesChatTypeFilterPrivate"}
        matches = [
            deepcopy(row)
            for row in self.rows
            if value["min_date"] <= row["date"] <= value["max_date"]
            and telegram_fixture_match(value["query"])
        ]
        self.queue.append(
            {
                "@type": "foundMessages",
                "messages": matches,
                "next_offset": "",
                "total_count": len(matches),
                "@extra": value["@extra"],
            }
        )


class AnchorPlanner:
    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.calls = 0

    async def generate(
        self, request: ConversationProviderRequest, /
    ) -> ConversationProviderResponse:
        admit_provider_dispatch()
        self.calls += 1
        packet = next(
            json.loads(message.content)
            for message in request.messages
            if message.content.startswith("{")
            and json.loads(message.content).get("kind") == "source_search_constraints"
        )
        projections = packet["anchor_projections"]
        projection = next(value for value in projections if value["kind"] == self.kind)
        # The authority index carries refs/kinds only. Actual eligible canonical
        # text is supplied once by the existing Core composition, not duplicated.
        assert ANCHOR in "\n".join(message.content for message in request.messages)
        # The model supplies only an eligible literal/ref, not Gmail or TDLib syntax.
        value = {
            "status": "search",
            "clarification": None,
            "literal_anchor": ANCHOR,
            "canonical_ref": projection["ref"],
            "anchor_kind": "company",
            "window_start": packet["window_start"],
            "window_end": packet["window_end"],
        }
        return replace(
            success_response(json.dumps(value, ensure_ascii=False)),
            provider="fake-a",
            model="fixture-a",
        )


def actual_read_ports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: SourceTaskRequest,
    plan: SourceSearchPlan,
) -> tuple[SourceTaskSnapshot, SourceTaskSnapshot, QueryAwareMail, QueryAwareNative]:
    fixture_connector, _, _ = mail_connector()
    principal = LocalPrincipal(
        os_uid=os.getuid(), profile_id=request.scope.profile_id, session_id=uuid4()
    )
    assert fixture_connector._session is not None
    wire = QueryAwareMail()
    connector = GmailConnector(
        principal, http=wire, clock=lambda: MailClock(wall=NOW, monotonic=1000.0)
    )
    connector._session = fixture_connector._session
    monkeypatch.setattr(
        MailAccessCoordinator, "select", lambda *_args, **_kwargs: pytest.fail("manual selection")
    )
    mail = connector.task_search(request, plan, cancel=Event())
    legacy = legacy_definitions(RESOURCES / "telegram/backend/worker.py.source")
    legacy["NativeClient"] = QueryAwareNative
    events: list[dict[str, Any]] = []
    engine = build_reply_engine(
        legacy,
        Path("/PUBLIC/unloaded.dylib"),
        "0" * 64,
        tmp_path / "PUBLIC-session",
        events.append,
        principal=principal,
        generation=1,
        journal_path=tmp_path / "PUBLIC-journal.sqlite3",
        clock=lambda: 1000.0,
        wall=lambda: NOW.timestamp(),
    )
    native = engine.native_factory(Path("/PUBLIC/unloaded.dylib"), "0" * 64)
    engine.native = native
    engine.account_confirmed, engine.auth_state = True, "authorizationStateReady"
    engine.account_id, engine.session_until = 1, 1900
    task_command(engine, request, plan)
    telegram = SourceTaskSnapshot.model_validate_json(json.dumps(events[-1]["snapshot"]))
    assert engine.native is native
    assert engine.selected_chat is None
    assert engine.page_id is None
    assert isinstance(native, QueryAwareNative)
    return mail, telegram, wire, native


@pytest.mark.parametrize("anchor_origin", ["current_request", "canonical_memory", "recent_user"])
def test_actual_core_compiled_anchor_retrieves_public_body_that_recorded_query_misses(
    migrated_database: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    anchor_origin: str,
) -> None:
    async def scenario() -> None:
        monkeypatch.setattr(core_fixture, "NOW", NOW - timedelta(hours=1))
        monkeypatch.setattr(core_fixture.PlanningClock, "instant", NOW - timedelta(hours=1))
        monkeypatch.setattr(
            core_fixture,
            "PREFERENCE",
            R1_PREFERENCE,
        )
        monkeypatch.setattr(
            core_fixture,
            "SUMMARY",
            # The additional canonical-memory case has an eligible inflected name.
            # The recent-user case preserves the exact failed R1 context instead.
            "PUBLIC TEST: последнее собеседование пользователя было "
            "в «Публичной Лаборатории», позиция Python-разработчика."
            if anchor_origin == "canonical_memory"
            else R1_SUMMARY,
        )
        monkeypatch.setattr(
            core_fixture,
            "RECENT",
            R1_RECENT
            if anchor_origin == "recent_user"
            else "PUBLIC TEST: жду ответа по последнему собеседованию.",
        )
        query = (
            "Посмотри, написали ли мне по собеседованию в «Публичной Лаборатории»."
            if anchor_origin == "current_request"
            else R1_QUERY
        )
        monkeypatch.setattr(core_fixture, "QUERY", query)
        database, core, _, clock, original = await core_fixture.seeded_core(
            migrated_database, tmp_path
        )
        model = AnchorPlanner(anchor_origin)
        core.talk.provider = model
        request = SourceTaskRequest(
            request_id=uuid4(),
            scope=SourceTaskScope(
                identity_id=original.identity_id,
                profile_id=original.profile_id,
                session_id=original.session_id,
                generation=original.generation,
                timezone="Europe/Moscow",
            ),
            user_text=query,
            sources=("mail", "telegram"),
            created_at=clock(),
            expires_at=clock() + timedelta(seconds=120),
        )
        service = SourceAnswerService(
            request=request,
            reasoner=CoreSourceAnswerReasoner(
                core.talk, core.emotion_status, "fake-a:fixture-a", clock
            ),
            require_live=lambda: None,
            clock=clock,
        )
        memory_scope = MemoryScope(str(request.scope.identity_id), "local-default")
        before = (
            core.history.execute(),
            core.memories.execute(scope=memory_scope),
            core.talk.get_self.execute(),
            core.emotion_status.execute(str(request.scope.identity_id)),
        )
        try:
            compiled = await service.plan()
            assert compiled.mail_query == f'"{ANCHOR}"'
            assert compiled.telegram_query == ANCHOR
            old = compiled.model_copy(
                update={
                    "mail_query": RECORDED_BAD_QUERY,
                    "telegram_query": RECORDED_BAD_QUERY,
                }
            )
            old_mail, old_tg, old_wire, old_native = actual_read_ports(
                tmp_path / "old-query", monkeypatch, request, old
            )
            assert old_mail.items == old_tg.items == ()
            assert old_wire.queries == [RECORDED_BAD_QUERY]
            assert [call["@type"] for call in old_native.calls] == ["searchMessages"]
            assert all("format=full" not in url for url in old_wire.calls)

            if anchor_origin == "recent_user":
                # Literal compilation does not promise morphological expansion.
                # An application-authored phrase still must actually match data.
                nominative = compiled.model_copy(
                    update={
                        "mail_query": '"Публичная Лаборатория"',
                        "telegram_query": "Публичная Лаборатория",
                    }
                )
                missed_mail, missed_tg, _, _ = actual_read_ports(
                    tmp_path / "nominative-query", monkeypatch, request, nominative
                )
                assert missed_mail.items == missed_tg.items == ()

            mail, telegram, wire, native = actual_read_ports(
                tmp_path / "compiled-query", monkeypatch, request, compiled
            )
            assert len(mail.items) == len(telegram.items) == 1
            assert mail.items[0].text == PUBLIC_BODY
            assert telegram.items[0].text == PUBLIC_SEARCH_TEXT
            assert wire.queries == [compiled.mail_query]
            assert len([url for url in wire.calls if "format=full" in url]) == 1
            assert (
                next(call for call in native.calls if call["@type"] == "searchMessages")["query"]
                == compiled.telegram_query
            )
            assert mail.plan_digest == telegram.plan_digest == compiled.digest
            assert mail.coverage[0].query == compiled.mail_query
            assert telegram.coverage[0].query == compiled.telegram_query
            assert model.calls == 1  # No model fallback or automatic broadening search.
            assert (
                core.history.execute(),
                core.memories.execute(scope=memory_scope),
                core.talk.get_self.execute(),
                core.emotion_status.execute(str(request.scope.identity_id)),
            ) == before
        finally:
            service.close()
            database.dispose()

    asyncio.run(scenario())
