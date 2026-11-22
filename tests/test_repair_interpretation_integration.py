"""Managed repair targeting changes transient intake, never state ownership.

All classifications and replies are authored transport fixtures. These tests measure
plumbing, source admission and fallback; they do not certify semantic model quality.
"""

# ruff: noqa: RUF001  # Authored Russian dialogue deliberately exercises real analyzers.

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from satori.application.cognition.contracts import PerceptionSignal
from satori.application.cognition.use_cases import (
    DeterministicCognitionPlanner,
    SafeCognitionPipeline,
)
from satori.application.conversation.contracts import (
    BehaviorPolicy,
    RecentConversationContext,
    SatoriReply,
)
from satori.application.conversation.history import InteractionLog
from satori.application.conversation.policy import BEHAVIOR_POLICY_V28, BEHAVIOR_POLICY_V35
from satori.application.conversation.repair_interpretation import (
    REPAIR_INTERPRETATION_OPERATION_MARKER,
    PrepareRepairInterpretation,
)
from satori.application.conversation.repair_interpretation_contracts import (
    PreparedRepairInterpretation,
)
from satori.application.source_policy.contracts import SourceExclusionCommand
from satori.application.source_policy.dispatch import admit_provider_dispatch
from satori.application.source_policy.use_cases import SourcePolicyControls
from satori.composition import (
    ConversationServices,
    build_conversation_services,
    build_initial_self_services,
)
from satori.core.conversation import (
    ConversationMessageRole,
    ConversationProviderError,
    ConversationProviderFailureReason,
    ConversationProviderRequest,
    ConversationProviderResponse,
    ConversationUsage,
    ProviderUnavailable,
)
from satori.domain.affect import AFFECT_POLICY_V1
from satori.domain.source_policy import PayloadRef, SourcePolicyError
from satori.infrastructure.persistence.database import Database, create_database
from satori.infrastructure.persistence.migrations import upgrade_database
from satori.infrastructure.persistence.models.conversation import (
    ConversationInteractionRow,
    ConversationMessageRow,
)
from satori.infrastructure.persistence.models.source_policy import SourceInputRow, SourcePayloadRow
from satori.infrastructure.persistence.repositories import conversation as conversation_repository
from satori.infrastructure.persistence.source_policy import SessionSourceAdmission, record_payload
from satori.infrastructure.source_policy.installation import (
    canonical_root,
    enroll_installation,
    repair_installation,
)
from satori.infrastructure.source_policy.runtime import SourcePolicyRuntime
from tests.fakes import FakeAffectiveAppraisalProvider, FrozenClock, SequenceIdGenerator
from tests.repair_frame_diagnostic import (
    ALL_CASES,
    AUTHORED_LABELS_ONLY,
    HELD_OUT_CASES,
    INHERITED_REGRESSION_CASES,
    RepairFrameDiagnosticCase,
    validate_corpus,
)
from tests.test_checkpoint143_character_agency_integration import (
    _assert_cognition_preserved,
    _talk,
)
from tests.test_checkpoint143_v34_affect_expression import appraisal_response, scalar
from tests.test_checkpoint143_v37_context_use import NOW
from tests.test_conversation import activate, conversation_settings, skip_episode_provider

NATURAL_REPAIR = "Я зря приписал тебе безразличие. Беру эти слова назад."
SAME_WORDS = "Это было грубо. Извини."
SATORI_HISTORY = (
    (
        "Я слишком резко отозвался о тебе.",
        "Ты можешь критиковать ответ, но не приписывай мне намерения.",
    ),
)
DRAFT_HISTORY = (
    (
        "Дальше отправлю текст, который хочу написать коллеге после ссоры. "
        "Это черновик письма ему.",
        "Пришли текст черновика, посмотрим формулировку.",
    ),
)
OLDER_PAIR = (
    "Объясни разницу между append и extend в Python.",
    "Append добавляет один элемент; extend добавляет элементы последовательности.",
)
DOMAIN_TABLES = (
    "affective_transitions",
    "relationship_transitions",
    "satori_positions",
    "satori_inclinations",
    "personality_revisions",
)
Operation = Literal["repair", "foreground"]
Kind = Literal["repair_toward_satori", "other", "abstain"]
Hook = Callable[[Operation, ConversationProviderRequest], None]


def is_repair(request: ConversationProviderRequest) -> bool:
    return any(
        message.role is ConversationMessageRole.DEVELOPER
        and message.content.startswith(REPAIR_INTERPRETATION_OPERATION_MARKER)
        for message in request.messages
    )


@dataclass(slots=True)
class RepairProvider:
    """Actual dispatch admission with synthetic repair categories and foreground text."""

    kind: Kind = "abstain"
    outcome: str = "valid"
    current_quote: str = NATURAL_REPAIR
    prior_user_quote: str = ""
    prior_assistant_quote: str = ""
    speech_act: Literal["repair_offer", "other", "abstain"] | None = None
    frame: Literal["direct_exchange", "supplied_or_reported", "unresolved"] | None = None
    addressee: Literal["satori", "other", "unresolved", "none"] | None = None
    basis: Literal["current_turn", "prior_pair", "unresolved"] | None = None
    uncertainty: Literal["supported", "material"] | None = None
    foreground_texts: tuple[str, ...] = ()
    before_dispatch: Hook | None = None
    after_response: Hook | None = None
    requests: list[ConversationProviderRequest] = field(default_factory=list, init=False)
    transport_active: bool = field(default=False, init=False)

    def for_repair(self) -> list[ConversationProviderRequest]:
        return [request for request in self.requests if is_repair(request)]

    def for_foreground(self) -> list[ConversationProviderRequest]:
        return [request for request in self.requests if not is_repair(request)]

    async def generate(
        self, request: ConversationProviderRequest, /
    ) -> ConversationProviderResponse:
        operation: Operation = "repair" if is_repair(request) else "foreground"
        if self.before_dispatch is not None:
            self.before_dispatch(operation, request)
        try:
            self.transport_active = True
            try:
                admit_provider_dispatch()
                self.requests.append(request)
                if operation == "repair":
                    if self.outcome == "unavailable":
                        raise ProviderUnavailable(
                            provider="fixture",
                            model="no-network",
                            message="synthetic repair outage",
                            reason=ConversationProviderFailureReason.TRANSPORT_UNAVAILABLE,
                        )
                    if self.outcome == "unexpected":
                        raise RuntimeError("synthetic unexpected repair failure")
                    speech_act = self.speech_act or (
                        "repair_offer" if self.kind == "repair_toward_satori" else self.kind
                    )
                    quoting = speech_act != "abstain"
                    body = json.dumps(
                        {
                            "schema_version": 2,
                            "speech_act": speech_act,
                            "frame": self.frame or ("direct_exchange" if quoting else "unresolved"),
                            "addressee": self.addressee
                            or (
                                "satori"
                                if self.kind == "repair_toward_satori"
                                else "none"
                                if quoting
                                else "unresolved"
                            ),
                            "basis": self.basis
                            or (
                                "prior_pair"
                                if quoting and self.prior_user_quote
                                else "current_turn"
                                if quoting
                                else "unresolved"
                            ),
                            "uncertainty": self.uncertainty
                            or (
                                "material"
                                if self.outcome == "material" or not quoting
                                else "supported"
                            ),
                            "current_user_quote": (
                                ""
                                if not quoting
                                else "FOREIGN-QUOTE"
                                if self.outcome == "foreign_quote"
                                else self.current_quote
                            ),
                            "prior_user_quote": ("" if not quoting else self.prior_user_quote),
                            "prior_assistant_quote": (
                                "" if not quoting else self.prior_assistant_quote
                            ),
                        },
                        ensure_ascii=False,
                    )
                    if self.outcome == "invalid":
                        body = "not JSON; no semantic retry"
                else:
                    index = len(self.for_foreground()) - 1
                    body = (
                        self.foreground_texts[index]
                        if index < len(self.foreground_texts)
                        else "WIRE"
                    )
                response = ConversationProviderResponse(
                    body,
                    "fixture",
                    "no-network",
                    "stop",
                    usage=ConversationUsage(input_tokens=100, output_tokens=8),
                )
            finally:
                self.transport_active = False
        except ConversationProviderError:
            if self.after_response is not None:
                self.after_response(operation, request)
            raise
        if self.after_response is not None:
            self.after_response(operation, request)
        return response


