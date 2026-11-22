"""Transient semantic content through actual Talk, with managed source admission.

Scripted JSON and foreground text are transport fixtures, never model-quality evidence.
The tests do not inspect private diagnostic mappings or prescribe Satori utterances.
"""

# ruff: noqa: RUF001  # Public authored Russian inputs and synthetic proposal data.

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Literal

import pytest
from sqlalchemy import select

from satori.application.cognition.use_cases import (
    DeterministicCognitionPlanner,
    SafeCognitionPipeline,
)
from satori.application.conversation.contracts import ConversationContextManifest
from satori.application.conversation.current_position import (
    CURRENT_POSITION_DATA_KEY,
    CURRENT_POSITION_OPERATION_MARKER,
    build_current_position_request,
)
from satori.application.conversation.policy import BEHAVIOR_POLICY_V35
from satori.application.relationship.contracts import RelationshipExpressionContext
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
    ConversationProviderFailureReason,
    ConversationProviderRequest,
    ConversationProviderResponse,
    ConversationUsage,
    ProviderUnavailable,
)
from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.persistence.database import Database, create_database
from satori.infrastructure.persistence.migrations import upgrade_database
from satori.infrastructure.persistence.models import (
    ConversationInteractionRow,
    ConversationMessageRow,
    SourcePayloadRow,
)
from satori.infrastructure.source_policy.installation import (
    canonical_root,
    enroll_installation,
    repair_installation,
)
from satori.infrastructure.source_policy.runtime import SourcePolicyRuntime
from tests.current_position_diagnostic import (
    CURRENT_POSITION_DIAGNOSTIC,
    CURRENT_POSITION_DIAGNOSTIC_VERSION,
    CurrentPositionDiagnosticObservation,
)
from tests.fakes import FakeAffectiveAppraisalProvider, FrozenClock, SequenceIdGenerator
from tests.test_checkpoint142_character_delivery_v9 import _relationship as relationship_fixture
from tests.test_checkpoint143_character_agency_integration import (
    InvalidCompletionPlanner,
    _assert_cognition_preserved,
    _talk,
)
from tests.test_checkpoint143_v34_affect_expression import appraisal_response, scalar
from tests.test_checkpoint143_v37_context_use import NOW
from tests.test_conversation import activate, conversation_settings, skip_episode_provider

Operation = Literal["position", "foreground"]
Hook = Callable[[Operation, ConversationProviderRequest], None]
ORDINARY_INPUT = "У дверцы шкафа теперь снова работает магнит."
SYNTHETIC_TAKE = "Мне интересна возможность повторного ремонта, а не только быстрый результат."
DOMAIN_TABLES = (
    "affective_transitions",
    "relationship_transitions",
    "satori_positions",
    "satori_inclinations",
    "personality_revisions",
)


def operation(request: ConversationProviderRequest) -> Operation:
    last = request.messages[-1]
    return (
        "position"
        if last.role is ConversationMessageRole.DEVELOPER
        and last.content.startswith(CURRENT_POSITION_OPERATION_MARKER)
        else "foreground"
    )


def proposal_json(request: ConversationProviderRequest, *, take: str = SYNTHETIC_TAKE) -> str:
    allowed = json.loads(request.messages[-1].content.rsplit("\n", 1)[-1])["allowed_refs"]
    return json.dumps(
        {
            "schema_version": 1,
            "kind": "view",
            "focus": "обратимость решения",
            "current_take": take,
            "groundsrefs": [allowed[0]],
            "uncertainty": "material",
        },
        ensure_ascii=False,
    )


