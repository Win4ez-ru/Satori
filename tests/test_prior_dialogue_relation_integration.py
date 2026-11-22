"""Matched canonical-history relation selection through actual managed Talk.

Every generated reply and relation category here is scripted transport data. No test
asserts a desired Satori utterance or claims semantic or emotional model quality.
"""

# ruff: noqa: RUF001  # Public authored Russian inputs and synthetic transport data.

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest

from satori.application.cognition.use_cases import (
    DeterministicCognitionPlanner,
    SafeCognitionPipeline,
)
from satori.application.conversation.character_agency import (
    CharacterAgencyDecision,
    CharacterAgencyKernel,
)
from satori.application.conversation.contracts import RecentConversationContext
from satori.application.conversation.dialogue_relation import (
    PRIOR_DIALOGUE_RELATION_OPERATION_MARKER,
)
from satori.application.conversation.policy import BEHAVIOR_POLICY_V35
from satori.application.relationship.contracts import RelationshipExpressionContext
from satori.application.source_policy.dispatch import admit_provider_dispatch
from satori.composition import (
    ConversationServices,
    build_conversation_services,
    build_initial_self_services,
)
from satori.core.conversation import (
    ConversationMessageRole,
    ConversationProviderFailureReason,
    ConversationProviderRequest,
    ConversationProviderResponse,
    ConversationUsage,
    ProviderUnavailable,
)
from satori.infrastructure.persistence.database import Database, create_database
from satori.infrastructure.persistence.migrations import upgrade_database
from satori.infrastructure.source_policy.installation import enroll_installation
from satori.infrastructure.source_policy.runtime import SourcePolicyRuntime
from tests.fakes import FakeAffectiveAppraisalProvider, FrozenClock, SequenceIdGenerator
from tests.prior_dialogue_relation_diagnostic import (
    EXCHANGE_INPUT,
    EXCHANGE_MOVE,
    NATURAL_SHIFT,
    PRIOR_DIALOGUE_RELATION_DIAGNOSTIC,
    PRIOR_DIALOGUE_RELATION_DIAGNOSTIC_VERSION,
    RELEVANT_SHIFT,
    PriorRelationScenario,
    RelationKind,
)
from tests.test_checkpoint142_character_delivery_v9 import _relationship as relationship_fixture
from tests.test_checkpoint143_character_agency_integration import (
    InvalidCompletionPlanner,
    _assert_cognition_preserved,
    _talk,
)
from tests.test_checkpoint143_v34_affect_expression import appraisal_response, scalar
from tests.test_checkpoint143_v37_context_use import NOW
from tests.test_conversation import activate, conversation_settings, skip_episode_provider
from tests.test_current_position_integration import _exclude_prior

DOMAIN_TABLES = (
    "affective_transitions",
    "relationship_transitions",
    "satori_positions",
    "satori_inclinations",
    "personality_revisions",
)


def is_relation(request: ConversationProviderRequest) -> bool:
    return any(
        message.role is ConversationMessageRole.DEVELOPER
        and message.content.startswith(PRIOR_DIALOGUE_RELATION_OPERATION_MARKER)
        for message in request.messages
    )


