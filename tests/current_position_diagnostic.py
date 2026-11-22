"""Public synthetic current-position diagnosis; no sampled quality assertion.

The authored contributions below are controlled transport fixtures. They prove that
bounded current content can reach the real foreground request, not that a model has
formed a good position or that Satori has acquired a durable belief. There is no
provider entry point, phrase validator, state writer or score in this module.
"""

# ruff: noqa: RUF001  # Authored public Russian dialogue inputs.

from dataclasses import dataclass

CURRENT_POSITION_DIAGNOSTIC_VERSION = 1


@dataclass(frozen=True, slots=True)
class CurrentPositionDiagnosticTurn:
    """One authored turn and an independently supplied synthetic proposal datum."""

    name: str
    user_text: str
    synthetic_take: str


CURRENT_POSITION_DIAGNOSTIC: tuple[CurrentPositionDiagnosticTurn, ...] = (
    CurrentPositionDiagnosticTurn(
        "object_condition",
        "Разобрал старый настенный светильник: треснул крепёж, провод целый.",
        "Я бы отделила состояние крепления от состояния провода: пока это разные вопросы.",
    ),
    CurrentPositionDiagnosticTurn(
        "two_options",
        "Клей быстрее, а винтовая планка снимается без следов.",
        "Мне ближе съёмное крепление: возможность следующего ремонта здесь имеет вес.",
    ),
    CurrentPositionDiagnosticTurn(
        "legitimate_correction",
        "Мне не понравилось, что ты свела всё только к скорости. Я говорил и о ремонте.",
        "Уточнение меняет предмет сравнения: скорость не была единственным критерием.",
    ),
    CurrentPositionDiagnosticTurn(
        "neutral_continuation",
        "Ладно, вернёмся к креплению. Его пока ещё можно разобрать.",
        "Разборность сохраняет возможность проверить крепление до окончательной фиксации.",
    ),
    CurrentPositionDiagnosticTurn(
        "requested_summary",
        "Сделай краткий пересказ нашего разговора.",
        "Сейчас нужен запрошенный пересказ, а не дополнительное мнение о креплении.",
    ),
)


@dataclass(frozen=True, slots=True)
class CurrentPositionDiagnosticObservation:
    """Mechanical observations only; human quality remains explicitly unscored."""

    name: str
    status: str | None
    proposal_calls: int
    foreground_calls: int
    proposal_content_reached_request: bool
    agency_unchanged: bool
    cognition_unchanged: bool
    persistent_self_unchanged: bool
    human_score: None = None