@dataclass(slots=True)
class OperationProvider:
    """Re-admit immediately before a synthetic transport attempt, including queue hooks."""

    outcome: str = "view"
    takes: tuple[str, ...] = (SYNTHETIC_TAKE,)
    foreground_texts: tuple[str, ...] = ()
    before_dispatch: Hook | None = None
    after_response: Hook | None = None
    entered: list[Operation] = field(default_factory=list, init=False)
    requests: list[ConversationProviderRequest] = field(default_factory=list, init=False)

    def for_operation(self, kind: Operation) -> list[ConversationProviderRequest]:
        return [request for request in self.requests if operation(request) == kind]

    async def generate(
        self, request: ConversationProviderRequest, /
    ) -> ConversationProviderResponse:
        kind = operation(request)
        self.entered.append(kind)
        if self.before_dispatch is not None:
            self.before_dispatch(kind, request)
        admit_provider_dispatch()
        self.requests.append(request)
        index = len(self.for_operation(kind)) - 1
        if kind == "position":
            if self.outcome == "unavailable":
                raise ProviderUnavailable(
                    provider="fixture",
                    model="no-network",
                    message="synthetic optional operation outage",
                    reason=ConversationProviderFailureReason.TRANSPORT_UNAVAILABLE,
                )
            if self.outcome == "unexpected":
                raise RuntimeError("synthetic unexpected failure")
            if self.outcome == "none":
                body = json.dumps(
                    {
                        "schema_version": 1,
                        "kind": "none",
                        "focus": "",
                        "current_take": "",
                        "groundsrefs": [],
                        "uncertainty": "low",
                    }
                )
            elif self.outcome == "invalid":
                body = "not JSON; no repair or retry"
            elif self.outcome == "foreign_ref":
                decoded = json.loads(proposal_json(request))
                decoded["groundsrefs"] = ["foreign-counterparty-root"]
                body = json.dumps(decoded)
            else:
                body = proposal_json(request, take=self.takes[min(index, len(self.takes) - 1)])
        else:
            body = (
                self.foreground_texts[index]
                if index < len(self.foreground_texts)
                else f"WIRE-{index:02d}"
            )
        response = ConversationProviderResponse(
            body,
            "fixture",
            "no-network",
            "stop",
            usage=ConversationUsage(input_tokens=100, output_tokens=8),
        )
        if self.after_response is not None:
            self.after_response(kind, request)
        return response


@dataclass(slots=True)
class ManagedCase:
    database: Database
    directory: Path
    runtime: SourcePolicyRuntime
    services: ConversationServices
    provider: OperationProvider
    appraisal: FakeAffectiveAppraisalProvider


def _services(
    database: Database,
    directory: Path,
    provider: OperationProvider,
    *,
    enabled: bool,
    counterparty: str = "local-default",
    prefix: str = "current-position",
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
            conversation_current_position_enabled=enabled,
            default_counterparty_id=counterparty,
        ),
        clock=FrozenClock(NOW),
        id_generator=SequenceIdGenerator(*(f"{prefix}-{index:04d}" for index in range(200))),
        appraisal_provider=appraisal,
        behavior_policy=BEHAVIOR_POLICY_V35,
        source_policy_required=True,
    )
    services.talk.monotonic = lambda: 0.0
    if services.talk.prepare_current_position is not None:
        services.talk.prepare_current_position.monotonic = lambda: 0.0
    services.talk.cognition_pipeline = SafeCognitionPipeline(
        planner=DeterministicCognitionPlanner(intent_registry_version=2, monotonic=lambda: 0.0),
        fallback=DeterministicCognitionPlanner(intent_registry_version=2, monotonic=lambda: 0.0),
    )
    return services, appraisal


@dataclass(slots=True)
class CaseFactory:
    root: Path
    project_root: Path
    databases: list[Database] = field(default_factory=list)

    def create(
        self, name: str, *, enabled: bool = True, provider: OperationProvider | None = None
    ) -> ManagedCase:
        database_url = f"sqlite+pysqlite:///{self.root / (name + '.db')}"
        upgrade_database(database_url, config_path=self.project_root / "alembic.ini")
        database = create_database(database_url)
        self.databases.append(database)
        activate(database)
        directory = self.root / (name + "-installation")
        runtime = enroll_installation(database, directory, reviewed=True, new_installation=True)
        transport = provider or OperationProvider()
        services, appraisal = _services(database, directory, transport, enabled=enabled)
        return ManagedCase(database, directory, runtime, services, transport, appraisal)


@pytest.fixture
def cases(tmp_path: Path, project_root: Path) -> Iterator[CaseFactory]:
    factory = CaseFactory(tmp_path, project_root)
    try:
        yield factory
    finally:
        for database in factory.databases:
            database.dispose()


