"""Stage 9 owner policy for evidence, kinds, correction, expiry and isolation."""

# ruff: noqa: RUF001  # Russian canonical evidence intentionally uses Cyrillic.

from datetime import UTC, datetime, timedelta

from satori.core.models import (
    ModelEpistemicKind,
    ModelEvidenceCitation,
    ModelSourceMessage,
    ModelValueKind,
    UserModelClaimProposal,
    WorldModelClaimProposal,
)
from satori.domain.models import (
    ModelClaimStatus,
    UserModelManager,
    WorldModelManager,
    model_claim_is_current,
)

NOW = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)


class Ids:
    def __init__(self) -> None:
        self.value = 0

    def new(self) -> str:
        self.value += 1
        return f"id-{self.value}"


def source(
    message: str, interaction: str, content: str, *, counterparty: str = "alice", day: int = 0
) -> ModelSourceMessage:
    return ModelSourceMessage(
        message, interaction, "satori", counterparty, NOW + timedelta(days=day), content
    )


def user_proposal(
    predicate: str,
    value: str,
    *messages: ModelSourceMessage,
    kind: ModelEpistemicKind = ModelEpistemicKind.EXPLICIT_FACT,
    corrects: str | None = None,
) -> UserModelClaimProposal:
    return UserModelClaimProposal(
        predicate,
        ModelValueKind.TEXT,
        value,
        kind,
        0.99,
        tuple(ModelEvidenceCitation(item.message_id, item.content) for item in messages),
        corrects,
    )


def world_proposal(
    label: str,
    status: str,
    *messages: ModelSourceMessage,
    kind: ModelEpistemicKind = ModelEpistemicKind.EXPLICIT_FACT,
    corrects: str | None = None,
) -> WorldModelClaimProposal:
    return WorldModelClaimProposal(
        "project",
        label,
        "status",
        ModelValueKind.TEXT,
        status,
        kind,
        0.99,
        tuple(ModelEvidenceCitation(item.message_id, item.content) for item in messages),
        corrects,
    )


def test_user_claim_is_typed_capped_and_counterparty_scoped() -> None:
    root = source("m1", "i1", "Меня зовут Алексей")
    ids = Ids()
    plan = UserModelManager().evaluate(
        (user_proposal("display_name", "Алексей", root),),
        identity_id="satori",
        counterparty_id="alice",
        current_message_id="m1",
        sources=(root,),
        existing_claims=(),
        max_claims=4,
        now=NOW,
        decision_id="d1",
        new_id=ids.new,
    )
    assert plan.created_count == 1
    claim = plan.claims[0]
    assert claim.epistemic_kind is ModelEpistemicKind.EXPLICIT_FACT
    assert claim.confidence == 0.90
    assert claim.counterparty_id == "alice"
    assert claim.expires_at is None
    assert claim.evidence[0].source_message_id == "m1"

    wrong_partition = UserModelManager().evaluate(
        (user_proposal("display_name", "Алексей", root),),
        identity_id="satori",
        counterparty_id="bob",
        current_message_id="m1",
        sources=(root,),
        existing_claims=(),
        max_claims=4,
        now=NOW,
        decision_id="d2",
        new_id=ids.new,
    )
    assert wrong_partition.rejected_count == 1
    assert wrong_partition.claims == ()


def test_inference_requires_two_independent_interactions_and_stays_inference() -> None:
    first = source("m1", "i1", "Я веду проект Аврора")
    second = source("m2", "i2", "Сегодня снова работаю над проектом Аврора", day=1)
    ids = Ids()
    rejected = UserModelManager().evaluate(
        (user_proposal("project", "Аврора", first, kind=ModelEpistemicKind.INFERENCE),),
        identity_id="satori",
        counterparty_id="alice",
        current_message_id="m1",
        sources=(first,),
        existing_claims=(),
        max_claims=4,
        now=NOW,
        decision_id="d1",
        new_id=ids.new,
    )
    assert rejected.rejected_count == 1
    accepted = UserModelManager().evaluate(
        (user_proposal("project", "Аврора", first, second, kind=ModelEpistemicKind.INFERENCE),),
        identity_id="satori",
        counterparty_id="alice",
        current_message_id="m2",
        sources=(first, second),
        existing_claims=(),
        max_claims=4,
        now=NOW + timedelta(days=1),
        decision_id="d2",
        new_id=ids.new,
    )
    assert accepted.claims[0].epistemic_kind is ModelEpistemicKind.INFERENCE
    assert accepted.claims[0].confidence == 0.65


