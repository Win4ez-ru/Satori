"""Stateless conversation orchestration and provider replacement tests."""

import asyncio
import json
from datetime import UTC, datetime
from io import StringIO

import pytest
from sqlalchemy import text

from satori.application.conversation.contracts import SatoriReply, TalkInput
from satori.application.conversation.errors import ConversationInputError
from satori.composition import build_conversation_services, build_initial_self_services
from satori.config import Environment, LogLevel, Settings
from satori.core.conversation import (
    ConversationProviderFailureReason,
    ConversationProviderResponse,
    ConversationUsage,
    GenerationFailed,
    InvalidProviderResponse,
    ProviderUnavailable,
)
from satori.core.episode import EpisodeFormationProposal, EpisodeFormationProviderResponse
from satori.domain.errors import NotActivated
from satori.domain.initial_self import InitialSelfSnapshot
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.seeds.loader import JsonSeedLoader
from satori.observability.logging import bind_trace_id, configure_logging
from tests.fakes import (
    FakeConversationProvider,
    FakeEpisodeFormationProvider,
    FrozenClock,
    SequenceIdGenerator,
)

ACTIVATION_TIME = datetime(2026, 7, 28, 10, 30, tzinfo=UTC)


def conversation_settings(**overrides: object) -> Settings:
    """Return isolated typed settings with optional Stage 3 limit overrides."""

    values: dict[str, object] = {
        "environment": Environment.TEST,
        "database_url": "sqlite+pysqlite:///:memory:",
        "log_level": LogLevel.INFO,
    }
    values.update(overrides)
    return Settings.model_validate(values)


def activate(database: Database) -> InitialSelfSnapshot:
    """Activate one deterministic Stage 2 identity for conversation tests."""

    services = build_initial_self_services(
        database,
        clock=FrozenClock(ACTIVATION_TIME),
        id_generator=SequenceIdGenerator("identity-conversation", "audit-conversation"),
    )
    return services.activate.execute(
        JsonSeedLoader().load_canonical(),
        trace_id="trace-activation",
    )


def success_response(
    text: str,
    *,
    provider: str = "fake-a",
    model: str = "fixture-a",
) -> ConversationProviderResponse:
    """Create one controlled provider-neutral success result."""

    return ConversationProviderResponse(
        text=text,
        provider=provider,
        model=model,
        finish_status="stop",
        usage=ConversationUsage(input_tokens=120, output_tokens=24),
    )


def skip_episode_provider() -> FakeEpisodeFormationProvider:
    return FakeEpisodeFormationProvider(
        response=EpisodeFormationProviderResponse(
            proposal=EpisodeFormationProposal(
                schema_version=1,
                should_create=False,
                summary=None,
                importance=None,
                confidence=None,
                evidence=(),
            ),
            provider="fake-episode",
            model="fixture-episode",
            formation_method="fixture.v1",
        )
    )


def talk(
    database: Database,
    provider: FakeConversationProvider,
    *,
    text: str = "Привет, Сатори",
    trace_id: str = "trace-talk",
    settings: Settings | None = None,
    client_request_id: str = "request-talk",
) -> SatoriReply:
    """Run the production use case with a deterministic provider."""

    initial_self = build_initial_self_services(database)
    conversation = build_conversation_services(
        database,
        initial_self,
        provider,
        skip_episode_provider(),
        settings or conversation_settings(),
        clock=FrozenClock(ACTIVATION_TIME),
        id_generator=SequenceIdGenerator(
            *(f"{client_request_id}-conversation-{index}" for index in range(100))
        ),
    )
    with bind_trace_id(trace_id):
        return asyncio.run(
            conversation.talk.execute(
                TalkInput(
                    user_text=text,
                    trace_id=trace_id,
                    client_request_id=client_request_id,
                )
            )
        )


def test_talk_before_activation_is_typed_and_has_no_side_effect(
    migrated_database: Database,
) -> None:
    """Conversation reads never auto-activate a fresh installation."""

    provider = FakeConversationProvider(response=success_response("unused"))

    with pytest.raises(NotActivated):
        talk(migrated_database, provider)

    assert provider.requests == []
    with pytest.raises(NotActivated):
        build_initial_self_services(migrated_database).get_self.execute()