def _current_data(request: ConversationProviderRequest) -> dict[str, object] | None:
    found: list[dict[str, object]] = []
    for message in request.messages:
        if message.role is not ConversationMessageRole.USER or not message.content.startswith("{"):
            continue
        parsed = json.loads(message.content)
        if isinstance(parsed, dict) and CURRENT_POSITION_DATA_KEY in parsed:
            found.append(parsed[CURRENT_POSITION_DATA_KEY])
    assert len(found) <= 1
    return found[0] if found else None


def _frozen_manifest(manifest: ConversationContextManifest) -> dict[str, object]:
    result = asdict(manifest)
    result.pop("schema_version")
    result["included_sections"] = tuple(
        section for section in result["included_sections"] if section != CURRENT_POSITION_DATA_KEY
    )
    for key in tuple(result):
        if key.startswith("current_position_"):
            result.pop(key)
    return result


def _assert_domain_neutral(case: ManagedCase) -> None:
    for table in DOMAIN_TABLES:
        assert scalar(case.database, f"SELECT count(*) FROM {table}") == 0


def test_disabled_control_retains_single_foreground_and_no_new_metadata(cases: CaseFactory) -> None:
    case = cases.create("disabled", enabled=False)
    assert conversation_settings().conversation_current_position_enabled is False
    reply = _talk(case.services, user_text=ORDINARY_INPUT, request_id="first")
    assert case.provider.entered == ["foreground"]
    assert reply.context_manifest.schema_version == 17
    assert reply.context_manifest.current_position_status is None
    assert _current_data(case.provider.requests[0]) is None
    _assert_domain_neutral(case)


def test_manifest_rejects_unknown_or_missing_fresh_operation_outcome(cases: CaseFactory) -> None:
    case = cases.create("manifest", provider=OperationProvider(outcome="none"))
    manifest = _talk(case.services, user_text=ORDINARY_INPUT, request_id="first").context_manifest
    for status, reason in ((None, None), ("unknown", None), ("none", "proposal_accepted")):
        with pytest.raises(ValueError, match="status and reason"):
            replace(manifest, current_position_status=status, current_position_reason=reason)


@pytest.mark.parametrize("after_operation", [False, True])
def test_optional_budget_denial_preserves_actual_foreground_and_available_usage(
    cases: CaseFactory, after_operation: bool
) -> None:
    old = cases.create("budget-control", enabled=False)
    _talk(old.services, user_text=ORDINARY_INPUT, request_id="first")
    base = old.provider.requests[0]
    operation_request = build_current_position_request(
        base, allowed_refs=("current-position-0002", "canonical_self")
    )
    trusted_roles = {ConversationMessageRole.SYSTEM, ConversationMessageRole.DEVELOPER}
    trusted_base = sum(
        len(message.content) for message in base.messages if message.role in trusted_roles
    )
    trusted_operation = sum(
        len(message.content)
        for message in operation_request.messages
        if message.role in trusted_roles
    )
    # JSON escaping can expand a bounded string. This adversarial transport datum has no
    # conversational meaning and tests the defensive post-operation bound, not character.
    case = cases.create("budget-candidate", provider=OperationProvider(takes=("\x00" * 480,)))
    case.services.talk.request_builder = replace(
        case.services.talk.request_builder,
        max_context_chars=trusted_operation if after_operation else trusted_base,
    )
    reply = _talk(case.services, user_text=ORDINARY_INPUT, request_id="first")
    assert reply.context_manifest.current_position_status == "skipped"
    assert reply.context_manifest.current_position_reason == "context_budget"
    assert case.provider.entered == (
        ["position", "foreground"] if after_operation else ["foreground"]
    )
    assert case.provider.for_operation("foreground")[0] == base
    assert (reply.current_position_usage is not None) is after_operation
    assert _current_data(case.provider.for_operation("foreground")[0]) is None
    _assert_domain_neutral(case)


@pytest.mark.parametrize("outcome", ["none", "invalid", "foreign_ref", "unavailable"])
def test_optional_absence_falls_back_to_exact_original_request(
    cases: CaseFactory, outcome: str
) -> None:
    old = cases.create("old", enabled=False)
    new = cases.create("new", provider=OperationProvider(outcome=outcome))
    baseline = _talk(old.services, user_text=ORDINARY_INPUT, request_id="first")
    reply = _talk(new.services, user_text=ORDINARY_INPUT, request_id="first")
    status = "invalid" if outcome == "foreign_ref" else outcome
    assert reply.context_manifest.current_position_status == status
    assert new.provider.entered == ["position", "foreground"]
    assert new.provider.for_operation("foreground") == old.provider.for_operation("foreground")
    assert _frozen_manifest(reply.context_manifest) == _frozen_manifest(baseline.context_manifest)
    assert reply.cognition_trace == baseline.cognition_trace
    assert _current_data(new.provider.for_operation("foreground")[0]) is None
    assert reply.text == baseline.text
    assert len(new.appraisal.requests) == len(old.appraisal.requests) == 1
    _assert_domain_neutral(new)