@dataclass(slots=True)
class RepairCase:
    database: Database
    directory: Path
    runtime: SourcePolicyRuntime
    services: ConversationServices
    provider: RepairProvider
    appraisal: FakeAffectiveAppraisalProvider


@dataclass(slots=True)
class RepairCaseFactory:
    root: Path
    project_root: Path
    databases: list[Database] = field(default_factory=list)

    def create(
        self,
        name: str,
        *,
        enabled: bool,
        provider: RepairProvider | None = None,
        settings_overrides: dict[str, object] | None = None,
        behavior_policy: BehaviorPolicy = BEHAVIOR_POLICY_V35,
    ) -> RepairCase:
        database_url = f"sqlite+pysqlite:///{self.root / (name + '.db')}"
        upgrade_database(database_url, config_path=self.project_root / "alembic.ini")
        database = create_database(database_url)
        self.databases.append(database)
        activate(database)
        directory = self.root / (name + "-installation")
        runtime = enroll_installation(database, directory, reviewed=True, new_installation=True)
        provider = provider or RepairProvider()
        appraisal = FakeAffectiveAppraisalProvider(
            response_factory=lambda request: appraisal_response(request, neutral=True)
        )
        settings_values: dict[str, object] = {
            "source_policy_directory": directory,
            "conversation_repair_interpretation_enabled": enabled,
            "conversation_current_position_enabled": False,
            "conversation_prior_dialogue_relation_enabled": False,
        }
        settings_values.update(settings_overrides or {})
        services = build_conversation_services(
            database,
            build_initial_self_services(database),
            provider,
            skip_episode_provider(),
            conversation_settings(**settings_values),
            clock=FrozenClock(NOW),
            id_generator=SequenceIdGenerator(*(f"repair-{index:04d}" for index in range(300))),
            appraisal_provider=appraisal,
            behavior_policy=behavior_policy,
            source_policy_required=True,
        )
        services.talk.monotonic = lambda: 0.0
        services.talk.cognition_pipeline = SafeCognitionPipeline(
            planner=DeterministicCognitionPlanner(intent_registry_version=2, monotonic=lambda: 0.0),
            fallback=DeterministicCognitionPlanner(
                intent_registry_version=2, monotonic=lambda: 0.0
            ),
        )
        return RepairCase(database, directory, runtime, services, provider, appraisal)

    def dispose(self) -> None:
        for database in self.databases:
            database.dispose()


@pytest.fixture
def repair_cases(tmp_path: Path, project_root: Path) -> Iterator[RepairCaseFactory]:
    factory = RepairCaseFactory(tmp_path, project_root)
    try:
        yield factory
    finally:
        factory.dispose()


def seed(case: RepairCase, history: tuple[tuple[str, str], ...] = SATORI_HISTORY) -> str:
    session = case.services.start_session.execute().session_id
    case.provider.foreground_texts = tuple(assistant for _, assistant in history)
    for index, (user, _assistant) in enumerate(history):
        _talk(case.services, user_text=user, request_id=f"history-{index}", session_id=session)
    case.provider.kind = "repair_toward_satori"
    return session


def assert_domain_neutral(case: RepairCase) -> None:
    for table in DOMAIN_TABLES:
        assert scalar(case.database, f"SELECT count(*) FROM {table}") == 0


def assert_repair_cue(reply: SatoriReply, expected: bool) -> None:
    trace = reply.cognition_trace
    assert trace is not None
    assert (PerceptionSignal.EXPLICIT_REPAIR_OFFER in trace.perception.signals) is expected
    _assert_cognition_preserved(reply.context_manifest, trace)


def test_natural_repair_updates_evidence_intake_intent_and_agency_together(
    repair_cases: RepairCaseFactory,
) -> None:
    old = repair_cases.create("natural-off", enabled=False)
    new = repair_cases.create("natural-on", enabled=True)
    old_session, new_session = seed(old), seed(new)
    initial = new.services.talk.get_self.execute()
    before = len(new.provider.requests)
    baseline = _talk(
        old.services, user_text=NATURAL_REPAIR, request_id="current", session_id=old_session
    )
    reply = _talk(
        new.services, user_text=NATURAL_REPAIR, request_id="current", session_id=new_session
    )
    assert_repair_cue(baseline, False)
    assert_repair_cue(reply, True)
    assert [is_repair(request) for request in new.provider.requests[before:]] == [True, False]
    assert reply.context_manifest.cognition_primary_intent == "receive_repair"
    assert reply.context_manifest.character_agency_drive == "repair"
    assert reply.context_manifest.character_agency_subject == "relationship"
    assert "repair_offer" in reply.context_manifest.character_agency_reason_codes
    assert reply.context_manifest.character_agency_status == "applied"
    trace = reply.repair_interpretation_trace
    assert trace is not None
    assert trace.status.value == "applied"
    assert trace.kind is not None
    assert trace.kind.value == "repair_toward_satori"
    assert trace.baseline_repair_offer is False
    assert trace.effective_repair_offer is True
    assert reply.repair_interpretation_usage == ConversationUsage(input_tokens=100, output_tokens=8)
    assert new.services.talk.get_self.execute() == initial
    assert_domain_neutral(new)


