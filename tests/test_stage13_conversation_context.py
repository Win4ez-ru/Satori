"""Stage 13 inclination request, manifest, persistence, and replay projection."""

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from types import TracebackType
from typing import Self, cast

import pytest

from satori.application.conversation.context import (
    CONTEXT_MANIFEST_SCHEMA_VERSION,
    RUNTIME_CHARACTER_CONTEXT_SCHEMA_VERSION,
    CharacterContextComposer,
    ConversationRequestBuilder,
)
from satori.application.conversation.contracts import (
    ConversationContextManifest,
    RuntimeCharacterContext,
    TalkInput,
)
from satori.application.conversation.policy import BEHAVIOR_POLICY_V9
from satori.application.positions.contracts import (
    InclinationContextItem,
    SatoriInclinationsContext,
    SatoriPositionsContext,
)
from satori.application.positions.ports import PositionsUnitOfWork
from satori.application.positions.use_cases import GetSatoriPositions
from satori.composition import build_conversation_services, build_initial_self_services
from satori.core.affect import (
    AffectiveAppraisalProposal,
    AffectiveAppraisalProviderResponse,
    AffectiveAppraisalRequest,
)
from satori.core.conversation import ConversationMessageRole
from satori.core.inclinations import InclinationKind, InclinationStateReference
from satori.domain.initial_self import activate_from_seed
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.seeds.loader import JsonSeedLoader
from tests.fakes import FakeAffectiveAppraisalProvider, FrozenClock
from tests.test_stage4_conversation_memory import (
    activate,
    conversation_provider,
    id_sequence,
    settings,
    skip_episode_provider,
)

AS_OF = datetime(2026, 8, 22, 12, tzinfo=UTC)
IDENTITY_ID = "identity-stage13-context"
INCLINATION_SECTION_PREFIX = "Canonical Satori preferences/interests"


def _runtime_context() -> RuntimeCharacterContext:
    snapshot = activate_from_seed(
        JsonSeedLoader().load_canonical(),
        identity_id=IDENTITY_ID,
        activation_time=datetime(2026, 8, 1, tzinfo=UTC),
    )
    return CharacterContextComposer(
        language_provider="ollama",
        language_model="qwen3:4b-instruct",
    ).compose(snapshot)


def _request_builder() -> ConversationRequestBuilder:
    return ConversationRequestBuilder(
        policy=BEHAVIOR_POLICY_V9,
        max_context_chars=12_000,
        temperature=0.3,
        max_output_tokens=768,
    )


def _available_context() -> SatoriInclinationsContext:
    return SatoriInclinationsContext(
        schema_version=1,
        status="available",
        inclinations=(
            InclinationContextItem(
                inclination_id="inclination-interest-jazz",
                kind="interest",
                topic="джаз",
                alternative_topic=None,
                effective_score=0.18,
                confidence=0.72,
                stability=0.31,
                preferred_topic=None,
            ),
            InclinationContextItem(
                inclination_id="inclination-preference-music",
                kind="preference",
                topic="джаз",
                alternative_topic="рок",
                effective_score=-0.34,
                confidence=0.81,
                stability=0.45,
                preferred_topic="рок",
            ),
        ),
        curiosity_influence=0.18,
    )


def test_available_inclinations_are_one_private_developer_section_with_v16_manifest() -> None:
    request, manifest = _request_builder().build(
        _runtime_context(),
        user_text="Расскажи про джаз",
        trace_id="trace-stage13-available",
        inclination_context=_available_context(),
    )

    sections = tuple(
        message for message in request.messages if INCLINATION_SECTION_PREFIX in message.content
    )
    assert len(sections) == 1
    section = sections[0]
    assert section.role is ConversationMessageRole.DEVELOPER
    payload = json.loads(section.content.splitlines()[-1])

    assert request.context_schema_version == RUNTIME_CHARACTER_CONTEXT_SCHEMA_VERSION == 16
    assert manifest.schema_version == CONTEXT_MANIFEST_SCHEMA_VERSION == 16
    assert manifest.character_context_schema_version == 16
    assert manifest.personality_aggregate_version == 1
    assert manifest.personality_expression_schema_version == 2
    assert manifest.personality_expression_cues == ()
    assert "satori_inclinations" in manifest.included_sections
    assert manifest.inclination_context_status == "available"
    assert manifest.inclination_context_schema_version == 1
    assert manifest.inclination_context_ids == (
        "inclination-interest-jazz",
        "inclination-preference-music",
    )
    assert manifest.inclination_curiosity_influence == pytest.approx(0.18)
    assert manifest.available_past_evidence_ids == manifest.inclination_context_ids

    assert payload["schema_version"] == 1
    assert payload["status"] == "available"
    assert payload["curiosity_influence"] == pytest.approx(0.18)
    assert [item["inclination_id"] for item in payload["inclinations"]] == list(
        manifest.inclination_context_ids
    )
    assert all(
        set(item)
        == {
            "inclination_id",
            "kind",
            "topic",
            "alternative_topic",
            "effective_score",
            "confidence",
            "stability",
            "preferred_topic",
        }
        for item in payload["inclinations"]
    )
    preference = payload["inclinations"][1]
    assert preference["topic"] == "джаз"
    assert preference["alternative_topic"] == "рок"
    assert preference["effective_score"] == pytest.approx(-0.34)
    assert preference["preferred_topic"] == "рок"

    rendered = "\n".join(message.content for message in request.messages)
    for forbidden_key in (
        '"quote":',
        '"evidence":',
        '"evidence_ids":',
        '"source_message_id":',
        '"source_interaction_id":',
        '"content_hash":',
        '"history":',
        '"revisions":',
    ):
        assert forbidden_key not in rendered