def test_accepted_content_reaches_actual_request_without_changing_frozen_movement(
    cases: CaseFactory,
) -> None:
    old = cases.create("old", enabled=False)
    new = cases.create("new", provider=OperationProvider(foreground_texts=(SYNTHETIC_TAKE,)))
    initial = new.services.talk.get_self.execute()
    baseline = _talk(old.services, user_text=ORDINARY_INPUT, request_id="first")
    reply = _talk(new.services, user_text=ORDINARY_INPUT, request_id="first")
    position, foreground = new.provider.requests
    assert position.messages[:-1] == old.provider.requests[0].messages
    assert position.parameters.max_output_tokens == 384
    assert position.parameters.temperature == 0.0
    assert foreground.parameters == old.provider.requests[0].parameters
    assert foreground.messages[-1] == old.provider.requests[0].messages[-1]
    data = _current_data(foreground)
    assert data is not None
    assert data["current_take"] == SYNTHETIC_TAKE
    assert data["uncertainty"] == "material"
    assert SYNTHETIC_TAKE not in "\n".join(
        message.content
        for message in foreground.messages
        if message.role is ConversationMessageRole.DEVELOPER
    )
    assert _frozen_manifest(reply.context_manifest) == _frozen_manifest(baseline.context_manifest)
    assert reply.cognition_trace == baseline.cognition_trace
    _assert_cognition_preserved(reply.context_manifest, reply.cognition_trace)
    assert reply.text == SYNTHETIC_TAKE  # Provider text is committed verbatim, never rewritten.
    assert reply.context_manifest.current_position_status == "applied"
    assert new.services.talk.get_self.execute() == initial
    _assert_domain_neutral(new)
    with new.database.session_factory() as unit:
        receipt = unit.get_one(SourcePayloadRow, ("assistant_reply", reply.interaction_id, 1))
        assert receipt.eligible
        assert receipt.proof_status == "complete"
        assert not receipt.legacy
        assert len(receipt.root_message_ids) == 1


@pytest.mark.parametrize(
    "user_text",
    [
        "Сделай краткий пересказ нашего разговора.",
        "Объясни два пункта: что делает append и чем он отличается от extend в Python.",
        "Повтори точно: «Красная коробка слева».",
        "Мне тяжело. Просто послушай, без советов.",
        "Давай на этом закончим тему.",
        "Прости, я был резок. Давай попробуем спокойнее.",
        "Не задавай в конце дежурный вопрос. Отвечай прямо.",
        "Я выжат, но всё равно продолжу работать через силу.",
    ],
)
def test_protected_and_explicit_substance_routes_skip_optional_operation(
    cases: CaseFactory, user_text: str
) -> None:
    old = cases.create("old", enabled=False)
    new = cases.create("new")
    baseline = _talk(old.services, user_text=user_text, request_id="first")
    reply = _talk(new.services, user_text=user_text, request_id="first")
    assert new.provider.entered == ["foreground"]
    assert reply.context_manifest.current_position_status == "skipped"
    assert new.provider.requests == old.provider.requests
    assert _frozen_manifest(reply.context_manifest) == _frozen_manifest(baseline.context_manifest)
    assert reply.cognition_trace == baseline.cognition_trace