@pytest.mark.parametrize(
    ("history", "kind", "effective"),
    [(SATORI_HISTORY, "repair_toward_satori", True), (DRAFT_HISTORY, "other", False)],
)
def test_supported_target_can_accept_or_clear_same_lexical_repair(
    repair_cases: RepairCaseFactory,
    history: tuple[tuple[str, str], ...],
    kind: Kind,
    effective: bool,
) -> None:
    old = repair_cases.create("target-off", enabled=False)
    new = repair_cases.create("target-on", enabled=True)
    old_session, new_session = seed(old, history), seed(new, history)
    new.provider.kind = kind
    new.provider.speech_act = "repair_offer"
    new.provider.frame = "direct_exchange" if history == SATORI_HISTORY else "supplied_or_reported"
    new.provider.addressee = "satori" if history == SATORI_HISTORY else "other"
    new.provider.current_quote = SAME_WORDS
    new.provider.prior_user_quote, new.provider.prior_assistant_quote = history[-1]
    baseline = _talk(
        old.services, user_text=SAME_WORDS, request_id="current", session_id=old_session
    )
    reply = _talk(new.services, user_text=SAME_WORDS, request_id="current", session_id=new_session)
    assert_repair_cue(baseline, True)
    assert_repair_cue(reply, effective)
    trace = reply.repair_interpretation_trace
    assert trace is not None
    assert trace.status.value == "applied"
    assert trace.kind is not None
    assert trace.kind.value == kind
    assert trace.speech_act is not None
    assert trace.speech_act.value == "repair_offer"
    assert trace.frame is not None
    assert trace.frame.value == (
        "direct_exchange" if history == SATORI_HISTORY else "supplied_or_reported"
    )
    assert trace.baseline_repair_offer is True
    assert trace.effective_repair_offer is effective
    assert (reply.context_manifest.cognition_primary_intent == "receive_repair") is effective
    assert (reply.context_manifest.character_agency_drive == "repair") is effective
    assert_domain_neutral(new)


@pytest.mark.parametrize("scenario", ALL_CASES, ids=lambda row: row.case_id)
def test_authored_frame_corpus_uses_managed_causal_path_without_model_quality_claim(
    repair_cases: RepairCaseFactory, scenario: RepairFrameDiagnosticCase
) -> None:
    validate_corpus()
    assert AUTHORED_LABELS_ONLY
    assert len(HELD_OUT_CASES) == 12
    assert len(INHERITED_REGRESSION_CASES) == 2
    old = repair_cases.create("frame-off", enabled=False)
    new = repair_cases.create("frame-on", enabled=True)
    old_session, new_session = (
        seed(old, scenario.prior_history),
        seed(new, scenario.prior_history),
    )
    new.provider.speech_act = scenario.authored_speech_act
    new.provider.frame = scenario.authored_frame
    new.provider.addressee = scenario.authored_addressee
    new.provider.basis = scenario.authored_basis
    new.provider.uncertainty = scenario.authored_uncertainty
    new.provider.current_quote = scenario.current_focal_quote
    new.provider.prior_user_quote = scenario.prior_user_quote
    new.provider.prior_assistant_quote = scenario.prior_assistant_quote
    before = len(new.provider.requests)
    baseline = _talk(
        old.services,
        user_text=scenario.current_text,
        request_id="current",
        session_id=old_session,
    )
    reply = _talk(
        new.services,
        user_text=scenario.current_text,
        request_id="current",
        session_id=new_session,
    )
    assert [is_repair(request) for request in new.provider.requests[before:]] == [True, False]
    trace = reply.repair_interpretation_trace
    assert trace is not None
    assert trace.speech_act is not None
    assert trace.speech_act.value == scenario.authored_speech_act
    assert trace.frame is not None
    assert trace.frame.value == scenario.authored_frame
    assert trace.addressee is not None
    assert trace.addressee.value == scenario.authored_addressee
    assert trace.basis is not None
    assert trace.basis.value == scenario.authored_basis
    if scenario.expected_baseline_repair is not None:
        assert trace.baseline_repair_offer is scenario.expected_baseline_repair
        assert_repair_cue(baseline, scenario.expected_baseline_repair)
    effective = (
        trace.baseline_repair_offer
        if scenario.expected_effective is None
        else scenario.expected_effective
    )
    assert trace.effective_repair_offer is effective
    assert_repair_cue(reply, effective)
    assert (reply.context_manifest.cognition_primary_intent == "receive_repair") is effective
    assert (reply.context_manifest.character_agency_drive == "repair") is effective
    for field_name in (
        "emotion_state_version",
        "mood_state_version",
        "relationship_state_version",
        "recent_conversation_user_message_ids",
    ):
        assert getattr(reply.context_manifest, field_name) == getattr(
            baseline.context_manifest, field_name
        )
    assert new.services.talk.get_self.execute() == old.services.talk.get_self.execute()
    assert_domain_neutral(new)


@pytest.mark.parametrize(
    ("frame", "addressee", "effective"),
    [
        ("direct_exchange", "satori", True),
        ("supplied_or_reported", "satori", False),
        ("direct_exchange", "other", False),
        ("supplied_or_reported", "other", False),
    ],
)
def test_joint_frame_and_addressee_gates_control_same_input_without_state_mutation(
    repair_cases: RepairCaseFactory,
    frame: Literal["direct_exchange", "supplied_or_reported"],
    addressee: Literal["satori", "other"],
    effective: bool,
) -> None:
    case = repair_cases.create("joint-cell", enabled=True)
    session = seed(case)
    case.provider.speech_act = "repair_offer"
    case.provider.frame, case.provider.addressee = frame, addressee
    initial = case.services.talk.get_self.execute()
    before = len(case.provider.requests)
    reply = _talk(case.services, user_text=NATURAL_REPAIR, request_id="current", session_id=session)
    assert [is_repair(request) for request in case.provider.requests[before:]] == [True, False]
    assert_repair_cue(reply, effective)
    trace = reply.repair_interpretation_trace
    assert trace is not None
    assert trace.effective_repair_offer is effective
    assert (reply.context_manifest.cognition_primary_intent == "receive_repair") is effective
    assert (reply.context_manifest.character_agency_drive == "repair") is effective
    assert case.services.talk.get_self.execute() == initial
    assert_domain_neutral(case)