@pytest.mark.parametrize(
    ("context", "expected_status"),
    [
        (None, "not_requested"),
        (SatoriInclinationsContext(1, "empty", (), 0.0), "empty"),
    ],
)
def test_empty_and_not_requested_context_have_no_section_or_state_metadata(
    context: SatoriInclinationsContext | None,
    expected_status: str,
) -> None:
    request, manifest = _request_builder().build(
        _runtime_context(),
        user_text="Расскажи про океан",
        trace_id=f"trace-stage13-{expected_status}",
        inclination_context=context,
    )

    assert not any(INCLINATION_SECTION_PREFIX in message.content for message in request.messages)
    assert "satori_inclinations" not in manifest.included_sections
    assert manifest.inclination_context_status == expected_status
    assert manifest.inclination_context_schema_version is None
    assert manifest.inclination_context_ids == ()
    assert manifest.inclination_curiosity_influence == 0.0


class _ReferenceUnitOfWork:
    """Minimal read-only unit exposing exact persistent inclination references."""

    def __init__(self, references: tuple[InclinationStateReference, ...]) -> None:
        self.references = references

    @property
    def positions(self) -> Self:
        return self

    def list_inclination_references(
        self, *, identity_id: str
    ) -> tuple[InclinationStateReference, ...]:
        assert identity_id == IDENTITY_ID
        return self.references

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


def _projection_reader(
    references: tuple[InclinationStateReference, ...],
) -> GetSatoriPositions:
    factory = cast(
        Callable[[], PositionsUnitOfWork],
        lambda: _ReferenceUnitOfWork(references),
    )
    return GetSatoriPositions(unit_of_work_factory=factory)


def test_current_topic_relevance_reaches_request_while_unrelated_turn_stays_empty() -> None:
    references = (
        InclinationStateReference(
            inclination_id="inclination-jazz",
            aggregate_version=3,
            kind=InclinationKind.INTEREST,
            topic="джаз",
            alternative_topic=None,
            score=0.18,
            confidence=0.72,
            stability=0.31,
            state_as_of=AS_OF,
        ),
        InclinationStateReference(
            inclination_id="inclination-space",
            aggregate_version=2,
            kind=InclinationKind.INTEREST,
            topic="космос",
            alternative_topic=None,
            score=0.16,
            confidence=0.70,
            stability=0.25,
            state_as_of=AS_OF,
        ),
    )
    reader = _projection_reader(references)
    relevant = reader.project_inclination_context(
        identity_id=IDENTITY_ID,
        user_text="Расскажи про джаз",
        as_of=AS_OF,
    )
    unrelated = reader.project_inclination_context(
        identity_id=IDENTITY_ID,
        user_text="Расскажи про океанские течения",
        as_of=AS_OF,
    )

    relevant_request, relevant_manifest = _request_builder().build(
        _runtime_context(),
        user_text="Расскажи про джаз",
        trace_id="trace-stage13-relevant",
        inclination_context=relevant,
    )
    unrelated_request, unrelated_manifest = _request_builder().build(
        _runtime_context(),
        user_text="Расскажи про океанские течения",
        trace_id="trace-stage13-unrelated",
        inclination_context=unrelated,
    )

    assert relevant.inclination_ids == ("inclination-jazz",)
    assert relevant_manifest.inclination_context_ids == ("inclination-jazz",)
    assert any(
        INCLINATION_SECTION_PREFIX in message.content for message in relevant_request.messages
    )
    assert unrelated.status == "empty"
    assert unrelated_manifest.inclination_context_status == "empty"
    assert unrelated_manifest.inclination_context_ids == ()
    assert not any(
        INCLINATION_SECTION_PREFIX in message.content for message in unrelated_request.messages
    )


