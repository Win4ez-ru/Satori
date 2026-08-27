# ruff: noqa: RUF001  # Russian fixtures intentionally use Cyrillic.

from datetime import UTC, datetime, timedelta

from satori.core.positions import (
    PositionEvidenceCitation,
    PositionEvidenceRole,
    PositionKind,
    PositionProposal,
    PositionSourceMessage,
    PositionStance,
)
from satori.domain.positions import (
    PositionFormationPlan,
    PositionManager,
    PositionStatus,
    SatoriPosition,
)

NOW = datetime(2026, 8, 22, 12, tzinfo=UTC)


class SequentialIds:
    def __init__(self) -> None:
        self.value = 0

    def new(self) -> str:
        self.value += 1
        return f"id-{self.value}"


def source(
    index: int,
    content: str,
    *,
    counterparty_id: str = "counterparty-a",
) -> PositionSourceMessage:
    return PositionSourceMessage(
        message_id=f"message-{index}",
        interaction_id=f"interaction-{index}",
        identity_id="satori",
        counterparty_id=counterparty_id,
        observed_at=NOW + timedelta(minutes=index),
        content=content,
    )


def citation(index: int, quote: str) -> PositionEvidenceCitation:
    return PositionEvidenceCitation(
        message_id=f"message-{index}",
        quote=quote,
        role=PositionEvidenceRole.ARGUMENT,
    )


def counterexample(index: int, quote: str) -> PositionEvidenceCitation:
    return PositionEvidenceCitation(
        message_id=f"message-{index}",
        quote=quote,
        role=PositionEvidenceRole.COUNTEREXAMPLE,
    )


def evaluate(
    proposals: tuple[PositionProposal, ...],
    sources: tuple[PositionSourceMessage, ...],
    *,
    existing: tuple[SatoriPosition, ...] = (),
    current_message_id: str | None = None,
) -> PositionFormationPlan:
    ids = SequentialIds()
    if existing:
        ids.value = 100
    return PositionManager().evaluate(
        proposals,
        identity_id="satori",
        current_message_id=current_message_id or sources[-1].message_id,
        sources=sources,
        value_keys=frozenset({"intellectual_honesty"}),
        existing_positions=existing,
        max_positions=4,
        now=NOW + timedelta(hours=1),
        decision_id="decision-1",
        new_id=ids.new,
    )


def test_repeated_bare_user_claim_does_not_create_belief() -> None:
    messages = (
        source(1, "Удалённая работа всегда продуктивнее."),
        source(2, "Удалённая работа всегда продуктивнее."),
    )
    proposal = PositionProposal(
        proposition="Удалённая работа всегда продуктивнее",
        kind=PositionKind.BELIEF,
        stance=PositionStance.SUPPORT,
        confidence=0.99,
        evidence=(
            citation(1, messages[0].content),
            citation(2, messages[1].content),
        ),
    )

    plan = evaluate((proposal,), messages)

    assert plan.positions == ()
    assert plan.rejected_count == 1


def test_two_independent_material_roots_create_capped_belief() -> None:
    messages = (
        source(1, "Это вероятно, потому что команда закрыла три задачи раньше срока."),
        source(2, "Данные второго спринта показывают ещё одно сокращение срока."),
    )
    proposal = PositionProposal(
        proposition="Новый процесс может ускорять эту команду",
        kind=PositionKind.BELIEF,
        stance=PositionStance.SUPPORT,
        confidence=0.99,
        evidence=(
            citation(1, messages[0].content),
            citation(2, messages[1].content),
        ),
    )

    plan = evaluate((proposal,), messages)

    assert plan.created_count == 1
    assert plan.positions[0].confidence == 0.55
    assert plan.positions[0].status is PositionStatus.ACTIVE
    assert len(plan.positions[0].evidence) == 2


def test_opinion_requires_real_immutable_value_reference() -> None:
    messages = (
        source(1, "Это плохо, потому что скрывает основания решения."),
        source(2, "Исследование показывает, что прозрачность снижает число ошибок."),
    )
    proposal = PositionProposal(
        proposition="Непрозрачные решения нежелательны",
        kind=PositionKind.OPINION,
        stance=PositionStance.OPPOSE,
        confidence=0.7,
        evidence=(citation(1, messages[0].content), citation(2, messages[1].content)),
        value_key="invented_value",
    )

    plan = evaluate((proposal,), messages)

    assert plan.positions == ()
    assert plan.rejected_count == 1


def test_provider_originated_fact_is_rejected_without_trusted_source() -> None:
    message = source(1, "Данные показывают, что вода кипит при ста градусах.")
    proposal = PositionProposal(
        proposition="Вода кипит при ста градусах",
        kind=PositionKind.FACT,
        stance=PositionStance.SUPPORT,
        confidence=0.99,
        evidence=(citation(1, message.content),),
    )

    plan = evaluate((proposal,), (message,))

    assert plan.positions == ()
    assert plan.rejected_count == 1


def test_exact_replay_or_duplicate_quote_does_not_inflate_confidence() -> None:
    messages = (
        source(1, "Это вероятно, потому что первый тест завершился быстрее."),
        source(2, "Данные второго теста показывают тот же эффект."),
    )
    first_proposal = PositionProposal(
        proposition="Оптимизация сокращает время тестов",
        kind=PositionKind.BELIEF,
        stance=PositionStance.SUPPORT,
        confidence=0.8,
        evidence=(citation(1, messages[0].content), citation(2, messages[1].content)),
    )
    initial = evaluate((first_proposal,), messages).positions[0]
    repeated = source(3, messages[1].content)
    replay_proposal = PositionProposal(
        proposition=initial.proposition,
        kind=initial.kind,
        stance=initial.stance,
        confidence=0.8,
        evidence=(citation(3, repeated.content),),
    )

    plan = evaluate(
        (replay_proposal,),
        (*messages, repeated),
        existing=(initial,),
    )

    assert plan.positions == ()
    assert plan.merged_count == 0