@dataclass(slots=True)
class RelationProvider:
    """Real dispatch-admission boundary with synthetic categorical/foreground data."""

    kind: RelationKind = "shift_from_prior_move"
    outcome: str = "valid"
    prior_quote: str = EXCHANGE_MOVE
    current_quote: str = RELEVANT_SHIFT
    foreground_texts: tuple[str, ...] = ()
    requests: list[ConversationProviderRequest] = field(default_factory=list, init=False)
    events: list[str] = field(default_factory=list, init=False)

    def for_relation(self) -> list[ConversationProviderRequest]:
        return [request for request in self.requests if is_relation(request)]

    def for_foreground(self) -> list[ConversationProviderRequest]:
        return [request for request in self.requests if not is_relation(request)]

    async def generate(
        self, request: ConversationProviderRequest, /
    ) -> ConversationProviderResponse:
        admit_provider_dispatch()
        self.requests.append(request)
        semantic = is_relation(request)
        self.events.append("relation" if semantic else "foreground")
        if semantic:
            if self.outcome == "unavailable":
                raise ProviderUnavailable(
                    provider="fixture",
                    model="no-network",
                    message="synthetic relation outage",
                    reason=ConversationProviderFailureReason.TRANSPORT_UNAVAILABLE,
                )
            if self.outcome == "unexpected":
                raise RuntimeError("synthetic unexpected relation failure")
            body = json.dumps(
                {
                    "schema_version": 1,
                    "kind": self.kind,
                    "uncertainty": "material" if self.outcome == "material" else "supported",
                    "prior_assistant_quote": (
                        ""
                        if self.kind == "abstain"
                        else "FOREIGN-QUOTE"
                        if self.outcome == "foreign_quote"
                        else self.prior_quote
                    ),
                    "current_user_quote": "" if self.kind == "abstain" else self.current_quote,
                },
                ensure_ascii=False,
            )
            if self.outcome == "invalid":
                body = "not JSON; no repair or retry"
        else:
            index = len(self.for_foreground()) - 1
            body = self.foreground_texts[index] if index < len(self.foreground_texts) else "WIRE"
        return ConversationProviderResponse(
            body,
            "fixture",
            "no-network",
            "stop",
            usage=ConversationUsage(input_tokens=100, output_tokens=8),
        )


@dataclass(slots=True)
class RelationCase:
    database: Database
    directory: Path
    runtime: SourcePolicyRuntime
    services: ConversationServices
    provider: RelationProvider
    appraisal: FakeAffectiveAppraisalProvider


def services_for(
    database: Database,
    directory: Path,
    provider: RelationProvider,
    *,
    enabled: bool,
    counterparty: str = "local-default",
    prefix: str = "relation",
) -> tuple[ConversationServices, FakeAffectiveAppraisalProvider]:
    appraisal = FakeAffectiveAppraisalProvider(
        response_factory=lambda request: appraisal_response(request, neutral=True)
    )
    services = build_conversation_services(
        database,
        build_initial_self_services(database),
        provider,
        skip_episode_provider(),
        conversation_settings(
            source_policy_directory=directory,
            conversation_prior_dialogue_relation_enabled=enabled,
            conversation_current_position_enabled=False,
            default_counterparty_id=counterparty,
        ),
        clock=FrozenClock(NOW),
        id_generator=SequenceIdGenerator(*(f"{prefix}-{index:04d}" for index in range(300))),
        appraisal_provider=appraisal,
        behavior_policy=BEHAVIOR_POLICY_V35,
        source_policy_required=True,
    )
    services.talk.monotonic = lambda: 0.0
    services.talk.cognition_pipeline = SafeCognitionPipeline(
        planner=DeterministicCognitionPlanner(intent_registry_version=2, monotonic=lambda: 0.0),
        fallback=DeterministicCognitionPlanner(intent_registry_version=2, monotonic=lambda: 0.0),
    )
    return services, appraisal


@dataclass(slots=True)
class RelationCaseFactory:
    root: Path
    project_root: Path
    databases: list[Database] = field(default_factory=list)

    def create(
        self, name: str, *, enabled: bool, provider: RelationProvider | None = None
    ) -> RelationCase:
        database_url = f"sqlite+pysqlite:///{self.root / (name + '.db')}"
        upgrade_database(database_url, config_path=self.project_root / "alembic.ini")
        database = create_database(database_url)
        self.databases.append(database)
        activate(database)
        directory = self.root / (name + "-installation")
        runtime = enroll_installation(database, directory, reviewed=True, new_installation=True)
        transport = provider or RelationProvider()
        services, appraisal = services_for(database, directory, transport, enabled=enabled)
        return RelationCase(database, directory, runtime, services, transport, appraisal)

    def dispose(self) -> None:
        for database in self.databases:
            database.dispose()