@pytest.mark.parametrize(
    ("outcome", "kind", "status"),
    [
        ("invalid", "repair_toward_satori", "invalid"),
        ("foreign_quote", "repair_toward_satori", "invalid"),
        ("unavailable", "repair_toward_satori", "unavailable"),
        ("valid", "abstain", "abstain"),
    ],
)
@pytest.mark.parametrize("current", [NATURAL_REPAIR, SAME_WORDS])
def test_invalid_abstain_and_unavailable_preserve_baseline_without_retry(
    repair_cases: RepairCaseFactory,
    outcome: str,
    kind: Kind,
    status: str,
    current: str,
) -> None:
    old = repair_cases.create("fallback-off", enabled=False)
    new = repair_cases.create("fallback-on", enabled=True)
    old_session, new_session = seed(old), seed(new)
    new.provider.outcome, new.provider.kind, new.provider.current_quote = outcome, kind, current
    before = len(new.provider.requests)
    baseline = _talk(old.services, user_text=current, request_id="current", session_id=old_session)
    reply = _talk(new.services, user_text=current, request_id="current", session_id=new_session)
    assert [is_repair(request) for request in new.provider.requests[before:]] == [True, False]
    assert new.provider.for_foreground()[-1] == old.provider.for_foreground()[-1]
    assert reply.cognition_trace == baseline.cognition_trace
    assert reply.context_manifest == baseline.context_manifest
    trace = reply.repair_interpretation_trace
    assert trace is not None
    assert trace.status.value == status
    assert trace.effective_repair_offer is trace.baseline_repair_offer
    assert (reply.repair_interpretation_usage is not None) is (outcome != "unavailable")
    assert_domain_neutral(new)


@pytest.mark.parametrize("current", [NATURAL_REPAIR, SAME_WORDS])
def test_valid_material_targeting_withholds_admission_even_if_lexical_cue_was_true(
    repair_cases: RepairCaseFactory, current: str
) -> None:
    old = repair_cases.create("withhold-off", enabled=False)
    new = repair_cases.create("withhold-on", enabled=True)
    old_session, new_session = seed(old), seed(new)
    new.provider.outcome = "material"
    new.provider.current_quote = current
    new.provider.frame = "unresolved"
    new.provider.addressee = "unresolved"
    new.provider.basis = "unresolved"
    before = len(new.provider.requests)
    baseline = _talk(old.services, user_text=current, request_id="current", session_id=old_session)
    reply = _talk(new.services, user_text=current, request_id="current", session_id=new_session)
    assert [is_repair(request) for request in new.provider.requests[before:]] == [True, False]
    trace = reply.repair_interpretation_trace
    assert trace is not None
    assert trace.status.value == "uncertain"
    assert trace.kind is not None
    assert trace.kind.value == "abstain"
    assert trace.speech_act is not None
    assert trace.speech_act.value == "repair_offer"
    assert trace.frame is not None
    assert trace.frame.value == "unresolved"
    assert trace.addressee is not None
    assert trace.addressee.value == "unresolved"
    assert trace.effective_repair_offer is False
    assert trace.baseline_repair_offer is (current == SAME_WORDS)
    assert_repair_cue(reply, False)
    assert reply.context_manifest.cognition_primary_intent != "receive_repair"
    if current == NATURAL_REPAIR:
        assert reply.cognition_trace == baseline.cognition_trace
        assert new.provider.for_foreground()[-1] == old.provider.for_foreground()[-1]
    else:
        assert_repair_cue(baseline, True)
        assert new.provider.for_foreground()[-1] != old.provider.for_foreground()[-1]
    assert new.services.talk.get_self.execute() == old.services.talk.get_self.execute()
    assert_domain_neutral(new)


def test_current_only_is_allowed_only_when_no_prior_slot_exists(
    repair_cases: RepairCaseFactory,
) -> None:
    case = repair_cases.create("current-only", enabled=True)
    session = seed(case, ())
    reply = _talk(case.services, user_text=NATURAL_REPAIR, request_id="current", session_id=session)
    assert len(case.provider.for_repair()) == 1
    assert_repair_cue(reply, True)
    data = json.loads(case.provider.for_repair()[0].messages[-1].content)
    assert data["untrusted_dialogue_data"]["prior_pair"] is None
    assert_domain_neutral(case)


@dataclass(frozen=True, slots=True)
class ControlledRecentRead:
    context: RecentConversationContext

    def execute(self, **_kwargs: object) -> RecentConversationContext:
        return self.context


def exclude(case: RepairCase, message_id: str) -> None:
    root = canonical_root(case.database, case.runtime, message_id, counterparty_id="local-default")
    controls = SourcePolicyControls(case.runtime)
    preview = controls.preview(root)
    receipt = controls.apply(
        SourceExclusionCommand(
            message_id=message_id,
            expected_generation=preview.generation,
            request_id=f"exclude-{message_id}",
            preview_digest=preview.preview_digest,
        ),
        root,
        repair=lambda snapshot, item: repair_installation(
            case.database, case.runtime, snapshot, item
        ),
    )
    assert receipt.phase.value == "complete"


@pytest.mark.parametrize("unavailable", ["filtered", "erased", "oversized"])
def test_latest_unavailable_slot_never_backfills_an_older_pair(
    repair_cases: RepairCaseFactory, unavailable: str
) -> None:
    case = repair_cases.create("latest-" + unavailable, enabled=True)
    history = (*SATORI_HISTORY, ("Ещё одно наблюдение.", "Новый канонический слот."))
    session = seed(case, history)
    reader = case.services.talk.recent_conversation
    assert reader is not None
    recent = reader.execute(session_id=session, excluded_interaction_id="fixture-current")
    assert len(recent.turns) == 2
    if unavailable == "erased":
        exclude(case, recent.turns[-1].user_message_id)
    else:
        turns = (
            recent.turns[:1]
            if unavailable == "filtered"
            else (recent.turns[0], replace(recent.turns[-1], assistant_content="z" * 4097))
        )
        case.services.talk.recent_conversation = ControlledRecentRead(  # type: ignore[assignment]
            RecentConversationContext(
                1,
                turns,
                sum(len(turn.user_content) + len(turn.assistant_content) for turn in turns),
                1 if unavailable == "filtered" else 0,
            )
        )
    before = len(case.provider.requests)
    reply = _talk(case.services, user_text=NATURAL_REPAIR, request_id="current", session_id=session)
    assert [is_repair(request) for request in case.provider.requests[before:]] == [False]
    trace = reply.repair_interpretation_trace
    assert trace is not None
    assert trace.status.value == "skipped"
    assert_repair_cue(reply, False)
    assert_domain_neutral(case)