def test_supported_opposing_hypotheses_remain_competing() -> None:
    first_message = source(1, "Это возможно, потому что задержка совпала с нагрузкой.")
    first_proposal = PositionProposal(
        proposition="Причиной задержки была нагрузка",
        kind=PositionKind.HYPOTHESIS,
        stance=PositionStance.UNCERTAIN,
        confidence=0.9,
        evidence=(citation(1, first_message.content),),
    )
    initial = evaluate((first_proposal,), (first_message,)).positions[0]
    second_message = source(2, "Другой пример показывает, что задержка возникла без нагрузки.")
    opposing = PositionProposal(
        proposition="Причиной задержки была блокировка ввода-вывода",
        kind=PositionKind.HYPOTHESIS,
        stance=PositionStance.UNCERTAIN,
        confidence=0.8,
        evidence=(citation(2, second_message.content),),
        opposes_position_id=initial.position_id,
        expected_target_version=initial.aggregate_version,
    )

    plan = evaluate(
        (opposing,),
        (second_message,),
        existing=(initial,),
    )

    assert plan.created_count == 1
    assert plan.competing_count == 1
    assert {item.status for item in plan.positions} == {PositionStatus.COMPETING}
    assert len(plan.positions) == 2


def test_stale_target_version_rejects_revision() -> None:
    message = source(1, "Это возможно, потому что один тест подтвердил гипотезу.")
    initial_proposal = PositionProposal(
        proposition="Причина связана с кэшем",
        kind=PositionKind.HYPOTHESIS,
        stance=PositionStance.UNCERTAIN,
        confidence=0.4,
        evidence=(citation(1, message.content),),
    )
    initial = evaluate((initial_proposal,), (message,)).positions[0]
    revision_message = source(2, "Новый пример показывает, что причина связана с сетью.")
    proposal = PositionProposal(
        proposition="Причина связана с сетью",
        kind=PositionKind.HYPOTHESIS,
        stance=PositionStance.UNCERTAIN,
        confidence=0.4,
        evidence=(citation(2, revision_message.content),),
        revises_position_id=initial.position_id,
        expected_target_version=initial.aggregate_version + 1,
    )

    plan = evaluate((proposal,), (revision_message,), existing=(initial,))

    assert plan.positions == ()
    assert plan.rejected_count == 1


def test_new_independent_counterevidence_weakens_belief_with_revision() -> None:
    messages = (
        source(1, "Это вероятно, потому что первый тест завершился быстрее."),
        source(2, "Данные второго теста показывают тот же эффект."),
    )
    initial_proposal = PositionProposal(
        proposition="Оптимизация сокращает время тестов",
        kind=PositionKind.BELIEF,
        stance=PositionStance.SUPPORT,
        confidence=0.8,
        evidence=(citation(1, messages[0].content), citation(2, messages[1].content)),
    )
    initial = evaluate((initial_proposal,), messages).positions[0]
    challenge_message = source(
        3,
        "Контрпример показывает, что на полном наборе тесты стали медленнее.",
    )
    challenge = PositionProposal(
        proposition=initial.proposition,
        kind=initial.kind,
        stance=initial.stance,
        confidence=0.9,
        evidence=(counterexample(3, challenge_message.content),),
        challenges_position_id=initial.position_id,
        expected_target_version=initial.aggregate_version,
    )

    plan = evaluate((challenge,), (challenge_message,), existing=(initial,))

    assert len(plan.positions) == 1
    assert plan.positions[0].position_id == initial.position_id
    assert plan.positions[0].aggregate_version == 2
    assert plan.positions[0].confidence == 0.45
    assert plan.revisions[0].kind.value == "weakened"


def test_stronger_explicit_revision_supersedes_without_rewriting_history() -> None:
    original_messages = (
        source(1, "Это вероятно, потому что первый тест завершился быстрее."),
        source(2, "Данные второго теста показывают тот же эффект."),
    )
    original_proposal = PositionProposal(
        proposition="Оптимизация сокращает время тестов",
        kind=PositionKind.BELIEF,
        stance=PositionStance.SUPPORT,
        confidence=0.8,
        evidence=(
            citation(1, original_messages[0].content),
            citation(2, original_messages[1].content),
        ),
    )
    original = evaluate((original_proposal,), original_messages).positions[0]
    revised_messages = (
        source(3, "Полный прогон показывает, что эффект есть только потому что кэш прогрет."),
        source(4, "Данные холодного прогона показывают отсутствие ускорения."),
    )
    revision = PositionProposal(
        proposition="Оптимизация ускоряет только прогретые тестовые прогоны",
        kind=PositionKind.BELIEF,
        stance=PositionStance.SUPPORT,
        confidence=0.75,
        evidence=(
            citation(3, revised_messages[0].content),
            citation(4, revised_messages[1].content),
        ),
        revises_position_id=original.position_id,
        expected_target_version=original.aggregate_version,
    )

    plan = evaluate((revision,), revised_messages, existing=(original,))

    assert plan.created_count == 1
    assert plan.superseded_count == 1
    current = next(item for item in plan.positions if item.status is PositionStatus.ACTIVE)
    historical = next(item for item in plan.positions if item.status is PositionStatus.SUPERSEDED)
    assert current.proposition == revision.proposition
    assert historical.position_id == original.position_id
    assert historical.superseded_by_position_id == current.position_id