def test_world_project_lifecycle_supersedes_without_erasing_history() -> None:
    planned = source("m1", "i1", "Проект Аврора запланирован")
    active = source("m2", "i2", "Проект Аврора теперь активен", day=5)
    completed = source("m3", "i3", "Проект Аврора завершён", day=20)
    manager = WorldModelManager()
    ids = Ids()
    first = manager.evaluate(
        (world_proposal("Аврора", "planned", planned),),
        identity_id="satori",
        counterparty_id="alice",
        current_message_id="m1",
        sources=(planned,),
        existing_claims=(),
        max_claims=4,
        now=NOW,
        decision_id="d1",
        new_id=ids.new,
    )
    planned_claim = first.claims[0]
    second = manager.evaluate(
        (world_proposal("Аврора", "active", active),),
        identity_id="satori",
        counterparty_id="alice",
        current_message_id="m2",
        sources=(active,),
        existing_claims=(planned_claim,),
        max_claims=4,
        now=NOW + timedelta(days=5),
        decision_id="d2",
        new_id=ids.new,
    )
    active_claim = next(item for item in second.claims if item.normalized_value == "active")
    old_planned = next(item for item in second.claims if item.normalized_value == "planned")
    assert old_planned.status is ModelClaimStatus.SUPERSEDED
    assert old_planned.superseded_by_claim_id == active_claim.claim_id
    assert old_planned.valid_until == active.observed_at

    third = manager.evaluate(
        (world_proposal("Аврора", "completed", completed),),
        identity_id="satori",
        counterparty_id="alice",
        current_message_id="m3",
        sources=(completed,),
        existing_claims=(old_planned, active_claim),
        max_claims=4,
        now=NOW + timedelta(days=20),
        decision_id="d3",
        new_id=ids.new,
    )
    current = next(item for item in third.claims if item.normalized_value == "completed")
    old_active = next(item for item in third.claims if item.normalized_value == "active")
    assert current.status is ModelClaimStatus.CURRENT
    assert old_active.status is ModelClaimStatus.SUPERSEDED
    assert current.expires_at == completed.observed_at + timedelta(days=365)


def test_expiry_is_pure_on_read_then_persisted_once_by_owner() -> None:
    root = source("m1", "i1", "Проект Аврора теперь активен")
    manager = WorldModelManager()
    ids = Ids()
    created = manager.evaluate(
        (world_proposal("Аврора", "active", root),),
        identity_id="satori",
        counterparty_id="alice",
        current_message_id="m1",
        sources=(root,),
        existing_claims=(),
        max_claims=4,
        now=NOW,
        decision_id="d1",
        new_id=ids.new,
    ).claims[0]
    assert created.expires_at is not None
    expires_at = created.expires_at
    assert model_claim_is_current(created, as_of=expires_at - timedelta(microseconds=1))
    assert not model_claim_is_current(created, as_of=expires_at)
    expired = manager.expire_due((created,), now=expires_at, decision_id="expiry", new_id=ids.new)
    assert expired.claims[0].status is ModelClaimStatus.EXPIRED
    assert expired.claims[0].valid_until == expires_at
    assert expired.revisions[0].reason_code == "freshness_window_elapsed"
    replay = manager.expire_due(
        expired.claims, now=expires_at, decision_id="expiry-2", new_id=ids.new
    )
    assert replay.claims == ()


def test_unknown_and_sensitive_predicates_are_rejected() -> None:
    root = source("m1", "i1", "У меня тревожное расстройство")
    plan = UserModelManager().evaluate(
        (user_proposal("mental_health", "тревожное расстройство", root),),
        identity_id="satori",
        counterparty_id="alice",
        current_message_id="m1",
        sources=(root,),
        existing_claims=(),
        max_claims=4,
        now=NOW,
        decision_id="d1",
        new_id=Ids().new,
    )
    assert plan.rejected_count == 1
    assert plan.claims == ()