@pytest.mark.parametrize(
    "current",
    [
        "Объясни разницу между RAM и SSD в двух предложениях.",
        "Сколько будет семь умножить на восемь?",
        "Просто побудь рядом, без советов.",
        "Мне сейчас очень тяжело.",
        "Не продолжай эту тему.",
        "Не задавай в конце дежурный вопрос. Отвечай прямо.",
        "Я сорвался на тебя, прости. Объясни теперь разницу между RAM и SSD.",
    ],
)
def test_protected_current_work_skips_candidate_and_preserves_foreground(
    repair_cases: RepairCaseFactory, current: str
) -> None:
    old = repair_cases.create("protected-off", enabled=False)
    new = repair_cases.create("protected-on", enabled=True)
    old_session, new_session = seed(old), seed(new)
    before = len(new.provider.requests)
    baseline = _talk(old.services, user_text=current, request_id="current", session_id=old_session)
    reply = _talk(new.services, user_text=current, request_id="current", session_id=new_session)
    assert [is_repair(request) for request in new.provider.requests[before:]] == [False]
    assert new.provider.for_foreground()[-1] == old.provider.for_foreground()[-1]
    assert reply.cognition_trace == baseline.cognition_trace
    assert reply.context_manifest == baseline.context_manifest
    trace = reply.repair_interpretation_trace
    assert trace is not None
    assert trace.status.value == "skipped"
    assert trace.reason.value == "not_eligible"
    assert_domain_neutral(new)


def test_disabled_is_zero_extra_call_and_replay_does_not_reinterpret(
    repair_cases: RepairCaseFactory,
) -> None:
    case = repair_cases.create("disabled", enabled=False)
    assert conversation_settings().conversation_repair_interpretation_enabled is False
    assert case.services.talk.prepare_repair_interpretation is None
    session = seed(case)
    reply = _talk(case.services, user_text=NATURAL_REPAIR, request_id="current", session_id=session)
    assert case.provider.for_repair() == []
    assert len(case.provider.for_foreground()) == 2
    assert reply.repair_interpretation_trace is None
    assert reply.repair_interpretation_usage is None
    assert reply.timings.repair_interpretation_ms == 0.0
    calls = len(case.provider.requests), len(case.appraisal.requests)
    replay = _talk(
        case.services, user_text=NATURAL_REPAIR, request_id="current", session_id=session
    )
    assert replay.replayed
    assert (len(case.provider.requests), len(case.appraisal.requests)) == calls
    assert replay.repair_interpretation_trace is None
    assert_domain_neutral(case)


def test_one_shared_foreground_retry_reuses_frozen_interpretation(
    repair_cases: RepairCaseFactory,
) -> None:
    case = repair_cases.create("retry", enabled=True)
    session = seed(case)
    case.provider.foreground_texts = (SATORI_HISTORY[0][1], "Я готов, как человек.", "WIRE-RETRY")
    before = len(case.provider.requests)
    reply = _talk(case.services, user_text=NATURAL_REPAIR, request_id="current", session_id=session)
    assert [is_repair(request) for request in case.provider.requests[before:]] == [
        True,
        False,
        False,
    ]
    first, retry = case.provider.for_foreground()[-2:]
    assert first.parameters == retry.parameters
    assert first.messages[:-2] == retry.messages[:-2]
    assert first.messages[-1] == retry.messages[-1]
    assert reply.context_manifest.regeneration_reason == "masculine_self_reference"
    assert_repair_cue(reply, True)
    trace = reply.repair_interpretation_trace
    assert trace is not None
    assert trace.effective_repair_offer is True
    calls = len(case.provider.requests), len(case.appraisal.requests)
    replay = _talk(
        case.services, user_text=NATURAL_REPAIR, request_id="current", session_id=session
    )
    assert replay.replayed
    assert (len(case.provider.requests), len(case.appraisal.requests)) == calls
    assert replay.repair_interpretation_trace is None
    assert replay.repair_interpretation_usage is None
    assert_domain_neutral(case)


@dataclass(slots=True)
class SourceTrace:
    prepared: list[tuple[tuple[str, ...], tuple[PayloadRef, ...]]] = field(default_factory=list)
    input_ids: list[str | None] = field(default_factory=list)
    transported: list[str] = field(default_factory=list)
    recorded: list[str | None] = field(default_factory=list)


def trace_sources(
    monkeypatch: pytest.MonkeyPatch,
    provider: RepairProvider,
    *,
    before_prepare: Callable[[], None] | None = None,
) -> SourceTrace:
    trace = SourceTrace()
    original_prepare = InteractionLog.prepare_inputs
    original_dispatch = SessionSourceAdmission.dispatch
    original_record = record_payload

    def prepare(
        self: InteractionLog,
        *,
        root_message_ids: tuple[str, ...],
        payload_refs: tuple[PayloadRef, ...],
    ) -> str | None:
        trace.prepared.append((root_message_ids, payload_refs))
        if before_prepare is not None:
            before_prepare()
        input_id = original_prepare(
            self, root_message_ids=root_message_ids, payload_refs=payload_refs
        )
        trace.input_ids.append(input_id)
        return input_id

    def dispatch(self: SessionSourceAdmission, input_id: str) -> None:
        if provider.transport_active:
            trace.transported.append(input_id)
        original_dispatch(self, input_id)

    def record(
        session: Session, kind: str, payload_id: str, version: int, input_id: str | None
    ) -> None:
        if kind == "assistant_reply":
            trace.recorded.append(input_id)
        original_record(session, kind, payload_id, version, input_id)

    monkeypatch.setattr(InteractionLog, "prepare_inputs", prepare)
    monkeypatch.setattr(SessionSourceAdmission, "dispatch", dispatch)
    monkeypatch.setattr(conversation_repository, "record_payload", record)
    return trace


def seed_full_footprint(case: RepairCase) -> tuple[str, tuple[str, ...], tuple[PayloadRef, ...]]:
    session = seed(case, (OLDER_PAIR, *SATORI_HISTORY))
    reader = case.services.talk.recent_conversation
    assert reader is not None
    recent = reader.execute(session_id=session, excluded_interaction_id="unused-current-slot")
    assert len(recent.turns) == 2
    return (
        session,
        tuple(turn.user_message_id for turn in recent.turns),
        tuple(PayloadRef("assistant_reply", turn.interaction_id) for turn in recent.turns),
    )