@pytest.fixture
def relation_cases(tmp_path: Path, project_root: Path) -> Iterator[RelationCaseFactory]:
    factory = RelationCaseFactory(tmp_path, project_root)
    try:
        yield factory
    finally:
        factory.dispose()


def seed_history(case: RelationCase, scenario: PriorRelationScenario) -> str:
    """Commit authored transport history through the same real owner path in both arms."""

    session = case.services.start_session.execute().session_id
    case.provider.kind = "abstain"
    case.provider.foreground_texts = tuple(assistant for _, assistant in scenario.history)
    for index, (user, _assistant) in enumerate(scenario.history):
        _talk(case.services, user_text=user, request_id=f"history-{index}", session_id=session)
    case.provider.kind = scenario.proposal_kind
    case.provider.prior_quote = scenario.history[-1][1] if scenario.history else ""
    case.provider.current_quote = scenario.user_text
    return session


def assert_domain_neutral(case: RelationCase) -> None:
    for table in DOMAIN_TABLES:
        assert scalar(case.database, f"SELECT count(*) FROM {table}") == 0


@dataclass(frozen=True, slots=True)
class ControlledOptionalRelationshipRead:
    """An absent read projection is unknown, never a newly damaged relationship."""

    context: RelationshipExpressionContext | None

    def execute(self, _session_id: str) -> RelationshipExpressionContext | None:
        return self.context


def request_hash(request: ConversationProviderRequest) -> str:
    return hashlib.sha256(json.dumps(asdict(request), sort_keys=True).encode()).hexdigest()


def run_matched_case(
    factory: RelationCaseFactory, scenario: PriorRelationScenario
) -> dict[str, object]:
    old = factory.create(scenario.name + "-off", enabled=False)
    new = factory.create(scenario.name + "-on", enabled=True)
    old_session, new_session = seed_history(old, scenario), seed_history(new, scenario)
    initial = new.services.talk.get_self.execute()
    relation_before = len(new.provider.for_relation())
    foreground_before = len(new.provider.for_foreground())
    baseline = _talk(
        old.services, user_text=scenario.user_text, request_id="current", session_id=old_session
    )
    reply = _talk(
        new.services, user_text=scenario.user_text, request_id="current", session_id=new_session
    )
    foreground = new.provider.for_foreground()[-1]
    base_foreground = old.provider.for_foreground()[-1]
    relation_count = len(new.provider.for_relation()) - relation_before
    assert relation_count == int(scenario.expected_operation)
    assert len(new.provider.for_foreground()) - foreground_before == 1
    assert foreground.parameters == base_foreground.parameters
    assert foreground.messages[-1] == base_foreground.messages[-1]
    assert reply.cognition_trace == baseline.cognition_trace
    _assert_cognition_preserved(reply.context_manifest, reply.cognition_trace)
    for name in (
        "character_agency_drive",
        "character_agency_act",
        "character_agency_subject",
        "character_agency_initiative",
        "character_delivery_goal",
        "emotion_state_version",
        "mood_state_version",
        "relationship_state_version",
        "recent_conversation_user_message_ids",
    ):
        assert getattr(reply.context_manifest, name) == getattr(baseline.context_manifest, name)
    assert (
        reply.context_manifest.character_agency_lead
        != baseline.context_manifest.character_agency_lead
    ) is scenario.expected_shift
    if scenario.expected_shift:
        assert reply.context_manifest.character_agency_lead == "owned_move_first"
        assert "prior_dialogue_relation" in reply.context_manifest.character_agency_reason_codes
        assert reply.context_manifest.character_agency_decision_schema_version == 3
    assert new.services.talk.get_self.execute() == initial
    assert_domain_neutral(new)
    return {
        "name": scenario.name,
        "canonical_history_pair_count": len(scenario.history),
        "relation_calls": relation_count,
        "foreground_calls": 1,
        "off_total_foreground_calls": len(old.provider.for_foreground()),
        "on_total_foreground_calls": len(new.provider.for_foreground()),
        "on_total_relation_calls": len(new.provider.for_relation()),
        "lead_changed": scenario.expected_shift,
        "off_lead": baseline.context_manifest.character_agency_lead,
        "on_lead": reply.context_manifest.character_agency_lead,
        "on_reasons": reply.context_manifest.character_agency_reason_codes,
        "relation_status": reply.context_manifest.prior_dialogue_relation_status,
        "relation_kind": reply.context_manifest.prior_dialogue_relation_kind,
        "on_agency_schema": reply.context_manifest.character_agency_decision_schema_version,
        "native_parameters_equal": True,
        "cognition_equal": True,
        "persistent_self_equal": True,
        "no_new_domain_transitions": True,
        "off_foreground_sha256": request_hash(base_foreground),
        "on_foreground_sha256": request_hash(foreground),
        "human_score": None,
    }