@dataclass(slots=True)
class _CountingPositionsProjection:
    inclination_context: SatoriInclinationsContext
    position_reads: int = 0
    inclination_reads: int = 0

    def project_context(self, *, identity_id: str, user_text: str) -> SatoriPositionsContext:
        assert identity_id
        assert user_text
        self.position_reads += 1
        return SatoriPositionsContext(schema_version=1, status="empty", positions=())

    def project_inclination_context(
        self,
        *,
        identity_id: str,
        user_text: str,
        as_of: datetime,
    ) -> SatoriInclinationsContext:
        assert identity_id
        assert "джаз" in user_text.casefold()
        assert as_of == AS_OF
        self.inclination_reads += 1
        return self.inclination_context


def _appraisal_response(request: AffectiveAppraisalRequest) -> AffectiveAppraisalProviderResponse:
    return AffectiveAppraisalProviderResponse(
        proposal=AffectiveAppraisalProposal(
            schema_version=1,
            pleasantness=0.4,
            activation=0.3,
            novelty=0.2,
            salience=0.7,
            uncertainty=0.05,
            curiosity_signal=0.3,
            interest_signal=0.6,
            humor_signal=0.0,
            concern_signal=0.0,
            frustration_signal=0.0,
            confidence_signal=0.4,
            appraisal_confidence=0.9,
            source_refs=(request.interaction_id,),
            reason_codes=("topic_engagement",),
        ),
        provider="fake-appraisal",
        model="fixture-appraisal",
        appraisal_method="fixture.appraisal.v1",
    )


def _manifest_projection(
    manifest: ConversationContextManifest,
) -> tuple[object, ...]:
    return (
        manifest.schema_version,
        manifest.personality_aggregate_version,
        manifest.personality_expression_schema_version,
        manifest.personality_expression_cues,
        manifest.inclination_context_status,
        manifest.inclination_context_schema_version,
        manifest.inclination_context_ids,
        manifest.inclination_curiosity_influence,
        manifest.available_past_evidence_ids,
    )


def test_metadata_roundtrip_and_replay_preserve_exact_projection_without_extra_calls(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    conversation = conversation_provider("Джаз интересен мне сложной ритмической структурой.")
    appraisal = FakeAffectiveAppraisalProvider(response_factory=_appraisal_response)
    episode = skip_episode_provider()
    services = build_conversation_services(
        migrated_database,
        build_initial_self_services(migrated_database),
        conversation,
        episode,
        settings(str(migrated_database.engine.url)),
        clock=FrozenClock(AS_OF),
        id_generator=id_sequence("stage13-conversation-context"),
        appraisal_provider=appraisal,
    )
    projection = _CountingPositionsProjection(_available_context())
    services.talk.get_positions = cast(GetSatoriPositions, projection)
    command = TalkInput(
        user_text="Расскажи про джаз",
        trace_id="trace-stage13-roundtrip",
        client_request_id="request-stage13-roundtrip",
    )

    first = asyncio.run(services.talk.execute(command))
    stored = services.talk.interaction_log.get(first.interaction_id)
    assert stored is not None
    assert stored.provider_metadata is not None
    metadata = stored.provider_metadata

    assert first.context_manifest.schema_version == 16
    assert metadata.context_schema_version == 16
    assert metadata.context_manifest_schema_version == 16
    assert metadata.personality_aggregate_version == 1
    assert metadata.personality_expression_schema_version == 2
    assert metadata.personality_expression_cues == ()
    assert metadata.inclination_context_status == "available"
    assert metadata.inclination_context_schema_version == 1
    assert metadata.inclination_context_ids == (
        "inclination-interest-jazz",
        "inclination-preference-music",
    )
    assert metadata.inclination_curiosity_influence == pytest.approx(0.18)
    assert "satori_inclinations" in first.context_manifest.included_sections
    assert len(conversation.requests) == 1
    assert len(appraisal.requests) == 1
    assert episode.requests == []
    assert projection.position_reads == 1
    assert projection.inclination_reads == 1

    replayed = asyncio.run(services.talk.execute(command))

    assert replayed.replayed
    assert replayed.text == first.text
    assert _manifest_projection(replayed.context_manifest) == _manifest_projection(
        first.context_manifest
    )
    assert "satori_inclinations" in replayed.context_manifest.included_sections
    assert len(conversation.requests) == 1
    assert len(appraisal.requests) == 1
    assert episode.requests == []
    assert projection.position_reads == 1
    assert projection.inclination_reads == 1
