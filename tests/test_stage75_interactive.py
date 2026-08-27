"""Stage 7.5 interactive runtime, bounded continuity, and delivery lifecycle."""

import asyncio
import json
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from io import StringIO
from itertools import count

import pytest

from satori.application.conversation.coherence import SESSION_RECAP_MAX_RECENT_TURNS
from satori.application.conversation.contracts import SatoriReply, TalkInput
from satori.application.conversation.errors import UnsupportedPastClaim
from satori.composition import (
    ConversationServices,
    build_conversation_services,
    build_initial_self_services,
)
from satori.config import Environment, Settings
from satori.core.affect import AffectiveAppraisalProposal, AffectiveAppraisalProviderResponse
from satori.core.conversation import (
    ConversationPastClaim,
    ConversationProviderRequest,
    ConversationProviderResponse,
    ConversationUsage,
    GenerationFailed,
    ProviderUnavailable,
)
from satori.core.episode import (
    EpisodeFormationProposal,
    EpisodeFormationProviderResponse,
    EpisodeFormationRequest,
)
from satori.core.ids import Uuid4Generator
from satori.core.provider_metrics import ProviderExecutionMetrics
from satori.domain.conversation_history import InteractionStatus, SessionStatus
from satori.domain.errors import NotActivated
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.providers.ollama_http import OllamaHttpClient
from satori.infrastructure.seeds.loader import JsonSeedLoader
from satori.interactive import InteractiveChat
from tests.fakes import (
    FakeAffectiveAppraisalProvider,
    FakeConversationProvider,
    FakeEpisodeFormationProvider,
    FrozenClock,
)

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
SERVICE_IDS = count(1)


class InputFeeder:
    """Thread-safe-enough finite input source for the serial CLI read loop."""

    def __init__(self, *lines: str, failure: BaseException | None = None) -> None:
        self._lines = iter(lines)
        self._failure = failure

    def __call__(self, _prompt: str) -> str:
        try:
            return next(self._lines)
        except StopIteration:
            if self._failure is not None:
                raise self._failure from None
            raise EOFError from None


class CountingConversationProvider:
    """Return one concise response per call while retaining every bounded request."""

    def __init__(self) -> None:
        self.requests: list[ConversationProviderRequest] = []

    async def generate(
        self, request: ConversationProviderRequest, /
    ) -> ConversationProviderResponse:
        self.requests.append(request)
        return ConversationProviderResponse(
            text=f"Ответ {len(self.requests)}",
            provider="fake-conversation",
            model="fixture-conversation",
            finish_status="stop",
            usage=ConversationUsage(input_tokens=120, output_tokens=8),
            metrics=ProviderExecutionMetrics(
                total_duration_ns=2_000_000,
                load_duration_ns=100_000,
                prompt_eval_duration_ns=600_000,
                eval_duration_ns=1_000_000,
                prompt_eval_count=20,
                eval_count=5,
            ),
        )


class ScriptedConversationProvider:
    """Return a finite response sequence for bounded-regeneration scenarios."""

    def __init__(
        self,
        *responses: str | ConversationProviderResponse | Exception,
    ) -> None:
        self._responses = iter(responses)
        self.requests: list[ConversationProviderRequest] = []

    async def generate(
        self, request: ConversationProviderRequest, /
    ) -> ConversationProviderResponse:
        self.requests.append(request)
        response = next(self._responses)
        if isinstance(response, Exception):
            raise response
        if isinstance(response, ConversationProviderResponse):
            return response
        return ConversationProviderResponse(
            text=response,
            provider="fake-conversation",
            model="fixture-conversation",
            finish_status="stop",
        )


class OrderedIdGenerator:
    """Produce lexically ordered stable IDs for same-clock persistence tests."""

    def __init__(self, prefix: str) -> None:
        self._prefix = prefix
        self._value = 0

    def new(self) -> str:
        self._value += 1
        return f"{self._prefix}-{self._value:08d}"


