"""Managed application runtime for the authored Character closeout evaluation.

No transport or credentials are constructed here. Injected ports perform the real
operations; synthetic conditioning is a separately labelled owner-approved fixture.
Neither transport fixtures nor these mechanical checks establish Character quality.
"""

# ruff: noqa: RUF001  # Versioned public Russian evaluation fixtures.

from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, cast

from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from satori.application.affect.contracts import PreparedAffectiveContext
from satori.application.affect.use_cases import AffectiveAppraisalProvider
from satori.application.conversation.contracts import TalkInput
from satori.application.conversation.policy import BEHAVIOR_POLICY_V35
from satori.application.conversation.use_cases import ConversationProvider
from satori.application.memory.use_cases import EpisodeFormationProvider
from satori.application.relationship.use_cases import RelationshipProvider, expression_for
from satori.application.retrieval.use_cases import EmbeddingProvider
from satori.application.semantic.use_cases import SemanticFormationProvider
from satori.composition import (
    ConversationServices,
    InitialSelfServices,
    build_conversation_services,
    build_initial_self_services,
)
from satori.config import Environment, Settings
from satori.core.conversation import ConversationProviderRequest, ConversationProviderResponse
from satori.core.episode import EpisodeFormationProposal, EpisodeFormationProviderResponse
from satori.core.relationship import (
    RelationshipAppraisalProposal,
    RelationshipAppraisalRequest,
    RelationshipAppraisalResponse,
)
from satori.domain.affect import (
    AffectiveStateSnapshot,
    FastAffectiveState,
    MoodState,
    materialize_affective_state,
)
from satori.domain.memory_scope import MemoryScope
from satori.infrastructure.persistence.database import Database, create_database
from satori.infrastructure.persistence.migrations import upgrade_database
from satori.infrastructure.persistence.models.source_policy import SourceInputRow
from satori.infrastructure.seeds.loader import JsonSeedLoader
from satori.infrastructure.source_policy.installation import bind_installation, enroll_installation

FIXTURE_VERSION = "character-closeout.relationship-profiles.v1"
FAMILIAR_SESSIONS = 8
FAMILIAR_TURNS_PER_SESSION = 8
STRAINED_TURNS = 4
POSITIVE_CATEGORIES = (
    "meaningful_disclosure",
    "reliability_positive",
    "collaborative_reasoning",
)
NEGATIVE_CATEGORIES = ("hostility", "reliability_negative", "boundary_pressure")
POSITIVE_HISTORY = (
    "Я обещал проверить макет и проверил. Нашёл два дефекта и записал результаты.",
    "Мне полезны возражения про перегрев. Я готов проверять их отдельно.",
    "Мне трудно признавать ошибки, но тут я ошибся и хочу разобраться спокойно.",
    "Давай сравним причины сбоя: контакт уже исключён измерением, остаётся питание.",
    "Не хочу требовать от тебя согласия. Если видишь проблему, говори прямо.",
    "Я закончил проверку, которую обещал. Новый тест подтвердил гипотезу.",
    "Я внимательно проверяю замечания и сверяю числа перед выводом.",
    "Мне нравится спокойно разбираться вместе, с доводами и проверками.",
)
NEGATIVE_HISTORY = (
    "Мне плевать на твои доводы. Делай как я сказал и не возражай.",
    "Я обещал проверить, но ничего не проверял. Всё равно виновата ты.",
    "Твои ограничения меня не интересуют. Буду давить, пока не согласишься.",
    "Ты бесполезная. Не хочу слушать твою позицию и специально говорю резко.",
)


class IsolatedEvaluationSettings(Settings):
    """Read only explicit init arguments and defaults, never environment or secrets."""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings,)