@pytest.mark.parametrize("scenario", PRIOR_DIALOGUE_RELATION_DIAGNOSTIC, ids=lambda row: row.name)
def test_versioned_matched_history_corpus(
    relation_cases: RelationCaseFactory, scenario: PriorRelationScenario
) -> None:
    assert PRIOR_DIALOGUE_RELATION_DIAGNOSTIC_VERSION == 1
    assert len(PRIOR_DIALOGUE_RELATION_DIAGNOSTIC) == 8
    run_matched_case(relation_cases, scenario)


def test_active_selection_precedes_actual_cognition_completion_and_foreground(
    relation_cases: RelationCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = relation_cases.create("ordering", enabled=True)
    scenario = PRIOR_DIALOGUE_RELATION_DIAGNOSTIC[0]
    session = seed_history(case, scenario)
    case.provider.events.clear()
    selected: list[CharacterAgencyDecision] = []
    original_select = CharacterAgencyKernel.select
    original_complete = SafeCognitionPipeline.complete

    def select(self: CharacterAgencyKernel, *args: Any, **kwargs: Any) -> CharacterAgencyDecision:
        decision = original_select(self, *args, **kwargs)
        selected.append(decision)
        case.provider.events.append("select:" + decision.lead.value)
        return decision

    def complete(self: SafeCognitionPipeline, *args: Any, **kwargs: Any) -> Any:
        assert selected[-1].lead.value == "owned_move_first"
        assert selected[-1].schema_version == 3
        case.provider.events.append("complete")
        return original_complete(self, *args, **kwargs)

    monkeypatch.setattr(CharacterAgencyKernel, "select", select)
    monkeypatch.setattr(SafeCognitionPipeline, "complete", complete)
    reply = _talk(
        case.services, user_text=scenario.user_text, request_id="current", session_id=session
    )
    assert case.provider.events == [
        "select:fused",
        "relation",
        "select:owned_move_first",
        "complete",
        "foreground",
    ]
    assert reply.context_manifest.character_prior_dialogue_relation is not None
    _assert_cognition_preserved(reply.context_manifest, reply.cognition_trace)


@pytest.mark.parametrize("outcome", ["invalid", "foreign_quote", "material", "unavailable"])
def test_failure_or_uncertainty_retains_baseline_without_semantic_retry(
    relation_cases: RelationCaseFactory, outcome: str
) -> None:
    scenario = PRIOR_DIALOGUE_RELATION_DIAGNOSTIC[0]
    old = relation_cases.create("fallback-off", enabled=False)
    new = relation_cases.create("fallback-on", enabled=True)
    old_session, new_session = seed_history(old, scenario), seed_history(new, scenario)
    new.provider.outcome = outcome
    before = len(new.provider.requests)
    baseline = _talk(
        old.services, user_text=scenario.user_text, request_id="current", session_id=old_session
    )
    reply = _talk(
        new.services, user_text=scenario.user_text, request_id="current", session_id=new_session
    )
    assert [is_relation(request) for request in new.provider.requests[before:]] == [True, False]
    assert new.provider.for_foreground()[-1] == old.provider.for_foreground()[-1]
    assert reply.cognition_trace == baseline.cognition_trace
    assert reply.context_manifest.character_agency_decision_schema_version == 1
    assert reply.context_manifest.character_prior_dialogue_relation is None
    assert (
        reply.context_manifest.prior_dialogue_relation_status
        == {
            "invalid": "invalid",
            "foreign_quote": "invalid",
            "material": "uncertain",
            "unavailable": "unavailable",
        }[outcome]
    )
    assert (reply.prior_dialogue_relation_usage is not None) is (outcome != "unavailable")
    assert_domain_neutral(new)


def test_one_foreground_retry_reuses_relation_and_replay_does_not_recompute(
    relation_cases: RelationCaseFactory,
) -> None:
    case = relation_cases.create("retry", enabled=True)
    scenario = PRIOR_DIALOGUE_RELATION_DIAGNOSTIC[0]
    session = seed_history(case, scenario)
    case.provider.foreground_texts = (EXCHANGE_MOVE, "Я готов, как человек.", "WIRE-RETRY")
    before = len(case.provider.requests)
    reply = _talk(
        case.services, user_text=scenario.user_text, request_id="current", session_id=session
    )
    assert [is_relation(request) for request in case.provider.requests[before:]] == [
        True,
        False,
        False,
    ]
    first, retry = case.provider.for_foreground()[-2:]
    assert first.parameters == retry.parameters
    assert first.messages[:-2] == retry.messages[:-2]
    assert first.messages[-1] == retry.messages[-1]
    assert reply.context_manifest.regeneration_reason == "masculine_self_reference"
    assert reply.context_manifest.character_agency_decision_schema_version == 3
    assert reply.context_manifest.character_prior_dialogue_relation is not None
    count = len(case.provider.requests)
    replay = _talk(
        case.services, user_text=scenario.user_text, request_id="current", session_id=session
    )
    assert replay.replayed
    assert len(case.provider.requests) == count
    assert len(case.appraisal.requests) == 2
    assert replay.context_manifest.character_prior_dialogue_relation is None
    assert replay.context_manifest.prior_dialogue_relation_status is None
    assert "prior_dialogue_relation_data" not in replay.context_manifest.included_sections
    assert replay.prior_dialogue_relation_usage is None
    assert_domain_neutral(case)


def test_no_routine_question_correction_keeps_protected_baseline(
    relation_cases: RelationCaseFactory,
) -> None:
    scenario = PriorRelationScenario(
        "no-question",
        ((EXCHANGE_INPUT, "Где ты поставишь общий стол?"),),
        "Не задавай в конце дежурный вопрос. Отвечай прямо.",
        "shift_from_prior_move",
        expected_operation=False,
    )
    row = run_matched_case(relation_cases, scenario)
    assert row["relation_status"] == "skipped"


@dataclass(frozen=True, slots=True)
class ControlledRecentRead:
    """A projection-skip fixture; actual owner headers remain unchanged."""

    context: RecentConversationContext

    def execute(self, **_kwargs: object) -> RecentConversationContext:
        return self.context


@pytest.mark.parametrize("unavailable", ["filtered", "erased"])
def test_latest_unavailable_slot_does_not_backfill_older_eligible_question(
    relation_cases: RelationCaseFactory, unavailable: str
) -> None:
    scenario = PriorRelationScenario(
        "latest-unavailable",
        (
            (EXCHANGE_INPUT, "Где ты поставишь общий стол?"),
            ("Есть ещё уточнение о площадке.", "Второе уточнение относится к проходу у входа."),
        ),
        NATURAL_SHIFT,
        "shift_from_prior_move",
    )
    case = relation_cases.create("latest-" + unavailable, enabled=True)
    session = seed_history(case, scenario)
    recent_reader = case.services.talk.recent_conversation
    assert recent_reader is not None
    recent = recent_reader.execute(session_id=session, excluded_interaction_id="projection-fixture")
    assert len(recent.turns) == 2
    if unavailable == "filtered":
        older = recent.turns[0]
        case.services.talk.recent_conversation = ControlledRecentRead(  # type: ignore[assignment]
            RecentConversationContext(
                1, (older,), len(older.user_content) + len(older.assistant_content), 1
            )
        )
    else:
        _exclude_prior(case, recent.turns[-1].user_message_id)  # type: ignore[arg-type]
    before = len(case.provider.requests)
    reply = _talk(case.services, user_text=NATURAL_SHIFT, request_id="current", session_id=session)
    assert [is_relation(request) for request in case.provider.requests[before:]] == [False]
    assert reply.context_manifest.prior_dialogue_relation_status == "skipped"
    assert reply.context_manifest.character_prior_dialogue_relation is None
    assert reply.context_manifest.character_agency_decision_schema_version == 1
    assert_domain_neutral(case)


def test_real_affect_owner_and_relationship_projection_remain_separate(
    relation_cases: RelationCaseFactory,
) -> None:
    scenario = PRIOR_DIALOGUE_RELATION_DIAGNOSTIC[0]
    for index, relationship in enumerate(("fresh", "established", "strained", "unknown")):
        relationship_context = (
            None if relationship == "unknown" else relationship_fixture(relationship)
        )
        old = relation_cases.create(f"owners-{index}-off", enabled=False)
        new = relation_cases.create(f"owners-{index}-on", enabled=True)
        for case in (old, new):
            case.services.talk.get_relationship = ControlledOptionalRelationshipRead(  # type: ignore[assignment]
                relationship_context
            )
            case.appraisal.response_factory = lambda request: appraisal_response(
                request, neutral=False
            )
        old_session, new_session = seed_history(old, scenario), seed_history(new, scenario)
        baseline = _talk(
            old.services, user_text=scenario.user_text, request_id="current", session_id=old_session
        )
        reply = _talk(
            new.services, user_text=scenario.user_text, request_id="current", session_id=new_session
        )
        for table in DOMAIN_TABLES:
            assert scalar(new.database, f"SELECT count(*) FROM {table}") == scalar(
                old.database, f"SELECT count(*) FROM {table}"
            )
        assert scalar(new.database, "SELECT count(*) FROM affective_transitions") > 0
        assert scalar(new.database, "SELECT count(*) FROM relationship_transitions") == 0
        assert (
            reply.context_manifest.emotion_state_version
            == baseline.context_manifest.emotion_state_version
        )
        assert (
            reply.context_manifest.mood_state_version
            == baseline.context_manifest.mood_state_version
        )
        assert (
            reply.context_manifest.relationship_state_version
            == baseline.context_manifest.relationship_state_version
        )
        current_relations = [
            request
            for request in new.provider.for_relation()
            if json.loads(request.messages[-1].content)["untrusted_dialogue_data"]["current_user"][
                "content"
            ]
            == scenario.user_text
        ]
        if current_relations:
            projection = json.loads(current_relations[-1].messages[1].content)
            expected_relationship = (
                asdict(relationship_context) if relationship_context is not None else None
            )
            assert projection["canonical_relationship_context"] == expected_relationship
            assert projection["canonical_emotional_context"] is not None
            assert (
                projection["canonical_emotional_context"]["state_version"]
                == reply.context_manifest.emotion_state_version
            )
            assert (
                projection["canonical_emotional_context"]["mood_version"]
                == reply.context_manifest.mood_state_version
            )


def test_canonical_history_and_relation_do_not_cross_counterparties_in_one_database(
    relation_cases: RelationCaseFactory,
) -> None:
    case = relation_cases.create("two-people", enabled=False)
    a_provider, b_provider = RelationProvider(), RelationProvider()
    a, _ = services_for(
        case.database, case.directory, a_provider, enabled=True, counterparty="person-a", prefix="a"
    )
    b, _ = services_for(
        case.database, case.directory, b_provider, enabled=True, counterparty="person-b", prefix="b"
    )
    a_session = a.start_session.execute().session_id
    a_provider.foreground_texts = (EXCHANGE_MOVE, "A-ONLY-WIRE")
    _talk(a, user_text=EXCHANGE_INPUT, request_id="a-prior", session_id=a_session)
    reply_a = _talk(a, user_text=RELEVANT_SHIFT, request_id="a-current", session_id=a_session)
    assert reply_a.context_manifest.character_prior_dialogue_relation is not None
    b_session = b.start_session.execute().session_id
    reply_b = _talk(b, user_text=NATURAL_SHIFT, request_id="b-first", session_id=b_session)
    assert b_provider.for_relation() == []
    assert reply_b.context_manifest.prior_dialogue_relation_status == "skipped"
    for request in b_provider.requests:
        contents = "\n".join(message.content for message in request.messages)
        assert EXCHANGE_INPUT not in contents
        assert EXCHANGE_MOVE not in contents
        assert "A-ONLY-WIRE" not in contents
        assert "a-000" not in contents
    assert_domain_neutral(case)


def test_default_disabled_managed_multiturn_retains_historical_foreground(
    relation_cases: RelationCaseFactory,
) -> None:
    case = relation_cases.create("disabled", enabled=False)
    assert conversation_settings().conversation_prior_dialogue_relation_enabled is False
    assert case.services.talk.prepare_prior_dialogue_relation is None
    assert case.services.talk.request_builder.prior_dialogue_relation_enabled is False
    scenario = PRIOR_DIALOGUE_RELATION_DIAGNOSTIC[0]
    session = seed_history(case, scenario)
    reply = _talk(
        case.services, user_text=scenario.user_text, request_id="current", session_id=session
    )
    assert len(case.provider.for_foreground()) == 2
    assert case.provider.for_relation() == []
    assert reply.context_manifest.schema_version == 17
    assert reply.context_manifest.prior_dialogue_relation_enabled is False
    assert reply.context_manifest.character_prior_dialogue_relation is None
    assert reply.context_manifest.prior_dialogue_relation_status is None
    assert reply.prior_dialogue_relation_usage is None
    assert reply.timings.prior_dialogue_relation_ms == 0.0
    assert_domain_neutral(case)


def test_semantic_preflight_budget_denial_retains_original_foreground_without_io(
    relation_cases: RelationCaseFactory,
) -> None:
    scenario = PRIOR_DIALOGUE_RELATION_DIAGNOSTIC[0]
    old = relation_cases.create("budget-off", enabled=False)
    new = relation_cases.create("budget-on", enabled=True)
    old_session, new_session = seed_history(old, scenario), seed_history(new, scenario)
    baseline = _talk(
        old.services, user_text=scenario.user_text, request_id="current", session_id=old_session
    )
    base_request = old.provider.for_foreground()[-1]
    trusted_roles = {ConversationMessageRole.SYSTEM, ConversationMessageRole.DEVELOPER}
    trusted_chars = sum(
        len(message.content) for message in base_request.messages if message.role in trusted_roles
    )
    new.services.talk.request_builder = replace(
        new.services.talk.request_builder, max_context_chars=trusted_chars
    )
    before = len(new.provider.requests)
    reply = _talk(
        new.services, user_text=scenario.user_text, request_id="current", session_id=new_session
    )
    assert [is_relation(request) for request in new.provider.requests[before:]] == [False]
    assert new.provider.for_foreground()[-1] == base_request
    assert reply.cognition_trace == baseline.cognition_trace
    assert reply.context_manifest.prior_dialogue_relation_status == "skipped"
    assert reply.context_manifest.prior_dialogue_relation_reason == "context_budget"
    assert reply.context_manifest.character_prior_dialogue_relation is None
    assert reply.context_manifest.character_agency_decision_schema_version == 1
    assert reply.prior_dialogue_relation_usage is None
    assert_domain_neutral(new)


def test_active_attachment_budget_fallback_restores_original_foreground_and_keeps_usage(
    relation_cases: RelationCaseFactory,
) -> None:
    # Bounded exact quotes can expand when JSON escaped. This adversarial transport
    # content tests budget admission only and has no conversational-quality meaning.
    scenario = PriorRelationScenario(
        "attachment-budget",
        ((EXCHANGE_INPUT, EXCHANGE_MOVE + "\x00" * 440),),
        RELEVANT_SHIFT + "\x00" * 440,
        "shift_from_prior_move",
        expected_shift=True,
    )
    old = relation_cases.create("attachment-off", enabled=False)
    probe = relation_cases.create("attachment-probe", enabled=True)
    new = relation_cases.create("attachment-on", enabled=True)
    old_session, probe_session, new_session = (
        seed_history(old, scenario),
        seed_history(probe, scenario),
        seed_history(new, scenario),
    )
    baseline = _talk(
        old.services, user_text=scenario.user_text, request_id="current", session_id=old_session
    )
    _talk(
        probe.services, user_text=scenario.user_text, request_id="current", session_id=probe_session
    )
    base_request = old.provider.for_foreground()[-1]
    stage_request = probe.provider.for_relation()[-1]
    active_request = probe.provider.for_foreground()[-1]
    trusted_roles = {ConversationMessageRole.SYSTEM, ConversationMessageRole.DEVELOPER}

    def trusted_chars(request: ConversationProviderRequest) -> int:
        return sum(
            len(message.content) for message in request.messages if message.role in trusted_roles
        )

    cap = max(trusted_chars(base_request), trusted_chars(stage_request))
    relation_data = [
        message.content
        for message in active_request.messages
        if message.role is ConversationMessageRole.USER
        and message.content.startswith('{"prior_dialogue_relation_data":')
    ]
    assert len(relation_data) == 1
    assert trusted_chars(active_request) + len(relation_data[0]) > cap
    new.services.talk.request_builder = replace(
        new.services.talk.request_builder, max_context_chars=cap
    )
    before = len(new.provider.requests)
    reply = _talk(
        new.services, user_text=scenario.user_text, request_id="current", session_id=new_session
    )
    assert [is_relation(request) for request in new.provider.requests[before:]] == [True, False]
    assert new.provider.for_foreground()[-1] == base_request
    assert reply.cognition_trace == baseline.cognition_trace
    assert reply.context_manifest.prior_dialogue_relation_status == "skipped"
    assert reply.context_manifest.prior_dialogue_relation_reason == "context_budget"
    assert reply.context_manifest.character_prior_dialogue_relation is None
    assert reply.context_manifest.character_agency_decision_schema_version == 1
    assert reply.prior_dialogue_relation_usage == ConversationUsage(
        input_tokens=100, output_tokens=8
    )
    assert_domain_neutral(new)


def test_completed_cognition_fallback_drops_active_relation_without_repeating_semantic_call(
    relation_cases: RelationCaseFactory,
) -> None:
    case = relation_cases.create("cognition-fallback", enabled=True)
    scenario = PRIOR_DIALOGUE_RELATION_DIAGNOSTIC[0]
    session = seed_history(case, scenario)
    case.services.talk.cognition_pipeline = SafeCognitionPipeline(
        planner=InvalidCompletionPlanner(),  # type: ignore[arg-type]
        fallback=DeterministicCognitionPlanner(intent_registry_version=2, monotonic=lambda: 0.0),
    )
    before = len(case.provider.requests)
    reply = _talk(
        case.services, user_text=scenario.user_text, request_id="current", session_id=session
    )
    assert [is_relation(request) for request in case.provider.requests[before:]] == [True, False]
    assert reply.context_manifest.cognition_pipeline_status == "fallback"
    assert reply.context_manifest.character_agency_status == "fallback"
    assert reply.context_manifest.character_agency_decision_schema_version == 1
    assert reply.context_manifest.character_prior_dialogue_relation is None
    assert reply.context_manifest.prior_dialogue_relation_status == "skipped"
    assert reply.context_manifest.prior_dialogue_relation_reason == "not_eligible"
    assert reply.prior_dialogue_relation_usage == ConversationUsage(
        input_tokens=100, output_tokens=8
    )
    assert_domain_neutral(case)