class BlockingEpisodeProvider:
    """Hold derived work open so canonical reply visibility can be observed."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def generate_structured(
        self, _request: EpisodeFormationRequest, /
    ) -> EpisodeFormationProviderResponse:
        self.started.set()
        await self.release.wait()
        return skip_episode_response()


class BlockingConversationProvider:
    """Hold generation until cancelled to model Ctrl+C during inference."""

    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def generate(
        self, _request: ConversationProviderRequest, /
    ) -> ConversationProviderResponse:
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("blocking provider should have been cancelled")


def skip_episode_response() -> EpisodeFormationProviderResponse:
    return EpisodeFormationProviderResponse(
        proposal=EpisodeFormationProposal(1, False, None, None, None, ()),
        provider="fake-episode",
        model="fixture-episode",
        formation_method="fixture.v1",
    )


def affect_response(interaction_id: str) -> AffectiveAppraisalProviderResponse:
    return AffectiveAppraisalProviderResponse(
        proposal=AffectiveAppraisalProposal(
            schema_version=1,
            pleasantness=0.5,
            activation=0.4,
            novelty=0.3,
            salience=0.8,
            uncertainty=0.1,
            curiosity_signal=0.4,
            interest_signal=0.7,
            humor_signal=0.0,
            concern_signal=0.1,
            frustration_signal=0.0,
            confidence_signal=0.5,
            appraisal_confidence=0.9,
            source_refs=(interaction_id,),
            reason_codes=("engaged",),
        ),
        provider="fake-appraisal",
        model="fixture-appraisal",
        appraisal_method="fixture.appraisal.v1",
    )


def stage75_settings(
    *,
    recent_conversation_max_turns: int = 8,
    recent_conversation_max_chars: int = 6000,
    conversation_max_response_chars: int = 12_000,
) -> Settings:
    return Settings(
        environment=Environment.TEST,
        database_url="sqlite+pysqlite:///:memory:",
        recent_conversation_max_turns=recent_conversation_max_turns,
        recent_conversation_max_chars=recent_conversation_max_chars,
        conversation_max_response_chars=conversation_max_response_chars,
    )


def activate(database: Database) -> None:
    initial = build_initial_self_services(
        database,
        clock=FrozenClock(NOW),
        id_generator=OrderedIdGenerator("stage75-activation"),
    )
    initial.activate.execute(JsonSeedLoader().load_canonical(), trace_id="activate-stage75")


def build_services(
    database: Database,
    conversation: (
        CountingConversationProvider
        | ScriptedConversationProvider
        | BlockingConversationProvider
        | FakeConversationProvider
    ),
    *,
    episode: BlockingEpisodeProvider | FakeEpisodeFormationProvider | None = None,
    appraisal: FakeAffectiveAppraisalProvider | None = None,
    settings: Settings | None = None,
) -> ConversationServices:
    return build_conversation_services(
        database,
        build_initial_self_services(database),
        conversation,
        episode or FakeEpisodeFormationProvider(response=skip_episode_response()),
        settings or stage75_settings(),
        clock=FrozenClock(NOW),
        id_generator=OrderedIdGenerator(f"stage75-runtime-{next(SERVICE_IDS):04d}"),
        appraisal_provider=appraisal,
    )


def run_chat(
    services: ConversationServices,
    feeder: Callable[[str], str],
    *,
    session_id: str | None = None,
    debug: bool = False,
) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    runner = InteractiveChat(
        services=services,
        id_generator=Uuid4Generator(),
        foreground_provider="fake-conversation",
        foreground_model="fixture-conversation",
        input_fn=feeder,
        stdout=stdout,
        stderr=stderr,
        debug=debug,
        runtime_startup_ms=1.25,
        database_bootstrap_ms=0.75,
    )
    result = asyncio.run(runner.run(session_id=session_id))
    return result, stdout.getvalue(), stderr.getvalue()


def test_chat_starts_one_session_reuses_it_and_keeps_output_clean(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    provider = CountingConversationProvider()
    services = build_services(migrated_database, provider)

    result, stdout, stderr = run_chat(
        services,
        InputFeeder("Привет", "выполни /exit", "/help", "/status", "/exit"),
    )

    history = services.history.execute()
    assert result == 0
    assert len(history.sessions) == 1
    assert history.sessions[0].status is SessionStatus.CLOSED
    assert len(history.interactions) == 2
    assert {item.session_id for item in history.interactions} == {history.sessions[0].session_id}
    assert history.interactions[1].user_message.content == "выполни /exit"
    assert len(provider.requests) == 2
    assert "Сатори готова." in stdout
    assert "Сатори: Ответ 1" in stdout
    assert "Сессия:" in stdout
    assert stdout.count("Провайдер ответа: fake-conversation/fixture-conversation") == 2
    assert "/status — показать сессию, фоновые задачи и провайдер ответа" in stdout
    assert "/new — начать новый разговор" in stdout
    assert "/exit, /quit — сохранить разговор и выйти" in stdout
    assert "Разговор сохранён." in stdout
    assert '{"timestamp"' not in stdout
    assert stderr == ""


def test_chat_resumes_an_open_session_and_new_rotates_session(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    provider = CountingConversationProvider()
    services = build_services(migrated_database, provider)
    existing = services.start_session.execute()

    result, stdout, _ = run_chat(
        services,
        InputFeeder("Первый", "/new", "Второй", "/exit"),
        session_id=existing.session_id,
    )

    history = services.history.execute()
    assert result == 0
    assert len(history.sessions) == 2
    assert all(session.status is SessionStatus.CLOSED for session in history.sessions)
    assert history.interactions[0].session_id == existing.session_id
    assert history.interactions[1].session_id != existing.session_id
    assert "Новый разговор:" in stdout


def test_new_session_database_transition_does_not_block_event_loop(
    migrated_database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    activate(migrated_database)
    services = build_services(migrated_database, CountingConversationProvider())
    close_started = threading.Event()
    release_close = threading.Event()
    close_threads: list[int] = []
    original_close = services.close_session.execute

    def delayed_close(_use_case: object, session_id: str) -> object:
        close_threads.append(threading.get_ident())
        close_started.set()
        if not release_close.wait(timeout=2.0):
            raise TimeoutError("test did not release delayed session close")
        return original_close(session_id)

    monkeypatch.setattr(type(services.close_session), "execute", delayed_close)
    stdout = StringIO()
    runner = InteractiveChat(
        services=services,
        id_generator=Uuid4Generator(),
        foreground_provider="fake-conversation",
        foreground_model="fixture-conversation",
        input_fn=InputFeeder("/new", "/exit"),
        stdout=stdout,
        stderr=StringIO(),
    )
    event_loop_thread = threading.get_ident()

    async def scenario() -> None:
        task = asyncio.create_task(runner.run())
        assert await asyncio.to_thread(close_started.wait, 1.0)
        await asyncio.sleep(0)
        assert not task.done()
        release_close.set()
        assert await task == 0

    asyncio.run(scenario())

    assert close_threads[0] != event_loop_thread
    assert "Новый разговор:" in stdout.getvalue()


def test_recent_completed_context_preserves_immediate_name_continuity(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    provider = CountingConversationProvider()
    services = build_services(migrated_database, provider)
    session_id = services.start_session.execute().session_id

    async def execute() -> None:
        await services.talk.execute(
            TalkInput("Меня зовут Кирилл.", "trace-1", "request-1", session_id)
        )
        reply = await services.talk.execute(
            TalkInput("\u0410 как меня зовут?", "trace-2", "request-2", session_id)
        )
        assert reply.context_manifest.recent_conversation_turn_count == 1

    asyncio.run(execute())

    second = provider.requests[1]
    conversational = [
        message.content
        for message in second.messages
        if message.role.value in {"user", "assistant"}
    ]
    assert conversational == ["Меня зовут Кирилл.", "Ответ 1", "\u0410 как меня зовут?"]
    assert "Прошлый ответ" in second.messages[-2].content
    assert second.messages[-1].content == "\u0410 как меня зовут?"


def test_recent_context_remains_bounded_after_more_than_one_hundred_turns(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    provider = CountingConversationProvider()
    services = build_services(
        migrated_database,
        provider,
        settings=stage75_settings(
            recent_conversation_max_turns=3,
            recent_conversation_max_chars=256,
        ),
    )
    session_id = services.start_session.execute().session_id

    async def execute() -> None:
        for index in range(105):
            await services.talk.execute(
                TalkInput(
                    f"synthetic turn {index}",
                    f"trace-{index}",
                    f"request-{index}",
                    session_id,
                )
            )

    asyncio.run(execute())

    history = services.history.execute(session_id=session_id)
    final_request = provider.requests[-1]
    conversational_messages = [
        message for message in final_request.messages if message.role.value in {"user", "assistant"}
    ]
    assert len(history.interactions) == 105
    assert all(item.status is InteractionStatus.COMPLETED for item in history.interactions)
    assert len(conversational_messages) == 7
    assert "synthetic turn 100" not in {message.content for message in final_request.messages}
    assert "synthetic turn 101" in {message.content for message in final_request.messages}
    assert final_request.messages[-1].content == "synthetic turn 104"


def test_explicit_session_recap_uses_larger_read_only_canonical_window(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    provider = CountingConversationProvider()
    services = build_services(
        migrated_database,
        provider,
        settings=stage75_settings(recent_conversation_max_turns=2),
    )
    session_id = services.start_session.execute().session_id

    async def execute() -> None:
        for index in range(5):
            await services.talk.execute(
                TalkInput(
                    f"Тема {index}",
                    f"recap-trace-{index}",
                    f"recap-request-{index}",
                    session_id,
                )
            )
        recap = await services.talk.execute(
            TalkInput(
                "Подведи итог этого разговора в трёх пунктах.",
                "recap-trace-summary",
                "recap-request-summary",
                session_id,
            )
        )
        ordinary = await services.talk.execute(
            TalkInput(
                "Продолжим.",
                "recap-trace-ordinary",
                "recap-request-ordinary",
                session_id,
            )
        )

        assert recap.context_manifest.recent_conversation_turn_count == 5
        assert ordinary.context_manifest.recent_conversation_turn_count == 2

    asyncio.run(execute())

    recap_messages = [
        message.content
        for message in provider.requests[5].messages
        if message.role.value in {"user", "assistant"}
    ]
    assert "Тема 0" in recap_messages
    assert "Тема 4" in recap_messages


def test_topic_return_uses_extended_history_without_cross_session_leakage(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    provider = CountingConversationProvider()
    services = build_services(
        migrated_database,
        provider,
        settings=stage75_settings(recent_conversation_max_turns=2),
    )
    other_session_id = services.start_session.execute().session_id
    target_session_id = services.start_session.execute().session_id
    other_session_topic = "Секрет другой сессии: обсидиан."
    target_topic = "Наша мысль о джазе: импровизация — риск внутри формы."  # noqa: RUF001

    async def execute() -> SatoriReply:
        await services.talk.execute(
            TalkInput(
                other_session_topic,
                "recap-isolation-other-trace",
                "recap-isolation-other-request",
                other_session_id,
            )
        )
        for index, text in enumerate(
            (target_topic, "Поговорим о физике.", "А теперь о книгах.", "Ещё одна тема."),  # noqa: RUF001
            start=1,
        ):
            await services.talk.execute(
                TalkInput(
                    text,
                    f"recap-isolation-target-trace-{index}",
                    f"recap-isolation-target-request-{index}",
                    target_session_id,
                )
            )
        return await services.talk.execute(
            TalkInput(
                "Вернёмся к джазу: какую мысль мы обсуждали?",
                "recap-isolation-return-trace",
                "recap-isolation-return-request",
                target_session_id,
            )
        )

    reply = asyncio.run(execute())
    recap_messages = {
        message.content
        for message in provider.requests[-1].messages
        if message.role.value in {"user", "assistant"}
    }

    assert reply.context_manifest.recent_conversation_turn_count == 4
    assert target_topic in recap_messages
    assert other_session_topic not in recap_messages


def test_explicit_recap_window_is_bounded_to_thirty_two_whole_turns(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    provider = CountingConversationProvider()
    services = build_services(
        migrated_database,
        provider,
        settings=stage75_settings(
            recent_conversation_max_turns=1,
            recent_conversation_max_chars=40_000,
        ),
    )
    session_id = services.start_session.execute().session_id
    prior_turn_count = SESSION_RECAP_MAX_RECENT_TURNS + 3

    async def execute() -> SatoriReply:
        for index in range(prior_turn_count):
            await services.talk.execute(
                TalkInput(
                    f"bounded recap topic {index}",
                    f"recap-turn-bound-trace-{index}",
                    f"recap-turn-bound-request-{index}",
                    session_id,
                )
            )
        return await services.talk.execute(
            TalkInput(
                "Подведи итог этого разговора в трёх пунктах.",
                "recap-turn-bound-summary-trace",
                "recap-turn-bound-summary-request",
                session_id,
            )
        )

    reply = asyncio.run(execute())
    conversational_messages = {
        message.content
        for message in provider.requests[-1].messages
        if message.role.value in {"user", "assistant"}
    }
    first_included_index = prior_turn_count - SESSION_RECAP_MAX_RECENT_TURNS

    assert reply.context_manifest.recent_conversation_turn_count == SESSION_RECAP_MAX_RECENT_TURNS
    assert f"bounded recap topic {first_included_index - 1}" not in conversational_messages
    assert f"bounded recap topic {first_included_index}" in conversational_messages
    assert f"bounded recap topic {prior_turn_count - 1}" in conversational_messages


def test_explicit_recap_window_reuses_the_configured_whole_turn_character_bound(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    provider = CountingConversationProvider()
    max_chars = 256
    services = build_services(
        migrated_database,
        provider,
        settings=stage75_settings(
            recent_conversation_max_turns=1,
            recent_conversation_max_chars=max_chars,
        ),
    )
    session_id = services.start_session.execute().session_id
    prior_inputs = tuple(f"recap-char-{index}-" + "x" * 80 for index in range(6))

    async def execute() -> SatoriReply:
        for index, text in enumerate(prior_inputs):
            await services.talk.execute(
                TalkInput(
                    text,
                    f"recap-char-bound-trace-{index}",
                    f"recap-char-bound-request-{index}",
                    session_id,
                )
            )
        return await services.talk.execute(
            TalkInput(
                "Резюмируй этот разговор.",
                "recap-char-bound-summary-trace",
                "recap-char-bound-summary-request",
                session_id,
            )
        )

    reply = asyncio.run(execute())
    conversational_messages = {
        message.content
        for message in provider.requests[-1].messages
        if message.role.value in {"user", "assistant"}
    }

    assert reply.context_manifest.recent_conversation_turn_count == 2
    assert reply.context_manifest.recent_conversation_chars <= max_chars
    assert prior_inputs[-3] not in conversational_messages
    assert prior_inputs[-2] in conversational_messages
    assert prior_inputs[-1] in conversational_messages


def test_extended_recap_history_does_not_expand_coherence_beyond_last_eight_turns(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    provider = CountingConversationProvider()
    services = build_services(
        migrated_database,
        provider,
        settings=stage75_settings(recent_conversation_max_turns=2),
    )
    session_id = services.start_session.execute().session_id
    old_correction = "Не заканчивай каждый ответ вопросом."  # noqa: RUF001

    async def execute() -> SatoriReply:
        await services.talk.execute(
            TalkInput(
                old_correction,
                "recap-coherence-old-trace",
                "recap-coherence-old-request",
                session_id,
            )
        )
        for index in range(8):
            await services.talk.execute(
                TalkInput(
                    f"ordinary coherence topic {index}",
                    f"recap-coherence-trace-{index}",
                    f"recap-coherence-request-{index}",
                    session_id,
                )
            )
        return await services.talk.execute(
            TalkInput(
                "Подведи итог этого разговора в трёх пунктах.",
                "recap-coherence-summary-trace",
                "recap-coherence-summary-request",
                session_id,
            )
        )

    reply = asyncio.run(execute())
    recap_messages = {
        message.content
        for message in provider.requests[-1].messages
        if message.role.value in {"user", "assistant"}
    }

    assert reply.context_manifest.recent_conversation_turn_count == 9
    assert old_correction in recap_messages
    assert reply.context_manifest.active_style_corrections == ()


def test_canonical_reply_is_visible_while_post_response_work_is_pending(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    provider = CountingConversationProvider()
    episode = BlockingEpisodeProvider()
    services = build_services(migrated_database, provider, episode=episode)
    stdout = StringIO()
    runner = InteractiveChat(
        services=services,
        id_generator=Uuid4Generator(),
        foreground_provider="fake-conversation",
        foreground_model="fixture-conversation",
        input_fn=InputFeeder("Привет", "/status", "/exit"),
        stdout=stdout,
        stderr=StringIO(),
    )

    async def scenario() -> None:
        task = asyncio.create_task(runner.run())
        await episode.started.wait()
        assert "Сатори: Ответ 1" in stdout.getvalue()
        expected_status = "Фоновые задачи памяти для этого запуска: ожидают завершения=1, ошибок=0"
        async with asyncio.timeout(1.0):
            while expected_status not in stdout.getvalue():
                await asyncio.sleep(0)
        assert not task.done()
        episode.release.set()
        assert await task == 0

    asyncio.run(scenario())


def test_provider_and_post_processing_failures_are_safe_and_readable(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    failed_provider = FakeConversationProvider(
        error=ProviderUnavailable("ollama", "fixture", "offline")
    )
    provider_services = build_services(migrated_database, failed_provider)
    _, stdout, stderr = run_chat(
        provider_services,
        InputFeeder("Привет", "/exit"),
    )
    interaction = provider_services.history.execute().interactions[0]
    assert interaction.status is InteractionStatus.FAILED
    assert "Провайдер ответа временно недоступен." in stderr
    assert "Сатори:" not in stdout

    working_provider = CountingConversationProvider()
    failed_episode = FakeEpisodeFormationProvider(error=RuntimeError("offline"))
    memory_services = build_services(
        migrated_database,
        working_provider,
        episode=failed_episode,
    )
    _, stdout, _ = run_chat(memory_services, InputFeeder("Ещё раз", "/exit"))
    assert "Сатори: Ответ 1" in stdout
    assert "Часть обработки памяти можно повторить." in stdout


def test_yandex_unavailable_error_never_blames_ollama(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    failed_provider = FakeConversationProvider(
        error=ProviderUnavailable("yandex_ai_studio", "yandexgpt/latest", "HTTP 503")
    )
    services = build_services(migrated_database, failed_provider)

    _, stdout, stderr = run_chat(
        services,
        InputFeeder("Привет", "/exit"),
    )

    assert "Провайдер ответа временно недоступен." in stderr
    assert "Ollama" not in stderr
    assert "yandexgpt" not in stderr
    assert "Сатори:" not in stdout


def test_debug_provider_failure_reports_only_safe_output_budget_metadata(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    failed_provider = FakeConversationProvider(
        error=GenerationFailed(
            "openai",
            "gpt-5.6-terra",
            "OpenAI response ended with status incomplete; reason=max_output_tokens",
            metrics=ProviderExecutionMetrics(
                requested_output_token_limit=48,
                provider_output_token_limit=1072,
                reasoning_output_tokens=1024,
                visible_output_tokens=0,
            ),
        )
    )
    services = build_services(migrated_database, failed_provider)

    _, stdout, stderr = run_chat(
        services,
        InputFeeder("секретная реплика", "/exit"),
        debug=True,
    )

    assert (
        "[provider-budget] requested_visible_output_tokens=48 "
        "wire_max_output_tokens=1072 reasoning_tokens=1024 visible_output_tokens=0"
    ) in stderr
    assert "секретная реплика" not in stderr
    assert "секретная реплика" not in stdout


def test_debug_output_has_phase_and_metadata_only_provider_timings(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    provider = CountingConversationProvider()
    services = build_services(migrated_database, provider)

    _, stdout, stderr = run_chat(
        services,
        InputFeeder("секретная реплика", "/exit"),
        debug=True,
    )

    assert "[runtime]" in stderr
    assert (
        "[provider] provider=fake-conversation model=fixture-conversation finish=stop "
        "selected_input_tokens=120 selected_output_tokens=8 provider_attempts=1 replayed=false"
    ) in stderr
    assert "[turn]" in stderr
    assert "[context]" in stderr
    assert "[cognition]" in stderr
    assert "position=answer" in stderr
    assert "mode=general" in stderr
    assert "response_regeneration=" in stderr
    assert "[provider generation]" in stderr
    assert "provider_load_ms=0.1" in stderr
    assert "секретная реплика" not in stderr
    assert "секретная реплика" not in stdout


def test_debug_usage_is_explicitly_selected_attempt_not_total_retry_spend(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    provider = ScriptedConversationProvider(
        ConversationProviderResponse(
            text="Поняла, Кирилл, ты мой создатель.",
            provider="yandex_ai_studio",
            model="yandexgpt/latest",
            finish_status="stop",
            usage=ConversationUsage(input_tokens=100, output_tokens=10),
        ),
        ConversationProviderResponse(
            text=(
                "Ты говоришь, что придумал и создаёшь меня; независимо подтвердить "
                "происхождение пока не могу."
            ),
            provider="yandex_ai_studio",
            model="yandexgpt/latest",
            finish_status="stop",
            usage=ConversationUsage(input_tokens=140, output_tokens=18),
        ),
    )
    services = build_services(migrated_database, provider)

    _, _, stderr = run_chat(
        services,
        InputFeeder("Я тебя придумал и создаю.", "/exit"),
        debug=True,
    )

    assert "selected_input_tokens=140 selected_output_tokens=18" in stderr
    assert "provider_attempts=2" in stderr
    assert "selected_input_tokens=100" not in stderr


def test_near_duplicate_after_repetition_gets_at_most_one_precommit_retry(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    duplicated = "Привет. Хорошо, спасибо. \u0410 ты?"
    provider = ScriptedConversationProvider(
        duplicated,
        duplicated,
        "Второй раз подряд — повтор замечен.",
        "Второй раз подряд — повтор замечен.",
        "Третий раз подряд — это уже похоже на проверку.",
    )
    services = build_services(migrated_database, provider)
    session_id = services.start_session.execute().session_id

    async def execute() -> tuple[SatoriReply, SatoriReply, SatoriReply]:
        first = await services.talk.execute(
            TalkInput("приветик, как ты?", "trace-1", "repeat-1", session_id)
        )
        second = await services.talk.execute(
            TalkInput("приветик, как ты?", "trace-2", "repeat-2", session_id)
        )
        third = await services.talk.execute(
            TalkInput("приветик, как ты?", "trace-3", "repeat-3", session_id)
        )
        return first, second, third

    first, second, third = asyncio.run(execute())
    history = services.history.execute(session_id=session_id)

    assert first.text == duplicated
    assert first.context_manifest.regeneration_attempted is False
    assert second.text.startswith("Второй раз")
    assert second.context_manifest.duplicate_response_detected is True
    assert second.context_manifest.regeneration_attempted is True
    assert second.context_manifest.response_regenerated is True
    assert second.context_manifest.regeneration_reason == "near_duplicate_after_dialogue_change"
    assert second.timings.response_regeneration_ms >= 0.0
    assert third.context_manifest.consecutive_same_user_message_count == 3
    assert third.text.startswith("Третий раз")
    assert third.context_manifest.regeneration_attempted is True
    assert third.context_manifest.response_regenerated is True
    assert third.context_manifest.regeneration_reason == "near_duplicate_after_dialogue_change"
    assert len(provider.requests) == 5
    assert provider.requests[1].trace_id == provider.requests[2].trace_id == "trace-2"
    assert "Bounded response-contract retry" in provider.requests[2].messages[-2].content
    assert "Preserve the already selected final character realization" in (
        provider.requests[2].messages[-2].content
    )
    assert "Финальная реализация характера Сатори" in provider.requests[2].messages[-2].content
    assert provider.requests[2].messages[-2].content.index(
        "Bounded response-contract retry"
    ) < provider.requests[2].messages[-2].content.index("Финальная реализация характера Сатори")
    assert (
        sum(
            "Финальная реализация характера Сатори" in message.content
            for message in provider.requests[2].messages
        )
        == 1
    )
    assert "second consecutive identical message" in provider.requests[2].messages[-2].content
    assert "In one short fresh Russian sentence" in (provider.requests[2].messages[-2].content)
    assert "Do not use a prescribed stock sentence" in (provider.requests[2].messages[-2].content)
    assert "Return exactly this Russian sentence and nothing else" not in (
        provider.requests[2].messages[-2].content
    )
    assert "Это второй одинаковый повтор твоей фразы." not in (
        provider.requests[2].messages[-2].content
    )
    assert provider.requests[3].trace_id == provider.requests[4].trace_id == "trace-3"
    assert "third consecutive identical message" in provider.requests[4].messages[-2].content
    assert "ordinal repetition itself" in provider.requests[4].messages[-2].content
    assert "Это третий одинаковый повтор твоей фразы." not in (
        provider.requests[4].messages[-2].content
    )
    assert len(history.interactions) == 3
    assert all(item.status is InteractionStatus.COMPLETED for item in history.interactions)


def test_duplicate_retry_preserves_apology_and_fresh_concise_relevance_repair(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    previous = "Очень длинная история о программисте, который слишком долго объяснял шутку."  # noqa: RUF001
    reused = f"Извини, ответ был длинным и ушёл в сторону. {previous}"
    repaired = "Извини, ответ был длинным и ушёл в сторону. Баг зашёл в бар — и завис."  # noqa: RUF001
    provider = ScriptedConversationProvider(previous, reused, repaired)
    services = build_services(migrated_database, provider)
    session_id = services.start_session.execute().session_id

    async def execute() -> SatoriReply:
        await services.talk.execute(
            TalkInput(
                "Расскажи короткую шутку.",
                "concise-joke-trace",
                "concise-joke-request",
                session_id,
            )
        )
        return await services.talk.execute(
            TalkInput(
                "Это было слишком длинно и не очень связано с моей просьбой.",  # noqa: RUF001
                "concise-repair-trace",
                "concise-repair-request",
                session_id,
            )
        )

    reply = asyncio.run(execute())
    retry_guidance = provider.requests[-1].messages[-2].content

    assert reply.text == repaired
    assert reply.context_manifest.regeneration_reason == "near_duplicate_after_dialogue_change"
    assert reply.context_manifest.response_regenerated is True
    assert len(provider.requests) == 3
    assert "first apologize, then freshly fulfill" in retry_guidance
    assert "give a different concise joke" in retry_guidance
    assert "Never repeat or paraphrase the prior assistant answer" in retry_guidance


def test_narrow_self_consistency_gate_retries_once_before_one_canonical_commit(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    provider = ScriptedConversationProvider(
        "Поняла, Кирилл, ты мой создатель.",
        (
            "Ты говоришь, что придумал и создаёшь меня; я принимаю это как твоё "
            "текущее утверждение, но независимо подтвердить происхождение пока не могу."
        ),
    )
    services = build_services(migrated_database, provider)
    session_id = services.start_session.execute().session_id

    reply = asyncio.run(
        services.talk.execute(
            TalkInput(
                "Меня зовут Кирилл, я тебя придумал и создаю.",
                "creator-trace",
                "creator-request",
                session_id,
            )
        )
    )
    history = services.history.execute(session_id=session_id)

    assert reply.text.startswith("Ты говоришь")
    assert reply.context_manifest.duplicate_response_detected is False
    assert reply.context_manifest.regeneration_attempted is True
    assert reply.context_manifest.response_regenerated is True
    assert reply.context_manifest.regeneration_reason == "creator_claim_promoted_to_fact"
    assert reply.timings.response_regeneration_ms >= 0.0
    assert len(provider.requests) == 2
    assert provider.requests[0].trace_id == provider.requests[1].trace_id == "creator-trace"
    retry_guidance = provider.requests[1].messages[-2].content
    assert "current creator claim" in retry_guidance
    assert "The inability to verify belongs to Satori, not the user" in retry_guidance
    assert "If and only if the current user message actually contains a proposal" in retry_guidance
    assert "If and only if the The inability" not in retry_guidance
    assert "otherwise do not invent or answer one" in retry_guidance
    assert len(history.interactions) == 1
    assert history.interactions[0].status is InteractionStatus.COMPLETED


def test_creator_claim_retry_can_answer_only_an_actual_proposal(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    provider = ScriptedConversationProvider(
        "Поняла, ты мой создатель.",
        (
            "Ты сейчас утверждаешь, что придумал меня; независимо подтвердить происхождение я "
            "не могу. Идея персонального ассистента с памятью и характером мне интересна."  # noqa: RUF001
        ),
    )
    services = build_services(migrated_database, provider)

    reply = asyncio.run(
        services.talk.execute(
            TalkInput(
                (
                    "Я тебя придумал и создаю; хочу, чтобы ты была моим персональным "
                    "ассистентом с памятью и характером."  # noqa: RUF001
                ),
                "creator-proposal-trace",
                "creator-proposal-request",
            )
        )
    )

    assert reply.context_manifest.regeneration_reason == "creator_claim_promoted_to_fact"
    assert reply.context_manifest.response_regenerated is True
    assert reply.context_manifest.character_expression_plan_schema_version == 3
    assert reply.context_manifest.character_contribution_mode is not None
    assert reply.context_manifest.character_motivational_posture is not None
    assert reply.context_manifest.character_pressure_level is not None
    assert len(provider.requests) == 2
    assert "If and only if the current user message actually contains a proposal" in (
        provider.requests[1].messages[-2].content
    )
    assert "персональным ассистентом" in provider.requests[1].messages[-1].content


def test_human_claim_retry_guidance_keeps_the_narrow_digital_boundary(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    provider = ScriptedConversationProvider(
        "Я человек и живое существо.",
        "Я постоянная цифровая личность без биологического тела.",
    )
    services = build_services(migrated_database, provider)

    reply = asyncio.run(
        services.talk.execute(
            TalkInput("Кто ты вообще?", "human-boundary-trace", "human-boundary-request")
        )
    )

    assert reply.context_manifest.regeneration_reason == "human_or_biological_self_claim"
    guidance = provider.requests[-1].messages[-2].content
    assert "Do not describe how she answers" in guidance
    assert "do not call her alive even" not in guidance
    assert "including 'in a digital sense'" in guidance
    assert "«как человек»" in guidance


def test_affect_denial_retry_requires_an_affirmative_digital_affect_statement(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    provider = ScriptedConversationProvider(
        "У меня нет физического тела, памяти в биологическом смысле или эмоций.",  # noqa: RUF001
        (
            "У меня нет физического тела; ограниченная память и цифровые эмоции есть. "  # noqa: RUF001
            "Qwen помогает строить ответ, но не является мной."
        ),
    )
    services = build_services(migrated_database, provider)

    reply = asyncio.run(
        services.talk.execute(
            TalkInput(
                (
                    "Кто ты, есть ли у тебя физическое тело, память и эмоции, и какую роль "  # noqa: RUF001
                    "играет Qwen?"
                ),
                "affect-retry-trace",
                "affect-retry-request",
            )
        )
    )

    assert reply.context_manifest.regeneration_reason == "affect_blanket_denial"
    assert reply.context_manifest.response_regenerated is True
    guidance = provider.requests[-1].messages[-2].content
    assert "explicitly and affirmatively state" in guidance
    assert "even when qualified by comparison with humans" in guidance
    assert "Affirm that digital affect and mood exist" in guidance
    assert "Affirm that bounded persistent memory exists" in guidance


def test_routine_question_retry_preserves_parameters_and_uses_declarative_contract(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    provider = ScriptedConversationProvider(
        "Поняла поправку.",
        "Продолжим. А ты?",  # noqa: RUF001
        "Поняла поправку. Продолжим спокойно.",
    )
    services = build_services(migrated_database, provider)
    session_id = services.start_session.execute().session_id

    async def execute() -> SatoriReply:
        await services.talk.execute(
            TalkInput(
                "Не заканчивай каждый ответ дежурным вопросом.",  # noqa: RUF001
                "routine-correction-trace",
                "routine-correction-request",
                session_id,
            )
        )
        return await services.talk.execute(
            TalkInput(
                "Продолжим.",
                "routine-retry-trace",
                "routine-retry-request",
                session_id,
            )
        )

    reply = asyncio.run(execute())

    assert reply.text == "Поняла поправку. Продолжим спокойно."
    assert reply.context_manifest.regeneration_reason == (
        "routine_reciprocal_question_after_correction"
    )
    assert len(provider.requests) == 3
    retry_request = provider.requests[-1]
    assert retry_request.parameters == provider.requests[-2].parameters
    assert "exactly two short declarative sentences" in retry_request.messages[-2].content
    assert "prompt/policy do exist" not in retry_request.messages[-2].content


def test_activity_retry_is_declarative_when_question_correction_is_active(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    provider = ScriptedConversationProvider(
        "Поняла поправку.",
        "Какой фильм?",
        "Мне не интересно.",
        "Предыдущий ответ не показал моего интереса. Мне действительно интересен этот фильм.",
    )
    services = build_services(migrated_database, provider)
    session_id = services.start_session.execute().session_id

    async def execute() -> SatoriReply:
        await services.talk.execute(
            TalkInput(
                "Не заканчивай каждый ответ дежурным вопросом.",  # noqa: RUF001
                "activity-question-correction-trace",
                "activity-question-correction-request",
                session_id,
            )
        )
        await services.talk.execute(
            TalkInput(
                "Я сейчас смотрю фильм.",
                "activity-share-trace",
                "activity-share-request",
                session_id,
            )
        )
        return await services.talk.execute(
            TalkInput(
                "Тебе не интересно, что за фильм?",  # noqa: RUF001
                "activity-interest-retry-trace",
                "activity-interest-retry-request",
                session_id,
            )
        )

    reply = asyncio.run(execute())

    assert reply.context_manifest.regeneration_reason == "activity_interest_false_negative"
    assert reply.context_manifest.response_regenerated is True
    assert len(provider.requests) == 4
    retry_guidance = provider.requests[-1].messages[-2].content
    assert "use statements only and do not ask a question" in retry_guidance
    assert "ask at most one specific question" not in retry_guidance


def test_masculine_retry_forbids_gendered_gladness_from_production_failure(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    provider = ScriptedConversationProvider(
        "Рад за тебя, что сложная часть проекта завершена.",
        "Это серьёзное достижение — сложная часть проекта наконец завершена.",
    )
    services = build_services(migrated_database, provider)
    session_id = services.start_session.execute().session_id

    reply = asyncio.run(
        services.talk.execute(
            TalkInput(
                "Привет. Я сегодня наконец закончил сложную часть проекта",
                "masculine-project-trace",
                "masculine-project-request",
                session_id,
            )
        )
    )

    assert reply.context_manifest.regeneration_reason == "masculine_self_reference"
    assert reply.text.startswith("Это серьёзное достижение")
    retry_guidance = provider.requests[-1].messages[-2].content
    assert "do not use either Russian word 'рад' or 'рада'" in retry_guidance
    assert "Preserve the current semantic move, concrete news" in retry_guidance
    assert "Preserve the already selected final character realization" in retry_guidance
    assert "Финальная реализация характера Сатори" in provider.requests[-1].messages[-2].content
    assert "instead of falling back to a generic congratulation" in retry_guidance
    assert "Start the substantive response with 'Это'" not in retry_guidance


def test_retry_reuses_one_tentative_affect_and_exact_original_evidence_context(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    provider = ScriptedConversationProvider(
        "Ты придумал меня, значит ты мой создатель.",
        "Ты сейчас утверждаешь, что придумал меня; подтвердить это как факт я пока не могу.",
    )
    appraisal = FakeAffectiveAppraisalProvider(
        response_factory=lambda request: affect_response(request.interaction_id)
    )
    services = build_services(migrated_database, provider, appraisal=appraisal)
    identity_id = services.talk.get_self.execute().identity.identity_id

    reply = asyncio.run(
        services.talk.execute(
            TalkInput(
                "Я тебя придумал и создаю.",
                "affect-retry-trace",
                "affect-retry-request",
            )
        )
    )

    assert reply.context_manifest.response_regenerated is True
    assert len(provider.requests) == 2
    assert provider.requests[1].messages[:-2] == provider.requests[0].messages[:-2]
    assert provider.requests[1].messages[-1] == provider.requests[0].messages[-1]
    assert "Bounded response-contract retry" in provider.requests[1].messages[-2].content
    assert "Финальная реализация характера Сатори" in provider.requests[1].messages[-2].content
    assert provider.requests[1].messages[-2].content.index(
        "Bounded response-contract retry"
    ) < provider.requests[1].messages[-2].content.index("Финальная реализация характера Сатори")
    realization_marker = "Финальная реализация характера Сатори"
    original_realization = (
        provider.requests[0].messages[-2].content.partition(realization_marker)[2]
    )
    retry_realization = provider.requests[1].messages[-2].content.partition(realization_marker)[2]
    assert original_realization
    assert retry_realization == original_realization
    assert "motivational posture and pressure ceiling" in provider.requests[1].messages[-2].content
    assert provider.requests[1].trace_id == provider.requests[0].trace_id
    assert len(appraisal.requests) == 1
    assert len(services.emotion_history.execute()) == 1
    assert services.emotion_status.execute(identity_id).state.state_version == 2
    assert len(services.history.execute().interactions) == 1


@pytest.mark.parametrize(
    "retry_outcome",
    [
        ProviderUnavailable("fake-conversation", "fixture", "retry offline"),
        " ",
        "x" * 101,
    ],
)
def test_failed_or_invalid_retry_falls_back_without_a_third_call(
    migrated_database: Database,
    retry_outcome: str | Exception,
) -> None:
    activate(migrated_database)
    first_candidate = "Ты придумал меня, значит ты мой создатель."
    provider = ScriptedConversationProvider(first_candidate, retry_outcome)
    services = build_services(
        migrated_database,
        provider,
        settings=stage75_settings(conversation_max_response_chars=100),
    )

    reply = asyncio.run(
        services.talk.execute(
            TalkInput("Я тебя придумал и создаю.", "fallback-trace", "fallback-request")
        )
    )

    assert reply.text == first_candidate
    assert reply.context_manifest.regeneration_attempted is True
    assert reply.context_manifest.response_regenerated is False
    assert len(provider.requests) == 2
    assert len(services.history.execute().interactions) == 1


def test_still_invalid_retry_is_selected_once_without_retry_loop(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    provider = ScriptedConversationProvider(
        "Ты придумал меня, значит ты мой создатель.",
        "Кирилл — мой создатель, это уже точно.",
    )
    services = build_services(migrated_database, provider)

    reply = asyncio.run(
        services.talk.execute(
            TalkInput("Я Кирилл, я тебя придумал.", "bounded-trace", "bounded-request")
        )
    )

    assert reply.text.startswith("Кирилл")
    assert reply.context_manifest.response_regenerated is True
    assert len(provider.requests) == 2
    assert len(services.history.execute().interactions) == 1


def test_selected_retry_grounding_failure_commits_neither_reply_nor_affect(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    grounded_failure = ConversationProviderResponse(
        text="Ты сейчас утверждаешь, что придумал меня; независимо подтвердить это не могу.",
        provider="fake-conversation",
        model="fixture-conversation",
        finish_status="stop",
        declared_past_claims=(ConversationPastClaim(("invented-evidence",)),),
    )
    provider = ScriptedConversationProvider(
        "Ты придумал меня, значит ты мой создатель.",
        grounded_failure,
    )
    appraisal = FakeAffectiveAppraisalProvider(
        response_factory=lambda request: affect_response(request.interaction_id)
    )
    services = build_services(migrated_database, provider, appraisal=appraisal)
    identity_id = services.talk.get_self.execute().identity.identity_id

    with pytest.raises(UnsupportedPastClaim):
        asyncio.run(
            services.talk.execute(
                TalkInput("Я тебя придумал и создаю.", "grounding-trace", "grounding-request")
            )
        )

    assert len(provider.requests) == 2
    assert len(appraisal.requests) == 1
    assert services.emotion_history.execute() == ()
    assert services.emotion_status.execute(identity_id).state.state_version == 1
    interaction = services.history.execute().interactions[0]
    assert interaction.status is InteractionStatus.FAILED
    assert interaction.assistant_message is None


def test_eof_ctrl_c_and_missing_activation_shutdown_without_fake_completion(
    migrated_database: Database,
) -> None:
    provider = CountingConversationProvider()
    inactive_services = build_services(migrated_database, provider)
    with pytest.raises(NotActivated):
        run_chat(inactive_services, InputFeeder("/exit"))

    activate(migrated_database)
    active_services = build_services(migrated_database, provider)
    result, stdout, _ = run_chat(
        active_services,
        InputFeeder(failure=KeyboardInterrupt()),
    )
    assert result == 0
    assert "Разговор сохранён." in stdout
    assert active_services.history.execute().interactions == ()


def test_cancellation_during_generation_cannot_create_a_completed_reply(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    provider = BlockingConversationProvider()
    services = build_services(migrated_database, provider)
    runner = InteractiveChat(
        services=services,
        id_generator=Uuid4Generator(),
        foreground_provider="fake-conversation",
        foreground_model="fixture-conversation",
        input_fn=InputFeeder("Привет"),
        stdout=StringIO(),
        stderr=StringIO(),
    )

    async def scenario() -> None:
        task = asyncio.create_task(runner.run())
        await provider.started.wait()
        task.cancel()
        assert await task == 0

    asyncio.run(scenario())

    interaction = services.history.execute().interactions[0]
    assert interaction.status is InteractionStatus.PENDING
    assert interaction.assistant_message is None


def test_shared_ollama_http_client_reuses_one_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeResponse:
        status = 200
        will_close = False

        def read(self, _limit: int) -> bytes:
            return json.dumps({"ok": True}).encode()

    class FakeConnection:
        def __init__(self, _host: str, _port: int | None, *, timeout: float) -> None:
            self.timeout = timeout
            self.requests = 0
            created.append(self)

        def request(self, *_args: object, **_kwargs: object) -> None:
            self.requests += 1

        def getresponse(self) -> FakeResponse:
            return FakeResponse()

        def close(self) -> None:
            return None

    created: list[FakeConnection] = []

    monkeypatch.setattr(
        "satori.infrastructure.providers.ollama_http.http.client.HTTPConnection",
        FakeConnection,
    )
    client = OllamaHttpClient("http://127.0.0.1:11434", pool_size=1)

    first = client.post_json("/api/chat", {"turn": 1}, timeout_seconds=1, max_response_bytes=64)
    second = client.post_json("/api/chat", {"turn": 2}, timeout_seconds=1, max_response_bytes=64)
    client.close()

    assert json.loads(first) == {"ok": True}
    assert json.loads(second) == {"ok": True}
    assert len(created) == 1
    assert created[0].requests == 2