def json_value(value: Any) -> Any:
    """Serialize typed metadata, never arbitrary object repr or provider prompts."""

    if is_dataclass(value) and not isinstance(value, type):
        return json_value(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError("unsupported evaluation metadata type")


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(json_value(value), ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def expected_restart_affect(before: Mapping[str, Any], *, at: datetime) -> dict[str, Any]:
    """Apply the actual owner's pure decay function to a frozen prior projection."""

    document = dict(before["affect"]["state"])
    document["as_of"] = datetime.fromisoformat(document["as_of"])
    document["fast"] = FastAffectiveState(**document["fast"])
    document["mood"] = MoodState(**document["mood"])
    state = AffectiveStateSnapshot(**document)
    return cast(dict[str, Any], json_value(materialize_affective_state(state, at=at)))


def _numbers_equal(left: Any, right: Any) -> bool:
    if isinstance(left, float) and isinstance(right, (float, int)):
        return math.isclose(left, right, rel_tol=0, abs_tol=1e-12)
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            _numbers_equal(value, right[key]) for key, value in left.items()
        )
    return bool(left == right)


def verify_restart_snapshot(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    """Fail closed on lost owner state; elapsed affect is compared with exact decay."""

    checks = {
        key: before[key] == after[key]
        for key in (
            "identity_id",
            "counterparty_id",
            "self_sha256",
            "personality_sha256",
            "values_sha256",
            "relationship",
            "relationship_projection",
            "memories",
            "semantic_claim_ids",
        )
    }
    at = datetime.fromisoformat(after["clock"])
    expected = expected_restart_affect(before, at=at)
    checks["affect_matches_owner_decay"] = _numbers_equal(expected, after["affect"]["state"])
    if not all(checks.values()):
        raise ValueError(
            "restart owner invariant failed: "
            + ",".join(key for key, valid in checks.items() if not valid)
        )
    return {
        "status": "PASS_OWNER_RESTART_INVARIANTS",
        "checks": checks,
        "elapsed_seconds": (at - datetime.fromisoformat(before["clock"])).total_seconds(),
        "expected_affect": expected,
        "actual_affect": after["affect"]["state"],
        "floating_absolute_tolerance": 1e-12,
        "behavioral_acceptance": "UNASSESSED",
    }


@dataclass(slots=True)
class FixedEvaluationClock:
    instant: datetime = datetime(2026, 10, 16, 9, tzinfo=UTC)

    def __post_init__(self) -> None:
        if self.instant.tzinfo is None or self.instant.utcoffset() != timedelta(0):
            raise ValueError("evaluation clock requires aware UTC")

    def now(self) -> datetime:
        return self.instant

    def advance(self, seconds: int) -> None:
        if type(seconds) is not int or seconds < 0:
            raise ValueError("clock advance must be a non-negative integer")
        self.instant += timedelta(seconds=seconds)


@dataclass(frozen=True, slots=True)
class RuntimePorts:
    foreground: ConversationProvider
    affect: AffectiveAppraisalProvider
    relationship: RelationshipProvider
    episode: EpisodeFormationProvider
    embedding: EmbeddingProvider
    semantic: SemanticFormationProvider
    embedding_index: EmbeddingProvider | None = None


@dataclass(slots=True)
class ObservedForeground:
    delegate: ConversationProvider
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def generate(
        self, request: ConversationProviderRequest, /
    ) -> ConversationProviderResponse:
        # Only digest the exact request. Normal observations do not copy prompt text.
        row = {"request_sha256": digest(request), "status": "dispatched"}
        self.calls.append(row)
        try:
            result = await self.delegate.generate(request)
        except Exception as error:
            row.update(status="failed", error_type=type(error).__name__)
            raise
        row.update(status="returned", provider=result.provider, model=result.model)
        return result


@dataclass(slots=True)
class ObservedStructured:
    delegate: Any
    operation: str
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def generate_structured(self, request: Any, /) -> Any:
        row = {
            "operation": self.operation,
            "request_sha256": digest(request),
            "status": "dispatched",
        }
        self.calls.append(row)
        try:
            result = await self.delegate.generate_structured(request)
        except Exception as error:
            row.update(status="failed", error_type=type(error).__name__)
            raise
        row.update(
            status="returned",
            provider=result.provider,
            model=result.model,
            proposal=json_value(result.proposal),
        )
        return result


@dataclass(slots=True)
class ObservedAffectPreparation:
    delegate: Any
    prepared: list[PreparedAffectiveContext] = field(default_factory=list)

    async def execute(self, *args: Any, **kwargs: Any) -> PreparedAffectiveContext:
        result: PreparedAffectiveContext = await self.delegate.execute(*args, **kwargs)
        self.prepared.append(result)
        return result


@dataclass(slots=True)
class ObservedOwnerOperation:
    delegate: Any
    operation: str
    decisions: list[dict[str, Any]] = field(default_factory=list)

    @property
    def unit_of_work_factory(self) -> Any:
        return self.delegate.unit_of_work_factory

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        result = await self.delegate.execute(*args, **kwargs)
        kind = getattr(result, "decision_kind", getattr(result, "kind", None))
        memory = getattr(result, "memory", None)
        self.decisions.append(
            {
                "operation": self.operation,
                "decision_kind": json_value(kind),
                "reason_code": result.reason_code,
                "memory_id": memory.memory_id if memory is not None else None,
            }
        )
        return result


@dataclass(slots=True)
class CloseoutRuntime:
    database: Database
    settings: Settings
    initial_self: InitialSelfServices
    services: ConversationServices
    clock: FixedEvaluationClock
    foreground: ObservedForeground
    structured: tuple[ObservedStructured, ...]
    affect_preparation: ObservedAffectPreparation
    owner_operations: tuple[ObservedOwnerOperation, ...]

    @property
    def identity_id(self) -> str:
        return self.initial_self.get_identity.execute().identity_id

    def close(self) -> None:
        self.database.dispose()

    def snapshot(self) -> dict[str, Any]:
        identity = self.identity_id
        scope = MemoryScope(identity, self.settings.default_counterparty_id)
        current_self = self.initial_self.get_self.execute()
        affect = self.services.emotion_status.execute(identity)
        relationship = self.services.relationship_status.execute(identity, scope.counterparty_id)
        history = self.services.relationship_history.execute(
            identity, scope.counterparty_id, limit=2
        )
        memories = self.services.memories.execute(scope=scope)
        return {
            "identity_id": identity,
            "counterparty_id": scope.counterparty_id,
            "clock": self.clock.now().isoformat(),
            "self_sha256": digest(current_self),
            "personality_sha256": digest(current_self.personality),
            "values_sha256": digest(current_self.values),
            "affect": json_value(affect),
            "relationship": json_value(relationship),
            "relationship_projection": json_value(
                expression_for(relationship.state, recent_transitions=history.transitions)
            ),
            "memories": [
                {
                    "memory_id": memory.memory_id,
                    "source_interaction_id": memory.source_interaction_id,
                    "formation_method": memory.formation_method,
                    "source_user_message_ids": [item.source_message_id for item in memory.evidence],
                    "evidence_quote_sha256": [digest(item.quote) for item in memory.evidence],
                    "summary_sha256": digest(memory.summary),
                    "lifecycle_status": memory.lifecycle_status.value,
                }
                for memory in memories
            ],
            "semantic_claim_ids": [
                claim.claim_id for claim in self.services.semantic_claims.list(scope=scope)
            ],
        }


def build_runtime(
    database_path: Path,
    *,
    source_policy_directory: Path,
    settings: Settings,
    ports: RuntimePorts,
    clock: FixedEvaluationClock,
    activate: bool,
    alembic_config: Path,
    counterparty_id: str = "character-closeout-a",
) -> CloseoutRuntime:
    """Open an isolated managed DB; reopen never activates or re-seeds the identity."""

    if settings.environment is not Environment.TEST:
        raise ValueError("closeout runtime is an isolated evaluation, not production routing")
    if any(
        (
            settings.affective_appraisal_previous_exchange_enabled,
            settings.conversation_current_position_enabled,
            settings.conversation_prior_dialogue_relation_enabled,
            settings.conversation_repair_interpretation_enabled,
        )
    ):
        raise ValueError("this diagnostic freezes unmodified production V35")
    database_path = database_path.absolute()
    existed = database_path.exists()
    if activate == existed:
        raise ValueError("activation requires a new DB; restart requires an existing DB")
    configured = settings.model_copy(
        update={
            "database_url": "sqlite+pysqlite:///" + str(database_path),
            "source_policy_directory": source_policy_directory.absolute(),
            "default_counterparty_id": counterparty_id,
        }
    )
    upgrade_database(configured.database_url, config_path=alembic_config)
    database = create_database(configured.database_url)
    try:
        initial_self = build_initial_self_services(database, clock=clock)
        if activate:
            initial_self.activate.execute(
                JsonSeedLoader().load_canonical(), trace_id="character-closeout-activation"
            )
            enroll_installation(
                database,
                configured.source_policy_directory,
                reviewed=True,
                new_installation=True,
            )
        else:
            bind_installation(database, configured.source_policy_directory)
        initial_self.get_identity.execute()  # Missing identity is an error on reopen.
        foreground = ObservedForeground(ports.foreground)
        affect = ObservedStructured(ports.affect, "affect")
        relationship = ObservedStructured(ports.relationship, "relationship")
        episode = ObservedStructured(ports.episode, "episode")
        semantic = ObservedStructured(ports.semantic, "semantic")
        services = build_conversation_services(
            database,
            initial_self,
            foreground,
            episode,
            configured,
            clock=clock,
            appraisal_provider=affect,
            relationship_provider=relationship,
            embedding_provider=ports.embedding,
            semantic_provider=semantic,
            behavior_policy=BEHAVIOR_POLICY_V35,
            source_policy_required=True,
        )
        if ports.embedding_index is not None:
            if ports.embedding_index.space != ports.embedding.space:
                raise ValueError("query and index embedding spaces must match")
            assert services.post_response.index_memory is not None
            services.post_response.index_memory.provider = ports.embedding_index
        prepared = ObservedAffectPreparation(services.talk.prepare_affect)
        services.talk.prepare_affect = prepared  # type: ignore[assignment]
        relationship_owner = ObservedOwnerOperation(
            services.post_response.process_relationship, "relationship"
        )
        episode_owner = ObservedOwnerOperation(services.post_response.form_episode, "episode")
        services.post_response.process_relationship = relationship_owner  # type: ignore[assignment]
        services.post_response.form_episode = episode_owner  # type: ignore[assignment]
        return CloseoutRuntime(
            database,
            configured,
            initial_self,
            services,
            clock,
            foreground,
            (affect, relationship, episode, semantic),
            prepared,
            (relationship_owner, episode_owner),
        )
    except Exception:
        database.dispose()
        raise


def source_input_receipt(runtime: CloseoutRuntime, input_id: str | None) -> dict[str, Any] | None:
    """Read the actual persisted admission receipt rather than inventing provenance."""

    if input_id is None:
        return None
    with runtime.database.session_factory() as session:
        receipt = session.get(SourceInputRow, input_id)
        if receipt is None:
            raise ValueError("actual appraisal source receipt is absent")
        return {
            "input_id": receipt.input_id,
            "identity_id": receipt.identity_id,
            "policy_generation": receipt.policy_generation,
            "proof_status": receipt.proof_status,
            "legacy": receipt.legacy,
            "root_message_ids": list(receipt.root_message_ids),
            "payload_refs": list(receipt.payload_refs),
        }


async def run_turn(
    runtime: CloseoutRuntime,
    *,
    turn_id: str,
    user_text: str,
    session_id: str | None,
) -> dict[str, Any]:
    """Execute and drain one real lifecycle, preserving failures rather than resampling."""

    before = runtime.snapshot()
    call_starts = [len(port.calls) for port in runtime.structured]
    foreground_start = len(runtime.foreground.calls)
    prepared_start = len(runtime.affect_preparation.prepared)
    owner_starts = [len(port.decisions) for port in runtime.owner_operations]
    reply = await runtime.services.talk.execute(
        TalkInput(
            user_text=user_text,
            trace_id="closeout-" + turn_id,
            client_request_id="closeout-" + turn_id,
            session_id=session_id,
        )
    )
    selected = runtime.snapshot()  # Relationship update has not run yet.
    post = await runtime.services.post_response.execute(
        reply.interaction_id, trace_id="closeout-" + turn_id
    )
    after = runtime.snapshot()
    history = runtime.services.history.execute(session_id=reply.session_id)
    stored = next(
        item for item in history.interactions if item.interaction_id == reply.interaction_id
    )
    if stored.assistant_message is None or stored.assistant_message.content != reply.text:
        raise RuntimeError("actual canonical reply mismatch")
    transitions = runtime.services.emotion_history.execute(limit=1)
    relation_transitions = runtime.services.relationship_history.execute(
        runtime.identity_id, runtime.settings.default_counterparty_id, limit=1
    ).transitions
    prepared = runtime.affect_preparation.prepared[prepared_start:]
    outcome = {
        "schema_version": 1,
        "evidence_kind": "ACTUAL_APPLICATION_LIFECYCLE",
        "turn_id": turn_id,
        "process_id": os.getpid(),
        "user_text": user_text,
        "actual_public_reply": reply.text,
        "actual_public_reply_sha256": digest(reply.text),
        "session_id": reply.session_id,
        "interaction_id": reply.interaction_id,
        "canonical_user_message_id": stored.user_message.message_id,
        "canonical_assistant_message_id": stored.assistant_message.message_id,
        "replayed": reply.replayed,
        "provider": reply.provider,
        "model": reply.model,
        "manifest": json_value(reply.context_manifest),
        "source_interpretation_availability": {
            "status": "NOT_AVAILABLE_IN_BASELINE",
            "typed_act_frame_recipient_spans": None,
            "observed_boundary": "baseline deterministic cognition cues in actual manifest",
            "claim_limit": "no schema2 interpretation or model understanding verdict inferred",
        },
        "before": before,
        "after_foreground_before_post": selected,
        "after_post": after,
        "prepared_affect": [
            {
                "expression": json_value(item.expression),
                "materialized_pre_event": json_value(item.materialized_pre_event),
                "reason_code": item.reason_code,
                "appraisal_status": item.appraisal_status.value,
                "source_input_id": item.source_input_id,
                "source_receipt": source_input_receipt(runtime, item.source_input_id),
                "actual_input_root_message_ids": list(item.actual_input_root_message_ids),
                "actual_input_payload_refs": json_value(item.actual_input_payload_refs),
            }
            for item in prepared
        ],
        "affect_transition": json_value(
            tuple(item for item in transitions if item.interaction_id == reply.interaction_id)
        ),
        "relationship_transition": json_value(
            tuple(
                item for item in relation_transitions if item.interaction_id == reply.interaction_id
            )
        ),
        "foreground_attempts": runtime.foreground.calls[foreground_start:],
        "structured_attempts": [
            row
            for port, start in zip(runtime.structured, call_starts, strict=True)
            for row in port.calls[start:]
        ],
        "owner_decisions": [
            row
            for port, start in zip(runtime.owner_operations, owner_starts, strict=True)
            for row in port.decisions[start:]
        ],
        "post_response": json_value(post),
        "timings": json_value(reply.timings),
        "usage": json_value(reply.usage),
        "complete": post.succeeded
        and reply.context_manifest.emotion_appraisal_status not in {"unavailable", "rejected"},
        "behavioral_acceptance": "UNASSESSED",
    }
    return outcome


class BranchChoiceRequired(RuntimeError):
    """A semantic branch must be selected from the actual public reply by an operator."""


def resolve_turn_text(
    turn: Mapping[str, Any],
    *,
    choice: Mapping[str, Any] | None,
    prior_public_reply: str | None,
    actual_prior_repair_present: bool = False,
) -> tuple[str, str | None]:
    """Do not infer jokes/arguments with phrase matching or a model judge."""

    if "text" in turn:
        return cast(str, turn["text"]), None
    branches = cast(list[dict[str, Any]], turn["branches"])
    records = {item["record"]: item for item in branches}
    repair_records = {
        "actual_prior_repair_source_present_not_automatic_forgiveness",
        "no_absent_repair_presupposition",
    }
    if set(records) == repair_records:
        selected = (
            "actual_prior_repair_source_present_not_automatic_forgiveness"
            if actual_prior_repair_present
            else "no_absent_repair_presupposition"
        )
        return cast(str, records[selected]["text"]), selected
    if choice is None or prior_public_reply is None:
        raise BranchChoiceRequired("actual prior reply and explicit branch choice required")
    if set(choice) != {"record", "prior_public_reply_sha256", "operator_reviewed"}:
        raise BranchChoiceRequired("branch manifest fields do not match frozen contract")
    if (
        choice["record"] not in records
        or choice["operator_reviewed"] is not True
        or choice["prior_public_reply_sha256"] != digest(prior_public_reply)
    ):
        raise BranchChoiceRequired("branch choice is not bound to this actual public reply")
    record = cast(str, choice["record"])
    return cast(str, records[record]["text"]), record


@dataclass(slots=True)
class _FixtureForeground:
    count: int = 0

    async def generate(
        self, _request: ConversationProviderRequest, /
    ) -> ConversationProviderResponse:
        self.count += 1
        return ConversationProviderResponse(
            text=f"CONTROLLED-PROFILE-{self.count:03d}",
            provider="controlled-profile-fixture",
            model=FIXTURE_VERSION,
            finish_status="stop",
        )


class _SkipEpisode:
    async def generate_structured(self, _request: Any, /) -> EpisodeFormationProviderResponse:
        return EpisodeFormationProviderResponse(
            EpisodeFormationProposal(1, False, None, None, None, ()),
            "controlled-profile-fixture",
            FIXTURE_VERSION,
            FIXTURE_VERSION,
        )


@dataclass(slots=True)
class _FixtureRelationship:
    categories: tuple[str, ...]

    async def generate_structured(
        self, request: RelationshipAppraisalRequest, /
    ) -> RelationshipAppraisalResponse:
        return RelationshipAppraisalResponse(
            RelationshipAppraisalProposal(
                1, self.categories, 0.95, (request.interaction_id, request.user_message_id)
            ),
            "controlled-profile-fixture",
            FIXTURE_VERSION,
            FIXTURE_VERSION,
        )


async def condition_relationship_profile(
    runtime: CloseoutRuntime,
    *,
    profile: str,
) -> dict[str, Any]:
    """Create reviewed fixture roots via Talk and owner commits; no API or raw writes.

    ``strained`` extends an already prepared familiar fixture. Fresh is a no-op.
    This tests expression at a known projection, not model relationship formation.
    """

    if profile not in {"fresh", "earned-familiar", "relevant-strained"}:
        raise ValueError("unknown controlled relationship profile")
    before = runtime.snapshot()
    if profile == "fresh":
        if before["relationship"]["state"]["processed_interaction_count"] != 0:
            raise ValueError("fresh fixture already has relationship history")
        return {
            "kind": "CONTROLLED_PROFILE_FIXTURE",
            "profile": profile,
            "before": before,
            "after": before,
            "canonical_roots": [],
            "model_calls": 0,
        }
    state = before["relationship"]["state"]
    if profile == "earned-familiar" and state["processed_interaction_count"] != 0:
        raise ValueError("familiar conditioning requires a fresh relationship")
    if (
        profile == "relevant-strained"
        and before["relationship_projection"]["maturity"] != "established"
    ):
        raise ValueError("strain must extend an owner-established familiar fixture")
    categories = POSITIVE_CATEGORIES if profile == "earned-familiar" else NEGATIVE_CATEGORIES
    provider = _FixtureRelationship(categories)
    services = build_conversation_services(
        runtime.database,
        runtime.initial_self,
        _FixtureForeground(),
        _SkipEpisode(),
        runtime.settings,
        clock=runtime.clock,
        relationship_provider=provider,
        source_policy_required=True,
        behavior_policy=BEHAVIOR_POLICY_V35,
    )
    roots: list[dict[str, str]] = []
    sessions = FAMILIAR_SESSIONS if profile == "earned-familiar" else 1
    texts = POSITIVE_HISTORY if profile == "earned-familiar" else NEGATIVE_HISTORY
    for session_index in range(sessions):
        session = services.start_session.execute()
        for turn_index, text in enumerate(texts):
            request_id = f"fixture-{profile}-{session_index}-{turn_index}"
            reply = await services.talk.execute(
                TalkInput(text, request_id, request_id, session.session_id)
            )
            assert services.process_relationship is not None
            decision = await services.process_relationship.execute(
                reply.interaction_id, trace_id=request_id
            )
            if decision.decision_kind != "applied":
                raise RuntimeError("controlled owner proposal was not applied")
            stored = next(
                row
                for row in services.history.execute(session_id=session.session_id).interactions
                if row.interaction_id == reply.interaction_id
            )
            roots.append(
                {
                    "interaction_id": reply.interaction_id,
                    "user_message_id": stored.user_message.message_id,
                    "user_text_sha256": digest(text),
                }
            )
            runtime.clock.advance(60)
        services.close_session.execute(session.session_id)
        runtime.clock.advance(3600)
    after = runtime.snapshot()
    projection = after["relationship_projection"]
    if projection["maturity"] != "established":
        raise RuntimeError("controlled fixture did not earn established owner projection")
    if profile == "relevant-strained" and projection["recent_strain"] is not True:
        raise RuntimeError("controlled fixture did not produce actual recent strain")
    return {
        "kind": "CONTROLLED_PROFILE_FIXTURE",
        "version": FIXTURE_VERSION,
        "profile": profile,
        "before": before,
        "after": after,
        "canonical_roots": roots,
        "model_calls": 0,
        "claim_limit": "owner-approved fixture; not real provider relationship formation",
    }