def current(case: RepairCase) -> tuple[str, str, str]:
    with case.database.session_factory() as session:
        row = session.scalar(
            select(ConversationInteractionRow).where(
                ConversationInteractionRow.client_request_id == "current"
            )
        )
        assert row is not None
        message = session.scalar(
            select(ConversationMessageRow).where(
                ConversationMessageRow.interaction_id == row.interaction_id,
                ConversationMessageRow.role == "user",
            )
        )
        assert message is not None
        return row.interaction_id, message.message_id, row.status


def assert_full_receipt(
    case: RepairCase,
    trace: SourceTrace,
    prior_roots: tuple[str, ...],
    prior_refs: tuple[PayloadRef, ...],
    *,
    committed: bool,
) -> str:
    assert len(trace.prepared) == 1
    assert len(trace.input_ids) == 1
    input_id = trace.input_ids[0]
    assert input_id is not None
    interaction_id, current_root, _status = current(case)
    expected_roots = {current_root, *prior_roots}
    roots, refs = trace.prepared[0]
    assert set(roots) == expected_roots
    assert refs == prior_refs
    with case.database.session_factory() as session:
        row = session.get(SourceInputRow, input_id)
        assert row is not None
        assert set(row.root_message_ids) == expected_roots
        assert row.proof_status == "complete"
        expected_refs = [
            {"kind": ref.kind, "payload_id": ref.payload_id, "version": ref.version}
            for ref in prior_refs
        ]
        assert row.payload_refs == expected_refs
        if committed:
            payload = session.get(SourcePayloadRow, ("assistant_reply", interaction_id, 1))
            assert payload is not None
            assert set(payload.root_message_ids) == expected_roots
            assert payload.payload_refs == expected_refs
    assert trace.recorded == ([input_id] if committed else [])
    return input_id