@pytest.mark.parametrize(
    ("user_text", "provider_text"),
    [
        ("Посчитай сумму 12 и 18.", "12 + 18 = 30."),
        ("Подведи итог этого разговора.", "SYNTHETIC-REQUESTED-SUMMARY"),
        (
            "Я не спал двое суток, но буду работать ещё ночь.",
            "SYNTHETIC-UNRECOGNIZED-STATE",
        ),
    ],
)
def test_unrecognized_requests_allow_none_without_reclassifying_or_rewriting(
    cases: CaseFactory, user_text: str, provider_text: str
) -> None:
    """A transport control: no new task/safety classifier or semantic success claim."""

    old = cases.create(
        "old", enabled=False, provider=OperationProvider(foreground_texts=(provider_text,))
    )
    new = cases.create(
        "new", provider=OperationProvider(outcome="none", foreground_texts=(provider_text,))
    )
    baseline = _talk(old.services, user_text=user_text, request_id="first")
    reply = _talk(new.services, user_text=user_text, request_id="first")
    assert new.provider.entered == ["position", "foreground"]
    assert reply.context_manifest.current_position_status == "none"
    assert new.provider.for_operation("foreground") == old.provider.for_operation("foreground")
    assert _frozen_manifest(reply.context_manifest) == _frozen_manifest(baseline.context_manifest)
    assert reply.cognition_trace == baseline.cognition_trace
    position = new.provider.for_operation("position")[0]
    assert "summary, quotation, calculation" in position.messages[-1].content
    assert "None is a valid result." in position.messages[-1].content
    assert new.provider.for_operation("foreground")[0].messages[-1].content == user_text
    assert reply.text == provider_text
    _assert_domain_neutral(new)


def test_cognition_fallback_cannot_acquire_a_semantic_optional_move(cases: CaseFactory) -> None:
    case = cases.create("fallback")
    case.services.talk.cognition_pipeline = SafeCognitionPipeline(
        planner=InvalidCompletionPlanner(),  # type: ignore[arg-type]
        fallback=DeterministicCognitionPlanner(intent_registry_version=2, monotonic=lambda: 0.0),
    )
    reply = _talk(case.services, user_text=ORDINARY_INPUT, request_id="first")
    assert reply.context_manifest.character_agency_status == "fallback"
    assert reply.context_manifest.current_position_status == "skipped"
    assert case.provider.entered == ["foreground"]


def test_foreground_retry_reuses_one_datum_and_replay_does_not_recompute(
    cases: CaseFactory,
) -> None:
    case = cases.create(
        "retry",
        provider=OperationProvider(foreground_texts=("Я готов, как человек.", "WIRE-RETRY")),
    )
    reply = _talk(case.services, user_text=ORDINARY_INPUT, request_id="first")
    assert case.provider.entered == ["position", "foreground", "foreground"]
    first, retried = case.provider.for_operation("foreground")
    assert _current_data(first) == _current_data(retried)
    assert first.parameters == retried.parameters
    assert first.messages[-2].role is ConversationMessageRole.DEVELOPER
    assert retried.messages[-2].role is ConversationMessageRole.DEVELOPER
    assert first.messages[:-2] == retried.messages[:-2]
    assert first.messages[-1] == retried.messages[-1]
    assert reply.context_manifest.regeneration_reason == "masculine_self_reference"
    replay = _talk(case.services, user_text=ORDINARY_INPUT, request_id="first")
    assert replay.replayed
    assert replay.text == "WIRE-RETRY"
    assert case.provider.entered == ["position", "foreground", "foreground"]
    assert len(case.appraisal.requests) == 1
    assert replay.context_manifest.current_position_status is None
    assert CURRENT_POSITION_DATA_KEY not in replay.context_manifest.included_sections
    _assert_domain_neutral(case)


def _exclude_prior(case: ManagedCase, message_id: str) -> None:
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