def test_conversation_returns_validated_reply_without_mutating_self(
    migrated_database: Database,
) -> None:
    """The entire persistent Stage 2 snapshot remains bit-for-bit equal after a turn."""

    expected = activate(migrated_database)
    provider = FakeConversationProvider(response=success_response("Привет. Я здесь."))

    reply = talk(migrated_database, provider)
    actual = build_initial_self_services(migrated_database).get_self.execute()

    assert reply.text == "Привет. Я здесь."
    assert reply.provider == "fake-a"
    assert reply.context_manifest.character_context_schema_version == 16
    assert reply.cognition_trace is not None
    assert reply.context_manifest.cognition_pipeline_status == "applied"
    assert reply.context_manifest.cognition_intent_registry_version == 2
    assert reply.context_manifest.cognition_template_id == "satori.cognition.response-substance"
    assert reply.context_manifest.cognition_template_schema_version == 3
    assert "cognition_response_strategy" not in reply.context_manifest.included_sections
    assert "character_delivery_decision" in reply.context_manifest.included_sections
    assert reply.context_manifest.character_delivery_decision_schema_version == 4
    assert reply.context_manifest.character_presence_projection_schema_version == 2
    assert any(
        "Trusted current-turn presence Сатори" in message.content
        for message in provider.requests[0].messages
    )
    assert all(
        "Единая request-local режиссура реплики Сатори" not in message.content
        for message in provider.requests[0].messages
    )
    assert actual == expected
    assert len(provider.requests) == 1
    with migrated_database.engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM audit_events")).scalar_one() == 1


def test_conversation_does_not_rewrite_provider_text_for_character_compliance(
    migrated_database: Database,
) -> None:
    """Behavioral calibration changes input context, never filters or rewrites provider output."""

    activate(migrated_database)
    raw_provider_text = "У меня нет эмоций — намеренно плохой fixture-ответ."  # noqa: RUF001
    provider = FakeConversationProvider(response=success_response(raw_provider_text))

    reply = talk(
        migrated_database,
        provider,
        text="Что ты чувствуешь?",
        client_request_id="request-no-character-rewrite",
    )

    assert reply.text == raw_provider_text


def test_golden_provider_replacement_preserves_character_basis_and_state(
    migrated_database: Database,
) -> None:
    """Provider A and B may phrase differently but receive the same persistent character."""

    before = activate(migrated_database)
    provider_a = FakeConversationProvider(response=success_response("Ответ A"))
    provider_b = FakeConversationProvider(
        response=success_response("Ответ B", provider="fake-b", model="fixture-b")
    )

    reply_a = talk(
        migrated_database,
        provider_a,
        trace_id="trace-provider-swap",
        client_request_id="request-provider-a",
    )
    reply_b = talk(
        migrated_database,
        provider_b,
        trace_id="trace-provider-swap",
        client_request_id="request-provider-b",
    )
    after = build_initial_self_services(migrated_database).get_self.execute()

    assert reply_a.text != reply_b.text
    assert provider_a.requests[0].messages == provider_b.requests[0].messages
    assert provider_a.requests[0].context_schema_version == 16
    assert after == before


def test_provider_failure_is_typed_logged_and_does_not_mutate_or_leak_text(
    migrated_database: Database,
) -> None:
    """Normal provider outage has a typed outcome and metadata-only structured log."""

    before = activate(migrated_database)
    stream = StringIO()
    configure_logging(LogLevel.INFO, stream=stream)
    provider = FakeConversationProvider(
        error=ProviderUnavailable(
            "fake-offline",
            "fixture",
            "provider unavailable",
            reason=ConversationProviderFailureReason.TRANSPORT_UNAVAILABLE,
        )
    )
    private_text = "мой приватный текст"

    with pytest.raises(ProviderUnavailable):
        talk(migrated_database, provider, text=private_text, trace_id="trace-failure")

    after = build_initial_self_services(migrated_database).get_self.execute()
    records = [
        record
        for line in stream.getvalue().splitlines()
        if (record := json.loads(line))["logger"] == "satori.conversation"
    ]
    assert [record["message"] for record in records] == [
        "conversation_attempted",
        "conversation_failed",
    ]
    assert records[-1]["fields"]["provider"] == "fake-offline"
    assert records[-1]["fields"]["error_type"] == "ProviderUnavailable"
    assert records[-1]["fields"]["failure_reason"] == "transport_unavailable"
    assert private_text not in stream.getvalue()
    with migrated_database.engine.connect() as connection:
        failed = connection.execute(
            text(
                "SELECT status, failure_kind, failure_reason, provider, model "
                "FROM conversation_interactions"
            )
        ).one()
    assert tuple(failed) == (
        "failed",
        "ProviderUnavailable",
        "transport_unavailable",
        "fake-offline",
        "fixture",
    )
    assert after == before