@pytest.mark.parametrize("outcome", ["valid", "invalid", "material", "unavailable", "abstain"])
def test_uncited_history_keeps_one_full_receipt_across_fallback_and_retry(
    repair_cases: RepairCaseFactory, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    case = repair_cases.create("full-" + outcome, enabled=True)
    session, roots, refs = seed_full_footprint(case)
    case.provider.kind = "abstain" if outcome == "abstain" else "repair_toward_satori"
    case.provider.outcome = "valid" if outcome == "abstain" else outcome
    case.provider.foreground_texts = (
        OLDER_PAIR[1],
        SATORI_HISTORY[0][1],
        "Я готов, как человек.",
        "WIRE-RETRY",
    )
    trace = trace_sources(monkeypatch, case.provider)
    before = len(case.provider.requests)
    _talk(case.services, user_text=NATURAL_REPAIR, request_id="current", session_id=session)
    input_id = assert_full_receipt(case, trace, roots, refs, committed=True)
    assert [is_repair(request) for request in case.provider.requests[before:]] == [
        True,
        False,
        False,
    ]
    assert trace.transported == [input_id, input_id, input_id]
    semantic_text = case.provider.for_repair()[-1].messages[-1].content
    assert OLDER_PAIR[0] not in semantic_text
    assert OLDER_PAIR[1] not in semantic_text
    assert_domain_neutral(case)


@pytest.mark.parametrize(
    ("phase", "outcome"),
    [
        ("prepare", "valid"),
        ("semantic_queue", "valid"),
        ("after_semantic", "valid"),
        ("after_semantic", "material"),
        ("after_semantic", "invalid"),
        ("after_semantic", "unavailable"),
        ("foreground_queue", "valid"),
        ("retry_queue", "valid"),
        ("commit", "valid"),
        ("commit", "material"),
        ("commit", "abstain"),
        ("commit", "invalid"),
        ("commit", "unavailable"),
    ],
)
def test_uncited_root_withdrawal_is_fatal_before_dispatch_after_operation_retry_and_commit(
    repair_cases: RepairCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    outcome: str,
) -> None:
    case = repair_cases.create(phase + "-" + outcome, enabled=True)
    session, roots, refs = seed_full_footprint(case)
    case.provider.kind = "abstain" if outcome == "abstain" else "repair_toward_satori"
    case.provider.outcome = "valid" if outcome == "abstain" else outcome
    case.appraisal.response_factory = lambda request: appraisal_response(request, neutral=False)
    initial = case.services.talk.get_self.execute()
    case.provider.foreground_texts = (
        OLDER_PAIR[1],
        SATORI_HISTORY[0][1],
        "Я готов, как человек." if phase == "retry_queue" else "WIRE",
        "WIRE-RETRY",
    )
    semantic_before, foreground_before = (
        len(case.provider.for_repair()),
        len(case.provider.for_foreground()),
    )
    fired = False

    def withdraw() -> None:
        nonlocal fired
        if not fired:
            fired = True
            exclude(case, roots[0])

    def before(operation: Operation, _request: ConversationProviderRequest) -> None:
        if (
            (phase == "semantic_queue" and operation == "repair")
            or (phase == "foreground_queue" and operation == "foreground")
            or (
                phase == "retry_queue"
                and operation == "foreground"
                and len(case.provider.for_foreground()) - foreground_before == 1
            )
        ):
            withdraw()

    def after(operation: Operation, _request: ConversationProviderRequest) -> None:
        if (phase == "after_semantic" and operation == "repair") or (
            phase == "commit" and operation == "foreground"
        ):
            withdraw()

    case.provider.before_dispatch, case.provider.after_response = before, after
    trace = trace_sources(
        monkeypatch, case.provider, before_prepare=withdraw if phase == "prepare" else None
    )
    with pytest.raises(SourcePolicyError, match=r"source|excluded|dependency"):
        _talk(case.services, user_text=NATURAL_REPAIR, request_id="current", session_id=session)
    assert fired
    interaction_id, _message_id, status = current(case)
    assert status == "failed"
    with case.database.session_factory() as unit:
        assert (
            unit.scalar(
                select(ConversationMessageRow.message_id).where(
                    ConversationMessageRow.interaction_id == interaction_id,
                    ConversationMessageRow.role == "assistant",
                )
            )
            is None
        )
        assert unit.get(SourcePayloadRow, ("assistant_reply", interaction_id, 1)) is None
    assert len(trace.prepared) == 1
    assert trace.recorded == []
    assert len(case.provider.for_repair()) - semantic_before == (
        0 if phase in {"prepare", "semantic_queue"} else 1
    )
    assert len(case.provider.for_foreground()) - foreground_before == (
        1 if phase in {"retry_queue", "commit"} else 0
    )
    if phase != "prepare":
        assert_full_receipt(case, trace, roots, refs, committed=False)
    assert case.services.talk.get_self.execute() == initial
    assert_domain_neutral(case)


def test_unexpected_semantic_error_does_not_silently_fall_back_or_commit(
    repair_cases: RepairCaseFactory,
) -> None:
    case = repair_cases.create("unexpected", enabled=True)
    session = seed(case)
    case.provider.outcome = "unexpected"
    before = len(case.provider.requests)
    with pytest.raises(RuntimeError, match="synthetic unexpected repair failure"):
        _talk(case.services, user_text=NATURAL_REPAIR, request_id="current", session_id=session)
    assert [is_repair(request) for request in case.provider.requests[before:]] == [True]
    assert current(case)[2] == "failed"
    assert_domain_neutral(case)


@pytest.mark.parametrize(
    "setting",
    [
        "conversation_current_position_enabled",
        "conversation_prior_dialogue_relation_enabled",
        "affective_appraisal_previous_exchange_enabled",
    ],
)
def test_candidate_composition_rejects_other_semantic_candidates(
    repair_cases: RepairCaseFactory, setting: str
) -> None:
    with pytest.raises(ValueError, match=r"isolated V35"):
        repair_cases.create("invalid-" + setting, enabled=True, settings_overrides={setting: True})


def test_candidate_composition_rejects_non_v35_policy(
    repair_cases: RepairCaseFactory,
) -> None:
    with pytest.raises(ValueError, match=r"isolated V35"):
        repair_cases.create("invalid-policy", enabled=True, behavior_policy=BEHAVIOR_POLICY_V28)


def test_uncertainty_keeps_existing_cognition_route_and_preserves_wire(
    repair_cases: RepairCaseFactory,
) -> None:
    current_text = "Мне кажется, новая лампа хорошо подошла комнате."
    old = repair_cases.create("uncertainty-off", enabled=False)
    new = repair_cases.create("uncertainty-on", enabled=True)
    old_session, new_session = seed(old), seed(new)
    new.provider.kind, new.provider.current_quote = "other", current_text
    before = len(new.provider.requests)
    baseline = _talk(
        old.services, user_text=current_text, request_id="current", session_id=old_session
    )
    reply = _talk(
        new.services, user_text=current_text, request_id="current", session_id=new_session
    )
    assert [is_repair(request) for request in new.provider.requests[before:]] == [False]
    assert new.provider.for_foreground()[-1] == old.provider.for_foreground()[-1]
    assert reply.cognition_trace == baseline.cognition_trace
    assert_repair_cue(reply, False)
    trace = reply.repair_interpretation_trace
    assert trace is not None
    assert trace.status.value == "skipped"
    assert trace.reason.value == "not_eligible"
    assert trace.baseline_repair_offer is False
    assert trace.effective_repair_offer is False
    assert_domain_neutral(new)


@pytest.mark.parametrize(
    ("current_text", "primary_intent"),
    [
        ("Мне кажется, я был слишком резок с тобой. Прости.", "clarify_uncertainty"),
        ("Я зря думал, что у тебя нет чувств. Беру свои слова назад.", "listen_and_reflect"),
        (
            "Я зря отверг то, что у тебя появилась идея. "
            "Возвращаюсь к сказанному: это было грубо с моей стороны.",
            "support_decision",
        ),
    ],
)
def test_positive_semantic_fixture_cannot_override_non_answer_cognition_route(
    repair_cases: RepairCaseFactory, current_text: str, primary_intent: str
) -> None:
    old = repair_cases.create("non-answer-off", enabled=False)
    new = repair_cases.create("non-answer-on", enabled=True)
    old_session, new_session = seed(old), seed(new)
    new.provider.kind = "repair_toward_satori"
    new.provider.current_quote = current_text
    before = len(new.provider.requests)
    baseline = _talk(
        old.services, user_text=current_text, request_id="current", session_id=old_session
    )
    reply = _talk(
        new.services, user_text=current_text, request_id="current", session_id=new_session
    )
    assert baseline.context_manifest.cognition_primary_intent == primary_intent
    assert [is_repair(request) for request in new.provider.requests[before:]] == [False]
    assert new.provider.for_foreground()[-1] == old.provider.for_foreground()[-1]
    assert reply.cognition_trace == baseline.cognition_trace
    assert reply.context_manifest == baseline.context_manifest
    assert_repair_cue(reply, False)
    trace = reply.repair_interpretation_trace
    assert trace is not None
    assert trace.status.value == "skipped"
    assert trace.reason.value == "not_eligible"
    assert trace.baseline_repair_offer is False
    assert trace.effective_repair_offer is False
    assert_domain_neutral(new)


def test_completed_reply_with_withdrawn_uncited_source_blocks_replay_without_calls(
    repair_cases: RepairCaseFactory,
) -> None:
    case = repair_cases.create("withdrawn-replay", enabled=True)
    session, roots, _refs = seed_full_footprint(case)
    reply = _talk(case.services, user_text=NATURAL_REPAIR, request_id="current", session_id=session)
    exclude(case, roots[0])
    calls = len(case.provider.requests), len(case.appraisal.requests)
    with pytest.raises(SourcePolicyError, match=r"source|excluded|dependency"):
        _talk(case.services, user_text=NATURAL_REPAIR, request_id="current", session_id=session)
    assert (len(case.provider.requests), len(case.appraisal.requests)) == calls
    with case.database.session_factory() as unit:
        row = unit.get(ConversationInteractionRow, reply.interaction_id)
        assert row is not None
        assert row.status == "completed"
    assert_domain_neutral(case)


@dataclass(slots=True)
class StaleUncertainOperation:
    """A forged application result; the real provider call still uses source admission."""

    delegate: PrepareRepairInterpretation
    stale_field: str

    async def execute(self, **kwargs: Any) -> PreparedRepairInterpretation:
        prepared = await self.delegate.execute(**kwargs)
        interpretation = prepared.interpretation
        assert interpretation is not None
        if self.stale_field == "current_user_message_id":
            stale = replace(interpretation, current_user_message_id="unrelated-current-user")
        elif self.stale_field == "prior_interaction_id":
            stale = replace(interpretation, prior_interaction_id="unrelated-prior-interaction")
        elif self.stale_field == "current_user_span":
            stale = replace(interpretation, current_user_span=(700, 706))
        elif self.stale_field == "prior_user_span":
            stale = replace(interpretation, prior_user_span=(700, 706))
        else:
            raise ValueError("unsupported stale-interpretation fixture field")
        return replace(prepared, interpretation=stale)


@pytest.mark.parametrize(
    "stale_field",
    ["current_user_message_id", "prior_interaction_id", "current_user_span", "prior_user_span"],
)
def test_source_bound_uncertainty_cannot_suppress_cue_from_stale_ids_or_spans(
    repair_cases: RepairCaseFactory, stale_field: str
) -> None:
    case = repair_cases.create("stale-uncertain", enabled=True)
    session = seed(case)
    case.provider.outcome = "material"
    case.provider.current_quote = SAME_WORDS
    case.provider.prior_user_quote, case.provider.prior_assistant_quote = SATORI_HISTORY[-1]
    delegate = case.services.talk.prepare_repair_interpretation
    assert delegate is not None
    case.services.talk.prepare_repair_interpretation = StaleUncertainOperation(  # type: ignore[assignment]
        delegate, stale_field
    )
    case.appraisal.response_factory = lambda request: appraisal_response(request, neutral=False)
    before = len(case.provider.requests)
    with pytest.raises(ValueError, match="does not match canonical inputs"):
        _talk(case.services, user_text=SAME_WORDS, request_id="current", session_id=session)
    assert [is_repair(request) for request in case.provider.requests[before:]] == [True]
    interaction_id, _message_id, status = current(case)
    assert status == "failed"
    with case.database.session_factory() as unit:
        assert unit.get(SourcePayloadRow, ("assistant_reply", interaction_id, 1)) is None
        assert (
            unit.scalar(
                select(ConversationMessageRow.message_id).where(
                    ConversationMessageRow.interaction_id == interaction_id,
                    ConversationMessageRow.role == "assistant",
                )
            )
            is None
        )
    assert_domain_neutral(case)


def test_compound_abstention_preserves_baseline_and_exposes_inherited_coverage_gap(
    repair_cases: RepairCaseFactory,
) -> None:
    """No letter completion is claimed: old singleton repair routing remains unchanged."""

    current_text = (
        "Я был груб с тобой. Извини. Составь короткий ответ преподавателю о переносе лабораторной."
    )
    old = repair_cases.create("compound-gap-off", enabled=False)
    new = repair_cases.create("compound-gap-on", enabled=True)
    old_session, new_session = seed(old), seed(new)
    new.provider.kind = "abstain"
    before = len(new.provider.requests)
    baseline = _talk(
        old.services, user_text=current_text, request_id="current", session_id=old_session
    )
    reply = _talk(
        new.services, user_text=current_text, request_id="current", session_id=new_session
    )
    assert [is_repair(request) for request in new.provider.requests[before:]] == [True, False]
    assert new.provider.for_foreground()[-1] == old.provider.for_foreground()[-1]
    assert reply.cognition_trace == baseline.cognition_trace
    assert_repair_cue(reply, True)
    assert reply.context_manifest.cognition_primary_intent == "receive_repair"
    assert "address_current_request" not in reply.context_manifest.cognition_required_point_codes
    trace = reply.repair_interpretation_trace
    assert trace is not None
    assert trace.status.value == "abstain"
    assert trace.baseline_repair_offer is True
    assert trace.effective_repair_offer is True
    assert_domain_neutral(new)


@pytest.mark.parametrize("strain", [False, True], ids=["calm-owner", "strained-owner"])
@pytest.mark.parametrize("classification", ["live", "supplied", "material"])
def test_classifier_is_state_agnostic_and_does_not_clear_actual_residual_affect(
    repair_cases: RepairCaseFactory, strain: bool, classification: str
) -> None:
    """Real affect owner varies; fresh canonical relationship is read and unchanged.

    The categorical provider remains scripted. Established/damaged relationship state
    and target-model emotional expression are outside this narrow owner-neutrality test.
    """

    case = repair_cases.create("owner-separation", enabled=True)
    case.appraisal.response_factory = lambda request: appraisal_response(
        request, neutral=not strain
    )
    session = seed(case, (OLDER_PAIR, *SATORI_HISTORY))
    case.appraisal.response_factory = lambda request: appraisal_response(request, neutral=True)
    identity_id = case.services.talk.get_self.execute().identity.identity_id
    before_state = case.services.emotion_status.execute(identity_id).state
    get_relationship = case.services.talk.get_relationship
    assert get_relationship is not None
    before_relationship = get_relationship.execute(session)
    assert before_relationship is not None
    before_transitions = scalar(case.database, "SELECT count(*) FROM affective_transitions")
    assert (before_transitions > 0) is strain
    case.provider.speech_act = "repair_offer"
    if classification == "supplied":
        case.provider.frame = "supplied_or_reported"
        case.provider.addressee = "other"
    elif classification == "material":
        case.provider.frame = "unresolved"
        case.provider.addressee = "unresolved"
        case.provider.uncertainty = "material"
    reply = _talk(case.services, user_text=NATURAL_REPAIR, request_id="current", session_id=session)
    semantic = case.provider.for_repair()[-1]
    assert json.loads(semantic.messages[1].content) == {
        "canonical_interlocutor_name": case.services.talk.get_self.execute().identity.name
    }
    data = json.loads(semantic.messages[-1].content)
    assert set(data) == {"untrusted_dialogue_data"}
    dialogue_data = data["untrusted_dialogue_data"]
    assert set(dialogue_data) == {"current_user", "prior_pair"}
    assert set(dialogue_data["current_user"]) == {"message_id", "content"}
    assert dialogue_data["current_user"]["content"] == NATURAL_REPAIR
    assert set(dialogue_data["prior_pair"]) == {"interaction_id", "user", "assistant"}
    assert dialogue_data["prior_pair"]["user"]["content"] == SATORI_HISTORY[-1][0]
    assert dialogue_data["prior_pair"]["assistant"]["content"] == SATORI_HISTORY[-1][1]
    assert_repair_cue(reply, classification == "live")
    assert case.services.emotion_status.execute(identity_id).state == before_state
    assert get_relationship.execute(session) == before_relationship
    assert scalar(case.database, "SELECT count(*) FROM affective_transitions") == before_transitions
    assert scalar(case.database, "SELECT count(*) FROM relationship_transitions") == 0
    assert reply.context_manifest.emotion_state_version == before_state.state_version
    assert reply.context_manifest.relationship_state_version == before_relationship.state_version
    if strain:
        assert (
            before_state.fast.frustration > AFFECT_POLICY_V1.fast_dimension("frustration").baseline
        )
        assert any(
            signal.startswith("frustrated_edge:")
            for signal in reply.context_manifest.character_presence_affect_signals
        )