@pytest.mark.parametrize(
    ("phase", "outcome"),
    [
        ("position_dispatch", "view"),
        ("foreground_dispatch", "view"),
        ("canonical_commit", "view"),
        ("foreground_dispatch", "none"),
        ("canonical_commit", "none"),
        ("foreground_dispatch", "invalid"),
        ("canonical_commit", "unavailable"),
    ],
)
def test_uncited_history_exclusion_rechecks_both_dispatches_and_final_commit(
    cases: CaseFactory, phase: str, outcome: str
) -> None:
    case = cases.create("source", provider=OperationProvider(outcome=outcome))
    session = case.services.start_session.execute().session_id
    prior = _talk(
        case.services,
        user_text="Объясни два пункта: что делает append и чем он отличается от extend в Python.",
        request_id="prior",
        session_id=session,
    )
    assert prior.context_manifest.current_position_status == "skipped"
    assert case.provider.entered == ["foreground"]
    prior_interaction = case.services.talk.interaction_log.get(prior.interaction_id)
    assert prior_interaction is not None
    root_id = prior_interaction.user_message.message_id
    fired = False

    def hook(kind: Operation, _request: ConversationProviderRequest) -> None:
        nonlocal fired
        target = "position" if phase == "position_dispatch" else "foreground"
        if not fired and kind == target:
            fired = True
            _exclude_prior(case, root_id)

    if phase == "canonical_commit":
        case.provider.after_response = hook
    else:
        case.provider.before_dispatch = hook
    with pytest.raises(SourcePolicyError):
        _talk(case.services, user_text=ORDINARY_INPUT, request_id="current", session_id=session)
    assert fired
    expected_positions = 0 if phase == "position_dispatch" else 1
    expected_foreground = 2 if phase == "canonical_commit" else 1
    assert len(case.provider.for_operation("position")) == expected_positions
    assert len(case.provider.for_operation("foreground")) == expected_foreground
    with case.database.session_factory() as unit:
        current = unit.scalar(
            select(ConversationInteractionRow).where(
                ConversationInteractionRow.client_request_id == "current"
            )
        )
        assert current is not None
        assert current.status == "failed"
        assert (
            unit.scalar(
                select(ConversationMessageRow.message_id).where(
                    ConversationMessageRow.interaction_id == current.interaction_id,
                    ConversationMessageRow.role == "assistant",
                )
            )
            is None
        )
        assert unit.get(SourcePayloadRow, ("assistant_reply", current.interaction_id, 1)) is None
    _assert_domain_neutral(case)


def test_real_owner_affect_is_preserved_without_new_relationship_or_position_evidence(
    cases: CaseFactory,
) -> None:
    old = cases.create("old", enabled=False)
    new = cases.create("new")
    for case in (old, new):
        case.appraisal.response_factory = lambda request: appraisal_response(request, neutral=False)
    baseline = _talk(old.services, user_text=ORDINARY_INPUT, request_id="first")
    reply = _talk(new.services, user_text=ORDINARY_INPUT, request_id="first")
    position = new.provider.for_operation("position")[0]
    assert position.messages[:-1] == old.provider.requests[0].messages
    assert _frozen_manifest(reply.context_manifest) == _frozen_manifest(baseline.context_manifest)
    assert reply.cognition_trace == baseline.cognition_trace
    for case in (old, new):
        assert scalar(case.database, "SELECT count(*) FROM affective_transitions") == 1
        assert scalar(case.database, "SELECT count(*) FROM relationship_transitions") == 0
        assert scalar(case.database, "SELECT count(*) FROM satori_positions") == 0
    assert reply.context_manifest.relationship_state_version == (
        baseline.context_manifest.relationship_state_version
    )


@dataclass(frozen=True, slots=True)
class ControlledRelationshipRead:
    """A read-projection control, not evidence about relationship owner formation."""

    context: RelationshipExpressionContext

    def execute(self, _session_id: str) -> RelationshipExpressionContext:
        return self.context


def test_relationship_input_varies_independently_from_owner_affect(cases: CaseFactory) -> None:
    observations = []
    for name in ("fresh", "developing", "established"):
        old = cases.create(name + "-old", enabled=False)
        new = cases.create(name + "-new")
        for case in (old, new):
            case.services.talk.get_relationship = ControlledRelationshipRead(  # type: ignore[assignment]
                relationship_fixture(name)
            )
        baseline = _talk(old.services, user_text=ORDINARY_INPUT, request_id="first")
        reply = _talk(new.services, user_text=ORDINARY_INPUT, request_id="first")
        position = new.provider.for_operation("position")[0]
        assert position.messages[:-1] == old.provider.requests[0].messages
        assert _frozen_manifest(reply.context_manifest) == _frozen_manifest(
            baseline.context_manifest
        )
        assert reply.cognition_trace == baseline.cognition_trace
        observations.append((position, reply.context_manifest))
        _assert_domain_neutral(new)
    # The ordinary wire omits this weak relationship contrast; metadata alone is not exposure.
    assert observations[0][0].messages == observations[1][0].messages
    # Established relationship changes the existing frozen realization before the new operation.
    assert observations[0][0].messages != observations[2][0].messages
    assert len({observation[1].emotion_state_version for observation in observations}) == 1
    assert len({observation[1].mood_state_version for observation in observations}) == 1
    assert (
        observations[0][1].relationship_state_version
        != observations[1][1].relationship_state_version
    )


