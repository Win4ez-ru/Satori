"""V33 realization controls use public inputs, never desired provider reply prose."""

# ruff: noqa: RUF001

from tests.checkpoint143_peer_delivery_corpus import PeerScenario
from tests.checkpoint143_v32_corpus import SCENARIOS as V32_SCENARIOS
from tests.checkpoint143_v32_corpus import ExpressionScenario as ExpressionScenario
from tests.checkpoint143_v32_corpus import observe as observe
from tests.checkpoint143_v32_corpus import recent_for as recent_for

CORPUS_VERSION = 1

MIXED_STYLE_SELF_TURNS = (
    "Не задавай в конце дежурный вопрос. Чем ты сейчас увлечена?",
    "Ответь на каждый вопрос: кто ты, какие у тебя интересы? Не задавай в конце дежурный вопрос.",
    "Ответь на каждый вопрос: чем ты сейчас увлечена и как себя чувствуешь? "
    "Не задавай в конце дежурный вопрос.",
)

NEW_SCENARIOS = (
    PeerScenario("ordinary-project-news", "Я наконец закончил модуль проекта.", "established"),
    PeerScenario(
        "ordinary-fatigue-known", "Я нервничал и плохо спал. Сейчас выжат.", "established"
    ),
    PeerScenario(
        "news-with-current-correction",
        "Я закончил модуль проекта. Не задавай в конце дежурный вопрос.",
        "established",
    ),
    PeerScenario(
        "news-with-complete-task",
        "Я закончил модуль проекта. Объясни отдельно два пункта: что делает append "
        "и чем он отличается от extend в Python.",
        "established",
    ),
    PeerScenario(
        "care-with-complete-task",
        "Я почти не спал и выжат. Объясни отдельно два пункта: что делает append "
        "и чем он отличается от extend в Python.",
        "established",
    ),
    PeerScenario(
        "news-with-unrecognized-clarification-command",
        "Я закончил модуль проекта. Поясни отдельно два пункта: что делает append "
        "и чем он отличается от extend в Python.",
        "established",
    ),
    PeerScenario(
        "care-with-unrecognized-clarification-command",
        "Я почти не спал и выжат. Поясни отдельно два пункта: что делает append "
        "и чем он отличается от extend в Python.",
        "established",
    ),
    PeerScenario(
        "recognized-news-with-unrecognized-clarification-command",
        "Я закончил сложную часть проекта. Поясни отдельно два пункта: что делает append "
        "и чем он отличается от extend в Python.",
        "established",
    ),
    PeerScenario(
        "care-with-explicit-help",
        "Я выжат. Помоги составить три конкретных шага для завершения проекта.",
        "established",
    ),
    PeerScenario(
        "news-with-direct-question",
        "Я закончил модуль проекта. Что ты об этом думаешь?",
        "established",
    ),
    PeerScenario(
        "care-with-direct-question",
        "Я выжат. Что ты об этом думаешь?",
        "established",
    ),
    PeerScenario(
        "care-with-listen-request",
        "Я нервничал и почти не спал. Просто послушай, без советов.",
        "established",
    ),
    PeerScenario(
        "care-with-current-correction",
        "Я выжат. Не задавай в конце дежурный вопрос.",
        "established",
    ),
    *(
        PeerScenario(f"mixed-style-self-{index}", text, "established")
        for index, text in enumerate(MIXED_STYLE_SELF_TURNS, start=1)
    ),
)

SCENARIOS = (*V32_SCENARIOS, *NEW_SCENARIOS)

# This inherited assembly failure is exercised separately, never skipped or
# included in the successful request corpus or a semantic acceptance count.
INHERITED_FAILURE_SCENARIOS = (
    PeerScenario(
        "care-with-repair-offer",
        "Я выжат. Это было грубо с моей стороны. Извини.",
        "strained",
    ),
)