def test_success_observability_has_metadata_without_conversation_content(
    migrated_database: Database,
) -> None:
    """Success logs provider/context/usage metadata, never full input or response."""

    activate(migrated_database)
    stream = StringIO()
    configure_logging(LogLevel.INFO, stream=stream)
    provider = FakeConversationProvider(response=success_response("приватный ответ"))

    talk(
        migrated_database,
        provider,
        text="приватный вопрос",
        trace_id="trace-success",
    )

    records = [
        record
        for line in stream.getvalue().splitlines()
        if (record := json.loads(line))["logger"] == "satori.conversation"
    ]
    assert [record["message"] for record in records] == [
        "conversation_attempted",
        "interaction_persisted",
        "conversation_succeeded",
    ]
    success = records[-1]
    assert success["trace_id"] == "trace-success"
    assert success["fields"]["operation"] == "conversation"
    assert success["fields"]["provider"] == "fake-a"
    assert success["fields"]["model"] == "fixture-a"
    assert success["fields"]["context_schema_version"] == 16
    assert success["fields"]["cognition_pipeline_status"] == "applied"
    assert success["fields"]["cognition_position_stance"] == "answer"
    assert success["fields"]["input_tokens"] == 120
    assert success["fields"]["output_tokens"] == 24
    assert "приватный вопрос" not in stream.getvalue()
    assert "приватный ответ" not in stream.getvalue()


def test_untyped_provider_exception_is_wrapped_without_leaking_error_text(
    migrated_database: Database,
) -> None:
    """Unexpected adapter failures cross neither as raw errors nor raw log text."""

    activate(migrated_database)
    stream = StringIO()
    configure_logging(LogLevel.INFO, stream=stream)
    private_error_text = "vendor internals containing private user input"
    provider = FakeConversationProvider(error=RuntimeError(private_error_text))

    with pytest.raises(GenerationFailed) as error:
        talk(migrated_database, provider)

    assert isinstance(error.value.__cause__, RuntimeError)
    assert error.value.reason is ConversationProviderFailureReason.ADAPTER_CONTRACT_VIOLATION
    assert private_error_text not in stream.getvalue()
    with migrated_database.engine.connect() as connection:
        failed = connection.execute(
            text(
                "SELECT failure_kind, failure_reason, provider, model "
                "FROM conversation_interactions"
            )
        ).one()
    assert tuple(failed) == (
        "GenerationFailed",
        "adapter_contract_violation",
        "unknown",
        "unknown",
    )


def test_provider_error_requires_closed_failure_reason() -> None:
    with pytest.raises(ValueError, match="ConversationProviderFailureReason"):
        GenerationFailed(
            "fixture-provider",
            "fixture-model",
            "safe detail",
            reason="free-form-reason",  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("text", ["", "   "])
def test_blank_user_text_is_rejected_before_provider(
    migrated_database: Database,
    text: str,
) -> None:
    """A blank turn is not a provider request."""

    activate(migrated_database)
    provider = FakeConversationProvider(response=success_response("unused"))

    with pytest.raises(ValueError, match="user_text must not be blank"):
        talk(migrated_database, provider, text=text)

    assert provider.requests == []


def test_input_and_response_length_policies_are_explicit(
    migrated_database: Database,
) -> None:
    """Configured bounds reject oversize input/output instead of truncating silently."""

    activate(migrated_database)
    provider = FakeConversationProvider(response=success_response("12345"))
    settings = conversation_settings(
        conversation_max_input_chars=4,
        conversation_max_response_chars=4,
    )

    with pytest.raises(ConversationInputError):
        talk(migrated_database, provider, text="12345", settings=settings)
    assert provider.requests == []

    provider = FakeConversationProvider(response=success_response("12345"))
    with pytest.raises(InvalidProviderResponse):
        talk(migrated_database, provider, text="1234", settings=settings)


def test_empty_provider_text_is_invalid(
    migrated_database: Database,
) -> None:
    """Whitespace-only model output never becomes a Satori reply."""

    activate(migrated_database)
    provider = FakeConversationProvider(response=success_response("   "))

    with pytest.raises(InvalidProviderResponse):
        talk(migrated_database, provider)