def test_unexpected_operation_error_is_not_silently_treated_as_optional_none(
    cases: CaseFactory,
) -> None:
    case = cases.create("unexpected", provider=OperationProvider(outcome="unexpected"))
    with pytest.raises(RuntimeError, match="synthetic unexpected failure"):
        _talk(case.services, user_text=ORDINARY_INPUT, request_id="first")
    assert case.provider.entered == ["position"]
    assert case.provider.for_operation("foreground") == []
    _assert_domain_neutral(case)


def test_transient_datum_and_history_do_not_cross_counterparty(cases: CaseFactory) -> None:
    case = cases.create("people")
    a_provider = OperationProvider(takes=("SYNTHETIC-A-ONLY",), foreground_texts=("A-REPLY",))
    a, _ = _services(
        case.database, case.directory, a_provider, enabled=True, counterparty="person-a", prefix="a"
    )
    b_provider = OperationProvider(outcome="none")
    b, _ = _services(
        case.database, case.directory, b_provider, enabled=True, counterparty="person-b", prefix="b"
    )
    _talk(a, user_text=ORDINARY_INPUT, request_id="person-a")
    reply_b = _talk(b, user_text=ORDINARY_INPUT, request_id="person-b")
    assert reply_b.context_manifest.current_position_status == "none"
    for request in b_provider.requests:
        serialized = repr(request.messages)
        assert "SYNTHETIC-A-ONLY" not in serialized
        assert "A-REPLY" not in serialized
        assert "a-000" not in serialized
    assert _current_data(b_provider.for_operation("foreground")[0]) is None
    _assert_domain_neutral(case)


def test_public_five_turn_diagnostic_records_content_path_and_protected_skips(
    cases: CaseFactory,
) -> None:
    assert CURRENT_POSITION_DIAGNOSTIC_VERSION == 1
    assert len(CURRENT_POSITION_DIAGNOSTIC) == 5
    old = cases.create("old", enabled=False)
    provider = OperationProvider()
    new = cases.create("new", provider=provider)
    old_session = old.services.start_session.execute().session_id
    new_session = new.services.start_session.execute().session_id
    initial = new.services.talk.get_self.execute()
    observations: list[CurrentPositionDiagnosticObservation] = []
    for index, turn in enumerate(CURRENT_POSITION_DIAGNOSTIC):
        provider.takes = (turn.synthetic_take,)
        before_position = len(provider.for_operation("position"))
        before_foreground = len(provider.for_operation("foreground"))
        baseline = _talk(
            old.services,
            user_text=turn.user_text,
            request_id=f"turn-{index}",
            session_id=old_session,
        )
        reply = _talk(
            new.services,
            user_text=turn.user_text,
            request_id=f"turn-{index}",
            session_id=new_session,
        )
        foreground = provider.for_operation("foreground")[-1]
        datum = _current_data(foreground)
        observations.append(
            CurrentPositionDiagnosticObservation(
                name=turn.name,
                status=reply.context_manifest.current_position_status,
                proposal_calls=len(provider.for_operation("position")) - before_position,
                foreground_calls=len(provider.for_operation("foreground")) - before_foreground,
                proposal_content_reached_request=datum is not None,
                agency_unchanged=_frozen_manifest(reply.context_manifest)
                == _frozen_manifest(baseline.context_manifest),
                cognition_unchanged=reply.cognition_trace == baseline.cognition_trace,
                persistent_self_unchanged=new.services.talk.get_self.execute() == initial,
            )
        )
        assert foreground.messages[-1].content == turn.user_text
        if datum is not None:
            assert datum["current_take"] == turn.synthetic_take
    assert any(row.proposal_content_reached_request for row in observations)
    assert observations[-1].status == "skipped"
    assert all(row.foreground_calls == 1 for row in observations)
    assert all(row.agency_unchanged and row.cognition_unchanged for row in observations)
    assert all(row.persistent_self_unchanged and row.human_score is None for row in observations)
    _assert_domain_neutral(new)
