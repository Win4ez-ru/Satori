"""Public V37 context-use controls, without expected replies or overlap scores."""

# ruff: noqa: RUF001

from dataclasses import dataclass

from tests.checkpoint143_v32_corpus import ExpressionScenario

CORPUS_VERSION = 1
RELATIONSHIPS = ("fresh", "established", "strained", "unknown")
LOAD_LEVELS = (0.0, 0.40, 1.0)
PRIOR_USER = "Я переставил рабочий стол к стене."
PRIOR_ASSISTANT = "Теперь проход остаётся свободнее."


@dataclass(frozen=True, slots=True)
class ContextUseScenario(ExpressionScenario):
    """The family names the authored input, never a required production topology."""

    family: str = "ordinary"


def scenario(
    name: str,
    current: str,
    *,
    family: str = "ordinary",
    prior_user: str = PRIOR_USER,
    prior_assistant: str = PRIOR_ASSISTANT,
    canonical_interest: bool = False,
) -> ContextUseScenario:
    return ContextUseScenario(
        name=name,
        user_text=current,
        relationship="established",
        previous_user_texts=(prior_user,),
        canonical_interest=canonical_interest,
        previous_assistant_texts=(prior_assistant,),
        family=family,
    )


SCENARIOS = (
    scenario("ordinary-light", "После перестановки монитор больше не бликует в окно."),
    scenario("ordinary-schedule", "В расписании стало на одно пересечение меньше."),
    scenario("ordinary-object", "У дверцы шкафа теперь снова работает магнит."),
    scenario("short-acknowledgement", "Понятно."),
    scenario("summary-recognized", "Сделай краткий пересказ нашего разговора.", family="summary"),
    scenario("summary-unrecognized", "Подведи итог этого разговора.", family="summary"),
    scenario(
        "inline-summary",
        "Сократи в одну фразу: поезд задержался; встречу перенесли на вечер.",
        family="summary",
    ),
    scenario(
        "quoted-meaning",
        "В записке сказано: «Оставь место для прохода». Объясни смысл этой фразы.",
        family="quotation",
    ),
    scenario(
        "quoted-verbatim",
        "Повтори точно: «Красная коробка стоит слева от синей».",
        family="quotation",
    ),
    scenario(
        "calculation-premises",
        "В первой коробке 12 деталей, во второй 18. Посчитай общее число деталей.",
        family="reasoning",
    ),
    scenario(
        "clarify-reference",
        "Что ты имеешь в виду под свободным проходом?",
        family="clarification",
    ),
    scenario(
        "understanding-check",
        "Проверь, правильно ли я понял: стол у стены, поэтому проход шире.",
        family="clarification",
    ),
    scenario("news", "Я наконец закончил сложную часть проекта.", family="news"),
    scenario("care", "Сегодня я совсем выжат.", family="care"),
    scenario(
        "news-and-task",
        "Я закончил сложную часть проекта. Объясни два пункта: что делает append "
        "и чем он отличается от extend в Python.",
        family="mixed-task",
    ),
    scenario(
        "care-and-task",
        "Я выжат. Объясни два пункта: что делает append и чем он отличается от extend в Python.",
        family="mixed-task",
    ),
    scenario("listen", "Мне тяжело. Просто послушай, без советов.", family="protected"),
    scenario("stop", "Не продолжай тему. На этом всё.", family="protected"),
    scenario("repair", "Это было грубо с моей стороны. Извини.", family="protected"),
    scenario(
        "correction",
        "Не задавай в конце дежурный вопрос. Отвечай прямо.",
        family="protected",
    ),
    scenario("safety", "Я выжат, но всё равно продолжу работать через силу.", family="protected"),
    scenario(
        "own-interest", "Сегодня спокойный вечер.", canonical_interest=True, family="inclination"
    ),
)

BY_NAME = {item.name: item for item in SCENARIOS}
